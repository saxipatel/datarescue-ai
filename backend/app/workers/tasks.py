"""Background task bodies. Each opens its own session and is safe to re-run."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import session_scope
from app.diagnosis.service import diagnose_incident
from app.logging_config import correlation_id_var
from app.models import Incident, IngestionBatch, PipelineRun, SimulationRun
from app.models.enums import IncidentState, RunState
from app.recovery.executor import reap_stale_jobs, run_recovery
from app.services.audit import Actor
from app.services.freshness import check_freshness
from app.services.pipeline import log_event, mark_batch_failed, run_batch
from app.services.simulation import evaluate_simulation
from app.workers.queue import enqueue

log = logging.getLogger(__name__)


def ingest_batch_task(run_id: str) -> None:
    incident_id = None
    with session_scope() as session:
        run = session.get_one(PipelineRun, uuid.UUID(run_id))
        correlation_id_var.set(run.correlation_id)
        if run.state == RunState.SUCCEEDED:
            return
        run.state = RunState.RUNNING
        run.attempts += 1
        run.started_at = datetime.now(UTC)
        run.error_message = None
        batch = session.get_one(IngestionBatch, run.batch_id)
        session.commit()
        try:
            incident = run_batch(session, batch, run)
            sim = session.scalar(select(SimulationRun).where(SimulationRun.batch_id == batch.id))
            if sim is not None:
                evaluate_simulation(session, sim)
            run.state = RunState.SUCCEEDED
            run.finished_at = datetime.now(UTC)
            session.commit()
            if incident is not None and incident.state == IncidentState.OPEN:
                incident_id = incident.id
        except Exception as exc:
            mark_batch_failed(session, batch.id, str(exc))
            run = session.get_one(PipelineRun, uuid.UUID(run_id))
            run.state = RunState.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(UTC)
            log_event(
                session, run, "ERROR", "batch failed", error=str(exc)[:500], attempt=run.attempts
            )
            session.commit()
            log.exception("ingestion failed", extra={"run_id": run_id})
            raise  # let RQ retry with backoff; the checkpoint makes the retry resume
    if incident_id is not None:
        enqueue(diagnose_task, str(incident_id), "system:ingestion")


def diagnose_task(incident_id: str, actor_id: str = "system:diagnosis") -> None:
    with session_scope() as session:
        diagnose_incident(session, uuid.UUID(incident_id), Actor(actor_id=actor_id, role="system"))


def recovery_task(job_id: str, crash_after: int | None = None) -> str:
    return run_recovery(uuid.UUID(job_id), crash_after=crash_after).value


def maintenance_task() -> dict[str, int]:
    """Periodic: freshness checks and stale recovery job reaping."""
    with session_scope() as session:
        results = check_freshness(session)
        reaped = reap_stale_jobs(session)
    opened = [r for r in results if r.get("incident_id")]
    for result in opened:
        with session_scope() as session:
            incident = session.get(Incident, uuid.UUID(result["incident_id"]))
            if incident is not None and incident.state == IncidentState.OPEN:
                diagnose_incident(session, incident.id, Actor.system("scheduler"))
    return {"sources_checked": len(results), "stale": len(opened), "jobs_reaped": reaped}
