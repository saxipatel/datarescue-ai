"""Failure lab, dashboard metrics and the audit log."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, Paging, rate_limited, require
from app.errors import NotFoundError
from app.logging_config import correlation_id_var
from app.models import AuditEvent, SimulationRun, SourceSystem, User
from app.repositories.pagination import paginate
from app.schemas.common import Page
from app.schemas.resources import AuditOut, SimulationOut, SimulationRequest, SimulationStarted
from app.security.rbac import Permission
from app.services import metrics
from app.services.pipeline import create_run
from app.services.simulation import SCENARIOS, start_simulation
from app.workers.queue import enqueue
from app.workers.tasks import ingest_batch_task

router = APIRouter()


@router.get("/simulations/scenarios", tags=["simulations"])
def scenarios(_: CurrentUser) -> dict[str, list[str]]:
    return {"scenarios": SCENARIOS}


@router.post(
    "/simulations", response_model=SimulationStarted, status_code=202, tags=["simulations"]
)
def simulate(
    body: SimulationRequest,
    db: DB,
    user: Annotated[User, Depends(require(Permission.SIMULATE))],
    _: Annotated[User, Depends(rate_limited("simulate"))],
) -> SimulationStarted:
    source = db.scalar(select(SourceSystem).where(SourceSystem.name == body.source))
    if source is None:
        raise NotFoundError(f"Unknown source '{body.source}'")
    cid = correlation_id_var.get() or uuid.uuid4().hex
    sim, batch, freshness = start_simulation(
        db,
        user,
        scenario=body.scenario,
        source=source,
        records=body.records,
        corruption_pct=body.corruption_pct,
        seed=body.seed,
        run_label=body.run_label,
        correlation_id=cid,
    )
    run = None
    if batch is not None:
        run = create_run(
            db,
            "ingest",
            cid,
            batch_id=batch.id,
            created_by=user.id,
            parameters={"simulation_id": str(sim.id)},
        )
    db.commit()
    job_id = enqueue(ingest_batch_task, str(run.id)) if run else None
    db.refresh(sim)
    return SimulationStarted(
        simulation=SimulationOut.model_validate(sim),
        run_id=run.id if run else None,
        job_id=job_id,
        freshness_results=freshness,
    )


@router.get("/simulations", response_model=Page[SimulationOut], tags=["simulations"])
def list_simulations(db: DB, _: CurrentUser, paging: Paging) -> Page[SimulationOut]:
    rows, total = paginate(
        db,
        select(SimulationRun).order_by(SimulationRun.created_at.desc()),
        paging.limit,
        paging.offset,
    )
    return Page(
        items=[SimulationOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/simulations/{sim_id}", response_model=SimulationOut, tags=["simulations"])
def get_simulation(sim_id: uuid.UUID, db: DB, _: CurrentUser) -> SimulationRun:
    sim = db.get(SimulationRun, sim_id)
    if sim is None:
        raise NotFoundError("Simulation not found")
    return sim


@router.get("/metrics/overview", tags=["metrics"])
def overview(db: DB, _: CurrentUser) -> dict[str, Any]:
    return metrics.overview(db)


@router.get("/metrics/timeseries", tags=["metrics"])
def timeseries(
    db: DB,
    _: CurrentUser,
    bucket: Literal["hour", "day"] = "hour",
    hours: Annotated[int, Query(ge=1, le=24 * 90)] = 48,
) -> dict[str, Any]:
    return {"bucket": bucket, "hours": hours, "points": metrics.timeseries(db, bucket, hours)}


@router.get("/metrics/revenue", tags=["metrics"])
def revenue(db: DB, _: CurrentUser) -> dict[str, Any]:
    return metrics.revenue(db)


@router.get("/audit", response_model=Page[AuditOut], tags=["audit"])
def audit_log(
    db: DB,
    _: Annotated[User, Depends(require(Permission.READ_AUDIT))],
    paging: Paging,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    since: datetime | None = None,
) -> Page[AuditOut]:
    query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc())
    for column, value in (
        (AuditEvent.entity_type, entity_type),
        (AuditEvent.entity_id, entity_id),
        (AuditEvent.action, action),
        (AuditEvent.actor_id, actor_id),
    ):
        if value:
            query = query.where(column == value)
    if since:
        query = query.where(AuditEvent.occurred_at >= since)
    rows, total = paginate(db, query, paging.limit, paging.offset)
    return Page(
        items=[AuditOut.model_validate(r[0]) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )
