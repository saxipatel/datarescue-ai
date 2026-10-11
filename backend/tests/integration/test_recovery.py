"""Recovery workflows, approval guards and failure injection."""

import uuid

import pytest
from sqlalchemy import func, select, text

from app.diagnosis.service import diagnose_incident
from app.errors import ConflictError, PermissionDenied, ValidationFailed
from app.models import (
    AcceptedTransaction,
    Dataset,
    Incident,
    IngestionBatch,
    QualityRule,
    QuarantineRecord,
    RecoveryJob,
)
from app.models.enums import (
    IncidentState,
    ProposalStatus,
    QuarantineState,
    RecoveryState,
    RepairAction,
)
from app.recovery.executor import SimulatedCrash, run_recovery, start_recovery
from app.recovery.proposals import create_proposal, review, run_dry_run
from app.services.audit import Actor
from tests.helpers import accepted_total, approved_proposal, ingest, user

pytestmark = pytest.mark.integration


def _diagnosed(db, seq, scenario, pct, records=1000, **kw):
    batch, incident = ingest(db, seq, scenario, pct, records=records, **kw)
    assert incident is not None
    diagnose_incident(db, incident.id, Actor.system("test"))
    db.commit()
    return batch, incident


def test_crash_mid_replay_then_retry_with_same_key(db, monkeypatch):
    """A11: worker dies after the first chunk; retrying the same key finishes without dupes."""
    monkeypatch.setattr("app.recovery.executor.CHUNK", 25)
    ingest(db, 1)
    # Make every malformed currency repairable so the replay touches many rows.
    _, incident = _diagnosed(db, 2, "malformed_currency", 10)
    proposal = approved_proposal(db, incident.id, RepairAction.NORMALIZE_CURRENCY_CASE)
    target = proposal.target_count
    assert target > 50
    steward = user(db, "steward")
    before = accepted_total(db)

    job, _ = start_recovery(db, steward, proposal.id, "chaos-key")
    db.commit()
    with pytest.raises(SimulatedCrash):
        run_recovery(job.id, crash_after=25)

    db.expire_all()
    job = db.get(RecoveryJob, job.id)
    assert job.state == RecoveryState.RUNNING  # looks exactly like a dead worker
    partial = accepted_total(db) - before
    assert 0 < partial < target

    retried, outcome = start_recovery(db, steward, proposal.id, "chaos-key")
    db.commit()
    assert outcome == "retry" and retried.id == job.id
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED

    db.expire_all()
    job = db.get(RecoveryJob, job.id)
    assert job.attempts == 2
    assert job.verification["passed"], job.verification
    assert accepted_total(db) == before + target
    dupes = db.execute(
        text(
            "SELECT count(*) FROM (SELECT event_id FROM accepted_transactions "
            "GROUP BY event_id HAVING count(*) > 1) d"
        )
    ).scalar()
    assert dupes == 0
    written = db.scalar(
        select(func.count())
        .select_from(AcceptedTransaction)
        .where(AcceptedTransaction.recovery_job_id == job.id)
    )
    assert written == target

    # A third call with the same key is a no-op.
    _, outcome = start_recovery(db, steward, proposal.id, "chaos-key")
    assert outcome == "completed"
    assert accepted_total(db) == before + target


def test_missing_batch_redelivery(db):
    ingest(db, 1, records=300)
    ingest(db, 2, records=300)
    # Sequence 3 never arrives; 4 does.
    _, incident = _diagnosed(db, 4, "clean", 0, records=300)
    assert incident.failed_rules == ["completeness"]
    causes = [c["cause_key"] for c in db.get(Incident, incident.id).diagnosis["suspected_causes"]]
    assert causes[0] == "missing_batch"

    proposal = approved_proposal(db, incident.id, RepairAction.RETRY_MISSING_BATCH)
    assert proposal.target["deliveries"][0]["sequence_number"] == 3
    job, _ = start_recovery(db, user(db, "steward"), proposal.id, "missing-3")
    db.commit()
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED

    db.expire_all()
    redelivered = db.scalars(
        select(IngestionBatch).where(IngestionBatch.sequence_number == 3)
    ).one()
    assert redelivered.accepted_count == 300
    assert db.get(Incident, incident.id).state == IncidentState.RESOLVED
    assert accepted_total(db) == 1200


def test_manual_review_flags_without_touching_business_data(db):
    ingest(db, 1, records=300)
    batch, incident = _diagnosed(db, 2, "invalid_amount", 10, records=300)
    before = accepted_total(db)
    proposal = approved_proposal(db, incident.id, RepairAction.MANUAL_REVIEW_REQUIRED)
    job, _ = start_recovery(db, user(db, "steward"), proposal.id, "manual-1")
    db.commit()
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED
    db.expire_all()
    assert accepted_total(db) == before
    states = set(
        db.scalars(select(QuarantineRecord.state).where(QuarantineRecord.batch_id == batch.id))
    )
    assert states == {QuarantineState.MANUAL_REVIEW}
    assert db.get(Incident, incident.id).state == IncidentState.RESOLVED


def test_replay_after_rule_change(db):
    """REPLAY_QUARANTINED re-validates under current rules; nothing passes until they change."""
    ingest(db, 1, records=300)
    _, incident = _diagnosed(db, 2, "unexpected_field", 10, records=300)

    rule = db.scalars(select(QualityRule).where(QualityRule.rule_key == "schema_contract")).one()
    rule.configuration = {**rule.configuration, "reject_unexpected_fields": False}
    rule.version += 1
    db.commit()

    proposal = approved_proposal(db, incident.id, RepairAction.REPLAY_QUARANTINED)
    assert proposal.dry_run_summary["counts"]["would_insert"] == 30
    job, _ = start_recovery(db, user(db, "steward"), proposal.id, "replay-1")
    db.commit()
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED
    db.expire_all()
    assert accepted_total(db) == 600
    assert db.get(Incident, incident.id).state == IncidentState.RESOLVED


def test_unapproved_proposal_cannot_run(db):
    """A08 at the service layer (API test covers the HTTP status)."""
    ingest(db, 1, records=200)
    _, incident = _diagnosed(db, 2, "invalid_amount", 10, records=200)
    engineer = user(db, "engineer")
    proposal = create_proposal(
        db,
        engineer,
        incident.id,
        RepairAction.MANUAL_REVIEW_REQUIRED,
        "Flag the negative amounts for review",
    )
    db.commit()
    with pytest.raises(PermissionDenied) as exc:
        start_recovery(db, engineer, proposal.id, "nope")
    assert exc.value.code == "approval_required"


def test_approval_rules(db):
    ingest(db, 1, records=200)
    _, incident = _diagnosed(db, 2, "invalid_amount", 10, records=200)
    engineer, steward = user(db, "engineer"), user(db, "steward")
    proposal = create_proposal(
        db,
        steward,
        incident.id,
        RepairAction.MANUAL_REVIEW_REQUIRED,
        "Flag the negative amounts for review",
    )
    with pytest.raises(PermissionDenied):
        review(db, engineer, proposal, approve=True, note=None)  # engineers can't approve
    with pytest.raises(ConflictError) as exc:
        review(db, steward, proposal, approve=True, note=None)
    assert exc.value.code == "dry_run_required"
    run_dry_run(db, steward, proposal)

    proposal.risk_level = "high"  # high risk: the proposer may not approve their own change
    with pytest.raises(PermissionDenied) as exc:
        review(db, steward, proposal, approve=True, note=None)
    assert exc.value.code == "separation_of_duties"
    review(db, user(db, "admin"), proposal, approve=True, note="second reviewer")
    assert proposal.status == ProposalStatus.APPROVED


def test_proposal_rejects_arbitrary_target_filters(db):
    ingest(db, 1, records=200)
    _, incident = _diagnosed(db, 2, "invalid_amount", 10, records=200)
    with pytest.raises(ValidationFailed):
        create_proposal(
            db,
            user(db, "engineer"),
            incident.id,
            RepairAction.REPLAY_QUARANTINED,
            "Try to run raw SQL",
            {"sql": "DELETE FROM accepted_transactions"},
        )


def test_idempotency_key_bound_to_one_proposal(db):
    ingest(db, 1, records=200)
    _, incident = _diagnosed(db, 2, "invalid_amount", 10, records=200)
    first = approved_proposal(db, incident.id, RepairAction.MANUAL_REVIEW_REQUIRED)
    start_recovery(db, user(db, "steward"), first.id, "shared-key")
    db.commit()
    with pytest.raises(ConflictError):
        start_recovery(db, user(db, "steward"), uuid.UUID(int=1), "shared-key")


def test_audit_table_is_append_only(db):
    ingest(db, 1, records=50)
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE audit_events SET action = 'tampered'"))
    db.rollback()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM audit_events"))
    db.rollback()


def test_second_executor_backs_off_while_lock_is_held(db):
    """Two workers picking up the same job: only the lock holder runs it."""
    from sqlalchemy import func as sa_func

    from app.db import get_engine
    from app.recovery.executor import _lock_key

    ingest(db, 1, records=200)
    _, incident = _diagnosed(db, 2, "invalid_amount", 10, records=200)
    proposal = approved_proposal(db, incident.id, RepairAction.MANUAL_REVIEW_REQUIRED)
    job, _ = start_recovery(db, user(db, "steward"), proposal.id, "concurrent-1")
    db.commit()

    holder = get_engine().connect()
    try:
        assert holder.scalar(select(sa_func.pg_try_advisory_lock(_lock_key(job.id))))
        assert run_recovery(job.id) == RecoveryState.RUNNING  # backed off
        db.expire_all()
        assert db.get(RecoveryJob, job.id).attempts == 0
        _, outcome = start_recovery(db, user(db, "steward"), proposal.id, "concurrent-1")
        assert outcome == "in_progress"
    finally:
        holder.invalidate()
        holder.close()
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED


def test_redelivery_scenario_after_repair_keeps_ground_truth(db):
    """Regression: rows repaired by NORMALIZE_CURRENCY_CASE are stored normalized, so the
    simulator must not treat their original raw payloads as identical redeliveries."""
    ingest(db, 1, records=300)
    _, incident = _diagnosed(db, 2, "malformed_currency", 20, records=300)
    proposal = approved_proposal(db, incident.id, RepairAction.NORMALIZE_CURRENCY_CASE)
    job, _ = start_recovery(db, user(db, "steward"), proposal.id, "repair-then-redeliver")
    db.commit()
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED
    db.expire_all()

    batch, _ = ingest(db, 3, "duplicate_event_ids", 10, records=300, with_prior=True)
    manifest = db.get(Dataset, batch.dataset_id).manifest["expected"]
    assert (batch.accepted_count, batch.rejected_count, batch.skipped_count) == (
        manifest["accepted"],
        manifest["quarantined"],
        manifest["skipped"],
    )
