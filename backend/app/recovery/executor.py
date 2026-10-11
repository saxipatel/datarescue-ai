"""Recovery execution and verification.

Safety properties:
  * one executor per job: a session-level Postgres advisory lock is held for the run and
    released automatically if the worker dies, so a retry can take over safely;
  * idempotent per record: every write is conditional (state = 'OPEN', NOT confirmed,
    ON CONFLICT DO NOTHING), so re-running a partially applied job never duplicates;
  * chunked commits with a heartbeat, so progress survives a crash;
  * the incident is RESOLVED only when verification passes and nothing is outstanding.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.errors import ConflictError, NotFoundError
from app.logging_config import correlation_id_var
from app.models import (
    AcceptedTransaction,
    Incident,
    IngestionBatch,
    QuarantineRecord,
    RawEvent,
    RecoveryJob,
    RepairProposal,
    SourceSystem,
    User,
)
from app.models.base import has_any, has_key
from app.models.enums import (
    IncidentState,
    ProposalStatus,
    QuarantineState,
    RawDisposition,
    RecoveryState,
    RepairAction,
)
from app.quality.contract import codes_for_rule
from app.quality.validator import normalize_currency_case
from app.recovery import targets
from app.recovery.proposals import ensure_executable
from app.services.audit import Actor, record_audit
from app.services.incidents import transition
from app.services.ingestion import load_ruleset, resume_watermark
from app.services.quality import accepted_checksum, sequence_gaps, unresolved_duplicates

log = logging.getLogger(__name__)
CHUNK = 500
A = RepairAction


class SimulatedCrash(BaseException):
    """Raised by the chaos hook. BaseException so ordinary handlers don't swallow it,
    which leaves the job exactly as a killed worker would: RUNNING, partially applied."""


def start_recovery(
    session: Session,
    user: User,
    proposal_id: uuid.UUID,
    idempotency_key: str,
) -> tuple[RecoveryJob, str]:
    """Create or reuse a job. Returns (job, outcome) with outcome in
    {created, retry, in_progress, completed}. Only created/retry need enqueueing."""
    existing = session.scalar(
        select(RecoveryJob).where(RecoveryJob.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.proposal_id != proposal_id:
            raise ConflictError(
                "Idempotency key already used for a different proposal",
                code="idempotency_key_reused",
                details={"job_id": str(existing.id)},
            )
        if existing.state == RecoveryState.SUCCEEDED:
            return existing, "completed"
        if existing.state in (RecoveryState.QUEUED,):
            return existing, "in_progress"
        if existing.state in (RecoveryState.RUNNING, RecoveryState.VERIFYING) and _lock_held(
            existing.id
        ):
            return existing, "in_progress"
        # FAILED, or RUNNING with no live executor (crashed worker): resume.
        record_audit(
            session,
            Actor.from_user(user),
            "recovery.retry_requested",
            "recovery_job",
            existing.id,
            details={"previous_state": existing.state, "attempts": existing.attempts},
        )
        existing.state = RecoveryState.QUEUED
        existing.error_message = None
        session.flush()
        return existing, "retry"

    proposal = session.get(RepairProposal, proposal_id)
    if proposal is None:
        raise NotFoundError("Proposal not found")
    incident = session.get_one(Incident, proposal.incident_id)
    ensure_executable(proposal, incident)
    summary = targets.dry_run(session, RepairAction(proposal.action), proposal.target)
    job = RecoveryJob(
        incident_id=incident.id,
        proposal_id=proposal.id,
        idempotency_key=idempotency_key,
        state=RecoveryState.QUEUED,
        target_batch_id=uuid.UUID(proposal.target["batch_id"]),
        dry_run_summary=summary,
        correlation_id=correlation_id_var.get() or uuid.uuid4().hex,
        created_by=user.id,
    )
    session.add(job)
    session.flush()
    record_audit(
        session,
        Actor.from_user(user),
        "recovery.requested",
        "recovery_job",
        job.id,
        after={"state": job.state},
        details={
            "proposal_id": proposal.id,
            "idempotency_key": idempotency_key,
            "dry_run": summary,
        },
    )
    return job, "created"


def _lock_key(job_id: uuid.UUID) -> int:
    return int.from_bytes(job_id.bytes[:8], "big", signed=True)


def _lock_held(job_id: uuid.UUID) -> bool:
    with get_engine().connect() as conn:
        # Transaction-level probe: released automatically when this block ends.
        got = conn.scalar(select(func.pg_try_advisory_xact_lock(_lock_key(job_id))))
        conn.rollback()
        return not got


def run_recovery(job_id: uuid.UUID, *, crash_after: int | None = None) -> RecoveryState:
    """Worker entry point. Safe to call any number of times for the same job."""
    lock_conn = get_engine().connect()
    try:
        if not lock_conn.scalar(select(func.pg_try_advisory_lock(_lock_key(job_id)))):
            log.info("recovery already running elsewhere", extra={"job_id": str(job_id)})
            return RecoveryState.RUNNING
        lock_conn.commit()
        session = get_sessionmaker()()
        try:
            return _execute(session, job_id, crash_after)
        finally:
            session.close()
    finally:
        # Invalidate rather than return to the pool: ending the server session is what
        # releases a session-level advisory lock (a pooled connection would keep it).
        lock_conn.invalidate()
        lock_conn.close()


def _execute(session: Session, job_id: uuid.UUID, crash_after: int | None) -> RecoveryState:
    job = session.get_one(RecoveryJob, job_id)
    if job.state == RecoveryState.SUCCEEDED:
        return RecoveryState.SUCCEEDED
    correlation_id_var.set(job.correlation_id)
    proposal = session.get_one(RepairProposal, job.proposal_id)
    incident = session.get_one(Incident, job.incident_id)
    actor = Actor.system("recovery-worker")
    batch = session.get_one(IngestionBatch, job.target_batch_id)

    if incident.state != IncidentState.RECOVERING:
        transition(
            session, incident, IncidentState.RECOVERING, actor, note=f"recovery job {job.id}"
        )
    job.state = RecoveryState.RUNNING
    job.attempts += 1
    job.started_at = job.started_at or datetime.now(UTC)
    job.heartbeat_at = datetime.now(UTC)
    if "before" not in job.metrics:
        job.metrics = {**job.metrics, "before": _batch_snapshot(session, batch)}
    record_audit(
        session,
        actor,
        "recovery.started",
        "recovery_job",
        job.id,
        details={"attempt": job.attempts, "action": proposal.action},
    )
    session.commit()

    try:
        effects = _apply(session, job, proposal, batch, crash_after)
        job.state = RecoveryState.VERIFYING
        session.commit()
        session.refresh(batch)
        verification = verify(session, job, proposal, batch, incident, effects)
    except SimulatedCrash:
        raise
    except Exception as exc:
        session.rollback()
        log.exception("recovery failed", extra={"job_id": str(job.id)})
        _fail(session, job, incident, f"{type(exc).__name__}: {exc}")
        return RecoveryState.FAILED

    job.metrics = {**job.metrics, "effects": effects, "after": _batch_snapshot(session, batch)}
    job.verification = verification
    job.completed_at = datetime.now(UTC)
    if not verification["passed"]:
        _fail(session, job, incident, "verification failed", verification=verification)
        return RecoveryState.FAILED

    job.state = RecoveryState.SUCCEEDED
    proposal.status = ProposalStatus.EXECUTED
    outstanding = verification["outstanding_total"]
    if outstanding == 0:
        transition(
            session,
            incident,
            IncidentState.RESOLVED,
            actor,
            note=f"Recovery {job.id} verified; no outstanding issues",
        )
    else:
        transition(
            session,
            incident,
            IncidentState.ACTION_REQUIRED,
            actor,
            note=f"Recovery {job.id} verified; {outstanding} issue(s) still outstanding",
        )
    record_audit(
        session,
        actor,
        "recovery.succeeded",
        "recovery_job",
        job.id,
        before=job.metrics["before"],
        after=job.metrics["after"],
        details={"effects": effects, "outstanding": verification["outstanding"]},
    )
    session.commit()
    return RecoveryState.SUCCEEDED


def _fail(
    session: Session,
    job: RecoveryJob,
    incident: Incident,
    message: str,
    verification: dict[str, Any] | None = None,
) -> None:
    job = session.get_one(RecoveryJob, job.id)
    incident = session.get_one(Incident, incident.id)
    job.state = RecoveryState.FAILED
    job.error_message = message[:2000]
    if verification is not None:
        job.verification = verification
    actor = Actor.system("recovery-worker")
    if incident.state == IncidentState.RECOVERING:
        transition(
            session,
            incident,
            IncidentState.ACTION_REQUIRED,
            actor,
            note=f"Recovery {job.id} failed: {message[:200]}",
        )
    record_audit(
        session,
        actor,
        "recovery.failed",
        "recovery_job",
        job.id,
        details={"error": message[:500], "attempt": job.attempts},
    )
    session.commit()


def _batch_snapshot(session: Session, batch: IngestionBatch) -> dict[str, Any]:
    session.refresh(batch)
    rows = session.scalar(
        select(func.count())
        .select_from(AcceptedTransaction)
        .where(AcceptedTransaction.batch_id == batch.id)
    )
    return {
        "accepted_rows": int(rows or 0),
        "accepted_count": batch.accepted_count,
        "rejected_count": batch.rejected_count,
        "skipped_count": batch.skipped_count,
        "checksum": accepted_checksum(session, batch.id),
        "open_quarantine": int(
            session.scalar(
                select(func.count())
                .select_from(QuarantineRecord)
                .where(
                    QuarantineRecord.batch_id == batch.id,
                    QuarantineRecord.state == QuarantineState.OPEN,
                )
            )
            or 0
        ),
        "unresolved_duplicates": unresolved_duplicates(session, batch.id),
    }


def _heartbeat(session: Session, job: RecoveryJob, progress: dict[str, Any]) -> None:
    job.heartbeat_at = datetime.now(UTC)
    job.metrics = {**job.metrics, "progress": dict(progress)}
    session.commit()


def _apply(
    session: Session,
    job: RecoveryJob,
    proposal: RepairProposal,
    batch: IngestionBatch,
    crash_after: int | None,
) -> dict[str, Any]:
    action = RepairAction(proposal.action)
    effects: dict[str, Any] = {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "still_quarantined": 0,
        "blocked": 0,
    }
    processed = 0

    def maybe_crash() -> None:
        if crash_after is not None and processed >= crash_after:
            raise SimulatedCrash(f"chaos: crash after {processed} records")

    if action == A.SKIP_IDENTICAL_DUPLICATE:
        ids = [uuid.UUID(i) for i in proposal.target["raw_event_ids"]]
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            # Re-check the hash at execution time; only verified rows are confirmed.
            confirmed = session.execute(
                text(
                    "UPDATE raw_events r SET duplicate_confirmed = true "
                    "WHERE r.id = ANY(:ids) AND NOT r.duplicate_confirmed AND ("
                    " r.disposition <> 'DUPLICATE_SKIPPED' OR EXISTS ("
                    "  SELECT 1 FROM accepted_transactions a WHERE a.event_id = r.event_id "
                    "  AND a.payload_hash = r.payload_hash)) RETURNING r.id"
                ),
                {"ids": chunk},
            ).all()
            effects["updated"] += len(confirmed)
            processed += len(chunk)
            _heartbeat(session, job, effects)
            maybe_crash()
        remaining = session.scalar(
            select(func.count())
            .select_from(RawEvent)
            .where(RawEvent.id.in_(ids), RawEvent.duplicate_confirmed.is_(False))
        )
        effects["blocked"] = int(remaining or 0)
        effects["skipped"] = len(ids) - effects["updated"] - effects["blocked"]
        return effects

    if action == A.MANUAL_REVIEW_REQUIRED:
        ids = [uuid.UUID(i) for i in proposal.target["quarantine_ids"]]
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            result = session.execute(
                update(QuarantineRecord)
                .where(
                    QuarantineRecord.id.in_(chunk), QuarantineRecord.state == QuarantineState.OPEN
                )
                .values(
                    state=QuarantineState.MANUAL_REVIEW,
                    recovery_job_id=job.id,
                    resolution_note="Flagged for manual review by approved proposal",
                )
            )
            effects["updated"] += result.rowcount  # type: ignore[attr-defined]
            processed += len(chunk)
            _heartbeat(session, job, effects)
            maybe_crash()
        effects["skipped"] = len(ids) - effects["updated"]
        return effects

    if action in (A.REPLAY_QUARANTINED, A.NORMALIZE_CURRENCY_CASE):
        source = session.get_one(SourceSystem, batch.source_id)
        rules = load_ruleset(session, source)
        watermark = resume_watermark(session, batch.id)
        ids = [uuid.UUID(i) for i in proposal.target["quarantine_ids"]]
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start : start + CHUNK]
            records = session.scalars(
                select(QuarantineRecord)
                .where(QuarantineRecord.id.in_(chunk))
                .order_by(QuarantineRecord.created_at)
                .with_for_update()
            ).all()
            for q in records:
                if q.state != QuarantineState.OPEN:
                    effects["skipped"] += 1  # handled by an earlier attempt
                    continue
                _replay_one(session, job, batch, source.id, q, action, rules, watermark, effects)
            session.commit()
            processed += len(chunk)
            _heartbeat(session, job, effects)
            maybe_crash()
        return effects

    if action == A.RETRY_MISSING_BATCH:
        from app.recovery.redelivery import redeliver  # avoids an import cycle

        for delivery in proposal.target["deliveries"]:
            redelivered = redeliver(session, job, delivery)
            effects["inserted"] += redelivered.accepted_count
            effects["still_quarantined"] += redelivered.rejected_count
            effects["skipped"] += redelivered.skipped_count
            effects.setdefault("batches", []).append(str(redelivered.id))
            processed += redelivered.processed_count
            _heartbeat(session, job, effects)
            maybe_crash()
        return effects

    raise ConflictError(f"Unsupported action {action}")


def _replay_one(
    session: Session,
    job: RecoveryJob,
    batch: IngestionBatch,
    source_id: uuid.UUID,
    q: QuarantineRecord,
    action: RepairAction,
    rules: Any,
    watermark: Any,
    effects: dict[str, Any],
) -> None:
    payload = q.raw_payload
    if action == A.NORMALIZE_CURRENCY_CASE:
        candidate = normalize_currency_case(payload, rules.allowed_currencies)
        if candidate is None:
            effects["still_quarantined"] += 1
            return
        payload = candidate
        q.repair_candidate = candidate
    outcome = targets.evaluate_replay(session, payload, q.event_id, rules, watermark)
    now = datetime.now(UTC)
    if outcome.kind == "insert":
        assert outcome.record is not None
        inserted = session.scalar(
            pg_insert(AcceptedTransaction)
            .values(
                id=uuid.uuid4(),
                event_id=q.event_id,
                **outcome.record,
                source_id=source_id,
                batch_id=batch.id,
                raw_event_id=q.raw_event_id,
                payload_hash=outcome.digest,
                warning_codes=outcome.warnings,
                recovery_job_id=job.id,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(AcceptedTransaction.id)
        )
        if inserted is None:  # raced with another writer; leave for the next attempt
            effects["blocked"] += 1
            return
        q.state = (
            QuarantineState.REPAIRED
            if action == A.NORMALIZE_CURRENCY_CASE
            else (QuarantineState.REPLAYED)
        )
        _move_counts(session, batch.id, accepted=1, rejected=-1)
        effects["inserted"] += 1
    elif outcome.kind == "already_present":
        q.state = QuarantineState.REPLAYED
        _move_counts(session, batch.id, skipped=1, rejected=-1)
        effects["skipped"] += 1
    else:
        q.resolution_note = f"Replay attempt {job.id}: {outcome.kind} {outcome.errors}"[:500]
        effects["still_quarantined"] += 1
        return
    q.resolved_at = now
    q.recovery_job_id = job.id
    session.execute(
        update(RawEvent)
        .where(RawEvent.id == q.raw_event_id)
        .values(
            disposition=RawDisposition.ACCEPTED
            if outcome.kind == "insert"
            else RawDisposition.DUPLICATE_SKIPPED
        )
    )


def _move_counts(
    session: Session, batch_id: uuid.UUID, *, accepted: int = 0, rejected: int = 0, skipped: int = 0
) -> None:
    # Moves rows between buckets; processed_count is unchanged so the CHECK still holds.
    session.execute(
        update(IngestionBatch)
        .where(IngestionBatch.id == batch_id)
        .values(
            accepted_count=IngestionBatch.accepted_count + accepted,
            rejected_count=IngestionBatch.rejected_count + rejected,
            skipped_count=IngestionBatch.skipped_count + skipped,
        )
    )


def outstanding_issues(session: Session, incident: Incident) -> dict[str, int]:
    """Per failed rule, how many problems remain unhandled for the incident's batch."""
    if incident.batch_id is None:
        return {}
    batch = session.get_one(IngestionBatch, incident.batch_id)
    out: dict[str, int] = {}
    for rule in incident.failed_rules:
        if rule == "event_uniqueness":
            conflicts = session.scalar(
                select(func.count())
                .select_from(QuarantineRecord)
                .where(
                    QuarantineRecord.batch_id == batch.id,
                    QuarantineRecord.state == QuarantineState.OPEN,
                    has_key(QuarantineRecord.error_codes, "DUPLICATE_CONFLICT"),
                )
            )
            out[rule] = unresolved_duplicates(session, batch.id) + int(conflicts or 0)
        elif rule == "completeness":
            out[rule] = len(sequence_gaps(session, batch))
        elif rule in ("volume_anomaly", "late_event"):
            out[rule] = 0  # informational: nothing to repair, reviewed via the incident
        else:
            out[rule] = int(
                session.scalar(
                    select(func.count())
                    .select_from(QuarantineRecord)
                    .where(
                        QuarantineRecord.batch_id == batch.id,
                        QuarantineRecord.state == QuarantineState.OPEN,
                        has_any(QuarantineRecord.error_codes, codes_for_rule(rule)),
                    )
                )
                or 0
            )
    return out


