"""Repair proposals, dry runs, approvals and recovery jobs."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, Paging, require
from app.api.incidents import timeline
from app.config import get_settings
from app.errors import NotFoundError, PermissionDenied
from app.models import RecoveryJob, RepairProposal, User
from app.models.enums import ProposalStatus, RecoveryState
from app.recovery import proposals as svc
from app.recovery.executor import start_recovery
from app.repositories.pagination import paginate
from app.schemas.common import Page
from app.schemas.resources import (
    DryRunRequest,
    ProposalCreate,
    ProposalOut,
    RecoveryDetail,
    RecoveryOut,
    RecoveryRequest,
    RecoveryStarted,
    ReviewRequest,
)
from app.security.rbac import Permission
from app.services.audit import Actor, record_audit
from app.workers.queue import enqueue
from app.workers.tasks import recovery_task

router = APIRouter()


def _proposal(db: DB, proposal_id: uuid.UUID) -> RepairProposal:
    proposal = db.get(RepairProposal, proposal_id)
    if proposal is None:
        raise NotFoundError("Proposal not found")
    return proposal


@router.post("/repairs/proposals", response_model=ProposalOut, status_code=201, tags=["repairs"])
def create_proposal(
    body: ProposalCreate,
    db: DB,
    user: Annotated[User, Depends(require(Permission.PROPOSE_REPAIR))],
) -> RepairProposal:
    proposal = svc.create_proposal(
        db, user, body.incident_id, body.action, body.justification, body.target
    )
    db.commit()
    return proposal


@router.get("/repairs/proposals", response_model=Page[ProposalOut], tags=["repairs"])
def list_proposals(
    db: DB,
    _: CurrentUser,
    paging: Paging,
    status_: Annotated[ProposalStatus | None, Query(alias="status")] = None,
    incident_id: uuid.UUID | None = None,
) -> Page[ProposalOut]:
    query = select(RepairProposal).order_by(RepairProposal.created_at.desc())
    if status_:
        query = query.where(RepairProposal.status == status_)
    if incident_id:
        query = query.where(RepairProposal.incident_id == incident_id)
    rows, total = paginate(db, query, paging.limit, paging.offset)
    return Page(
        items=[ProposalOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/repairs/proposals/{proposal_id}", response_model=ProposalOut, tags=["repairs"])
def get_proposal(proposal_id: uuid.UUID, db: DB, _: CurrentUser) -> RepairProposal:
    return _proposal(db, proposal_id)


@router.post(
    "/repairs/proposals/{proposal_id}/approve", response_model=ProposalOut, tags=["repairs"]
)
def approve(
    proposal_id: uuid.UUID,
    body: ReviewRequest,
    db: DB,
    user: Annotated[User, Depends(require(Permission.APPROVE_REPAIR))],
) -> RepairProposal:
    proposal = svc.review(
        db,
        user,
        _proposal(db, proposal_id),
        approve=True,
        note=body.note,
        expected_version=body.expected_version,
    )
    db.commit()
    return proposal


@router.post(
    "/repairs/proposals/{proposal_id}/reject", response_model=ProposalOut, tags=["repairs"]
)
def reject(
    proposal_id: uuid.UUID,
    body: ReviewRequest,
    db: DB,
    user: Annotated[User, Depends(require(Permission.APPROVE_REPAIR))],
) -> RepairProposal:
    proposal = svc.review(
        db,
        user,
        _proposal(db, proposal_id),
        approve=False,
        note=body.note,
        expected_version=body.expected_version,
    )
    db.commit()
    return proposal


@router.post("/recoveries/dry-run", tags=["recoveries"])
def dry_run(
    body: DryRunRequest,
    db: DB,
    user: Annotated[User, Depends(require(Permission.PROPOSE_REPAIR))],
) -> dict[str, Any]:
    proposal = _proposal(db, body.proposal_id)
    summary = svc.run_dry_run(db, user, proposal)
    db.commit()
    return {"proposal_id": str(proposal.id), "risk_level": proposal.risk_level, **summary}


@router.post("/recoveries", response_model=RecoveryStarted, status_code=202, tags=["recoveries"])
def start(
    body: RecoveryRequest,
    db: DB,
    response: Response,
    user: Annotated[User, Depends(require(Permission.EXECUTE_RECOVERY))],
) -> RecoveryStarted:
    """Run an approved proposal. The idempotency key makes retries safe:
    a finished job is returned as-is, a failed or abandoned one resumes."""
    crash_after = body.chaos_crash_after
    if crash_after is not None and not get_settings().chaos_enabled:
        raise PermissionDenied(
            "Failure injection is disabled (CHAOS_ENABLED=false)", code="chaos_disabled"
        )
    job, outcome = start_recovery(db, user, body.proposal_id, body.idempotency_key)
    if crash_after is not None:
        record_audit(
            db,
            Actor.from_user(user),
            "recovery.chaos_injected",
            "recovery_job",
            job.id,
            details={"crash_after": crash_after},
        )
    db.commit()
    queue_job = None
    if outcome in ("created", "retry"):
        # Crash injection applies to the first attempt only, so a retry can succeed.
        inject = crash_after if outcome == "created" else None
        queue_job = enqueue(recovery_task, str(job.id), inject, retry=False)
        db.refresh(job)
    else:
        response.status_code = status.HTTP_200_OK
    return RecoveryStarted(
        job=RecoveryOut.model_validate(job), outcome=outcome, queue_job_id=queue_job
    )


@router.get("/recoveries", response_model=Page[RecoveryOut], tags=["recoveries"])
def list_recoveries(
    db: DB,
    _: CurrentUser,
    paging: Paging,
    state: RecoveryState | None = None,
) -> Page[RecoveryOut]:
    query = select(RecoveryJob).order_by(RecoveryJob.created_at.desc())
    if state:
        query = query.where(RecoveryJob.state == state)
    rows, total = paginate(db, query, paging.limit, paging.offset)
    return Page(
        items=[RecoveryOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/recoveries/{job_id}", response_model=RecoveryDetail, tags=["recoveries"])
def get_recovery(job_id: uuid.UUID, db: DB, _: CurrentUser) -> RecoveryDetail:
    job = db.get(RecoveryJob, job_id)
    if job is None:
        raise NotFoundError("Recovery job not found")
    return RecoveryDetail(
        **RecoveryOut.model_validate(job).model_dump(),
        audit=timeline(db, "recovery_job", [str(job.id)]),
    )
