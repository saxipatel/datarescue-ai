"""Incidents, lineage/impact and quarantine inspection."""

import csv
import io
import json
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, Paging, rate_limited, require
from app.diagnosis.redaction import redact_payload
from app.errors import NotFoundError
from app.lineage.service import graph, incident_impact
from app.models import (
    AuditEvent,
    Incident,
    IncidentEvidence,
    QuarantineRecord,
    RecoveryJob,
    RepairProposal,
    User,
)
from app.models.base import has_any
from app.models.enums import IncidentState, QuarantineState, Severity
from app.repositories.pagination import paginate
from app.schemas.common import Accepted, Page
from app.schemas.resources import (
    CloseRequest,
    EvidenceOut,
    IncidentDetail,
    IncidentOut,
    ProposalOut,
    QuarantineDetail,
    QuarantineOut,
    RecoveryOut,
    TimelineEntry,
    TransitionRequest,
)
from app.security.rbac import Permission
from app.services.audit import Actor
from app.services.incidents import transition
from app.workers.queue import enqueue
from app.workers.tasks import diagnose_task

router = APIRouter()


def _incident(db: Session, incident_id: uuid.UUID) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError("Incident not found")
    return incident


def timeline(db: Session, entity_type: str, entity_ids: list[str]) -> list[TimelineEntry]:
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id.in_(entity_ids))
        .order_by(AuditEvent.occurred_at)
    ).all()
    return [
        TimelineEntry(
            at=e.occurred_at,
            actor_id=e.actor_id,
            actor_role=e.actor_role,
            action=e.action,
            details=e.details,
        )
        for e in events
    ]


@router.get("/incidents", response_model=Page[IncidentOut], tags=["incidents"])
def list_incidents(
    db: DB,
    _: CurrentUser,
    paging: Paging,
    state: Annotated[list[IncidentState] | None, Query()] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    open_only: bool = False,
    batch_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[IncidentOut]:
    query = select(Incident).order_by(Incident.detected_at.desc())
    if state:
        query = query.where(Incident.state.in_(state))
    if open_only:
        query = query.where(
            Incident.state.not_in([IncidentState.RESOLVED, IncidentState.CLOSED_AS_FALSE_POSITIVE])
        )
    if severity:
        query = query.where(Incident.severity.in_(severity))
    if batch_id:
        query = query.where(Incident.batch_id == batch_id)
    if q:
        query = query.where(or_(Incident.title.ilike(f"%{q}%"), Incident.summary.ilike(f"%{q}%")))
    rows, total = paginate(db, query, paging.limit, paging.offset)
    return Page(
        items=[IncidentOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/incidents/{incident_id}", response_model=IncidentDetail, tags=["incidents"])
def get_incident(incident_id: uuid.UUID, db: DB, _: CurrentUser) -> IncidentDetail:
    incident = _incident(db, incident_id)
    evidence = db.scalars(
        select(IncidentEvidence)
        .where(IncidentEvidence.incident_id == incident.id)
        .order_by(IncidentEvidence.created_at)
    ).all()
    proposals = db.scalars(
        select(RepairProposal)
        .where(RepairProposal.incident_id == incident.id)
        .order_by(RepairProposal.created_at)
    ).all()
    jobs = db.scalars(
        select(RecoveryJob)
        .where(RecoveryJob.incident_id == incident.id)
        .order_by(RecoveryJob.created_at)
    ).all()
    entries = timeline(db, "incident", [str(incident.id)])
    entries += timeline(db, "repair_proposal", [str(p.id) for p in proposals])
    entries += timeline(db, "recovery_job", [str(j.id) for j in jobs])
    entries.sort(key=lambda e: e.at)
    return IncidentDetail(
        **IncidentOut.model_validate(incident).model_dump(),
        diagnosis=incident.diagnosis,
        resolution_note=incident.resolution_note,
        evidence=[EvidenceOut.model_validate(e) for e in evidence],
        timeline=entries,
        proposals=[ProposalOut.model_validate(p) for p in proposals],
        recoveries=[RecoveryOut.model_validate(j) for j in jobs],
    )


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentOut, tags=["incidents"])
def acknowledge(
    incident_id: uuid.UUID,
    body: TransitionRequest,
    db: DB,
    user: Annotated[User, Depends(require(Permission.TRIAGE))],
) -> Incident:
    incident = _incident(db, incident_id)
    transition(
        db,
        incident,
        IncidentState.ACKNOWLEDGED,
        Actor.from_user(user),
        note=body.note,
        expected_version=body.expected_version,
    )
    db.commit()
    return incident


@router.post("/incidents/{incident_id}/close", response_model=IncidentOut, tags=["incidents"])
def close_false_positive(
    incident_id: uuid.UUID,
    body: CloseRequest,
    db: DB,
    user: Annotated[User, Depends(require(Permission.TRIAGE))],
) -> Incident:
    incident = _incident(db, incident_id)
    transition(
        db,
        incident,
        IncidentState.CLOSED_AS_FALSE_POSITIVE,
        Actor.from_user(user),
        note=body.note,
        expected_version=body.expected_version,
    )
    db.commit()
    return incident


@router.post(
    "/incidents/{incident_id}/diagnose",
    response_model=Accepted,
    status_code=202,
    tags=["incidents"],
)
def diagnose(
    incident_id: uuid.UUID,
    db: DB,
    user: Annotated[User, Depends(require(Permission.TRIAGE))],
    _: Annotated[User, Depends(rate_limited("diagnose"))],
) -> Accepted:
    _incident(db, incident_id)
    job_id = enqueue(diagnose_task, str(incident_id), str(user.id))
    return Accepted(job_id=job_id, status_url=f"/api/v1/incidents/{incident_id}")


@router.get("/incidents/{incident_id}/impact", tags=["lineage"])
def impact(incident_id: uuid.UUID, db: DB, _: CurrentUser) -> dict[str, Any]:
    return incident_impact(db, _incident(db, incident_id))


@router.get("/lineage", tags=["lineage"])
def lineage(db: DB, _: CurrentUser) -> dict[str, Any]:
    return graph(db)


# ---- quarantine -----------------------------------------------------------


def _quarantine_query(
    batch_id: uuid.UUID | None, state: QuarantineState | None, code: list[str] | None
) -> Any:
    query = select(QuarantineRecord).order_by(QuarantineRecord.created_at.desc())
    if batch_id:
        query = query.where(QuarantineRecord.batch_id == batch_id)
    if state:
        query = query.where(QuarantineRecord.state == state)
    if code:
        query = query.where(has_any(QuarantineRecord.error_codes, code))
    return query


@router.get("/quarantine", response_model=Page[QuarantineOut], tags=["quarantine"])
def list_quarantine(
    db: DB,
    _: CurrentUser,
    paging: Paging,
    batch_id: uuid.UUID | None = None,
    state: QuarantineState | None = None,
    code: Annotated[list[str] | None, Query()] = None,
) -> Page[QuarantineOut]:
    rows, total = paginate(
        db, _quarantine_query(batch_id, state, code), paging.limit, paging.offset
    )
    return Page(
        items=[QuarantineOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    # Spreadsheet apps execute cells starting with these characters (CSV injection).
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


@router.get("/quarantine/export.csv", tags=["quarantine"])
def export_quarantine(
    db: DB,
    _: CurrentUser,
    batch_id: uuid.UUID | None = None,
    state: QuarantineState | None = None,
    code: Annotated[list[str] | None, Query()] = None,
) -> StreamingResponse:
    query = _quarantine_query(batch_id, state, code).limit(50_000)

    def rows() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["quarantine_id", "batch_id", "event_id", "state", "error_codes", "payload"]
        )
        yield buffer.getvalue()
        for record in db.scalars(query.execution_options(yield_per=1000)):
            buffer.seek(0)
            buffer.truncate()
            writer.writerow(
                [
                    _csv_safe(v)
                    for v in (
                        str(record.id),
                        str(record.batch_id),
                        record.event_id,
                        record.state,
                        ";".join(record.error_codes),
                        redact_payload(record.raw_payload),
                    )
                ]
            )
            yield buffer.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="quarantine.csv"'},
    )


@router.get("/quarantine/{record_id}", response_model=QuarantineDetail, tags=["quarantine"])
def get_quarantine(record_id: uuid.UUID, db: DB, _: CurrentUser) -> QuarantineDetail:
    record = db.get(QuarantineRecord, record_id)
    if record is None:
        raise NotFoundError("Quarantine record not found")
    out = QuarantineDetail.model_validate(record)
    out.raw_payload = redact_payload(record.raw_payload)
    return out