def verify(
    session: Session,
    job: RecoveryJob,
    proposal: RepairProposal,
    batch: IngestionBatch,
    incident: Incident,
    effects: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    before = job.metrics["before"]
    after = _batch_snapshot(session, batch)

    dupes = session.scalar(
        text(
            "SELECT count(*) FROM (SELECT event_id FROM accepted_transactions "
            "WHERE batch_id = :b GROUP BY event_id HAVING count(*) > 1) d"
        ),
        {"b": batch.id},
    )
    checks.append(
        {
            "name": "no_duplicate_event_ids",
            "passed": dupes == 0,
            "detail": f"{dupes} duplicated event_ids in accepted rows",
        }
    )

    reconciles = (
        batch.accepted_count + batch.rejected_count + batch.skipped_count == batch.processed_count
    ) and after["accepted_rows"] == batch.accepted_count
    checks.append(
        {
            "name": "counts_reconcile",
            "passed": reconciles,
            "detail": f"accepted {batch.accepted_count} (rows {after['accepted_rows']}) + rejected "
            f"{batch.rejected_count} + skipped {batch.skipped_count} = processed "
            f"{batch.processed_count}",
        }
    )

    action = RepairAction(proposal.action)
    if action in (A.REPLAY_QUARANTINED, A.NORMALIZE_CURRENCY_CASE):
        # Count rows written by this job across all attempts, not just the last one.
        written = int(
            session.scalar(
                select(func.count())
                .select_from(AcceptedTransaction)
                .where(AcceptedTransaction.recovery_job_id == job.id)
            )
            or 0
        )
        delta = after["accepted_rows"] - before["accepted_rows"]
        checks.append(
            {
                "name": "accepted_delta_matches_inserts",
                "passed": delta == written,
                "detail": f"accepted rows +{delta}, rows written by job {written}",
            }
        )
        expected = (job.dry_run_summary or {}).get("counts", {}).get("would_insert")
        checks.append(
            {
                "name": "matches_dry_run",
                "passed": expected == written,
                "detail": f"dry run predicted {expected}, written {written}",
            }
        )
    elif action in (A.SKIP_IDENTICAL_DUPLICATE, A.MANUAL_REVIEW_REQUIRED):
        checks.append(
            {
                "name": "business_data_unchanged",
                "passed": before["checksum"] == after["checksum"]
                and before["accepted_rows"] == after["accepted_rows"],
                "detail": "accepted_transactions checksum before == after",
            }
        )
        if action == A.SKIP_IDENTICAL_DUPLICATE:
            checks.append(
                {
                    "name": "all_duplicates_confirmed",
                    "passed": effects["blocked"] == 0,
                    "detail": f"{effects['blocked']} rows failed the hash re-check",
                }
            )
    elif action == A.RETRY_MISSING_BATCH:
        checks.append(
            {
                "name": "sequence_gap_closed",
                "passed": not any(
                    d["sequence_number"] in sequence_gaps(session, batch)
                    for d in proposal.target["deliveries"]
                ),
                "detail": f"remaining gaps: {sequence_gaps(session, batch)}",
            }
        )

    outstanding = outstanding_issues(session, incident)
    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "outstanding": outstanding,
        "outstanding_total": sum(outstanding.values()),
        "checksum_before": before["checksum"],
        "checksum_after": after["checksum"],
        "verified_at": datetime.now(UTC).isoformat(),
    }


def reap_stale_jobs(session: Session) -> int:
    """Mark RUNNING jobs whose executor is gone as FAILED so they can be retried."""
    timeout = get_settings().stale_job_timeout_seconds
    stale = session.scalars(
        select(RecoveryJob).where(
            RecoveryJob.state.in_([RecoveryState.RUNNING, RecoveryState.VERIFYING]),
            RecoveryJob.heartbeat_at < func.now() - text(f"interval '{int(timeout)} seconds'"),
        )
    ).all()
    count = 0
    for job in stale:
        if _lock_held(job.id):
            continue
        incident = session.get_one(Incident, job.incident_id)
        _fail(session, job, incident, "executor heartbeat lost (worker crashed?)")
        count += 1
    return count
