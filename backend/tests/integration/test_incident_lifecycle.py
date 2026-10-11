"""End-to-end lifecycle: ingest -> detect -> diagnose -> approve -> recover -> verify."""

import pytest
from sqlalchemy import func, select

from app.diagnosis.service import diagnose_incident
from app.models import AuditEvent, Incident, QuarantineRecord
from app.models.enums import (
    BatchStatus,
    IncidentState,
    QuarantineState,
    RecoveryState,
    RepairAction,
)
from app.recovery.executor import run_recovery, start_recovery
from app.services.audit import Actor
from tests.helpers import accepted_total, approved_proposal, ingest, user

pytestmark = pytest.mark.integration


def test_clean_batch_has_no_incident(db):
    batch, incident = ingest(db, 1)
    assert incident is None
    assert batch.status == BatchStatus.COMPLETE
    assert (batch.accepted_count, batch.rejected_count, batch.skipped_count) == (1000, 0, 0)
    assert accepted_total(db) == 1000
    assert batch.checksum


def test_duplicate_incident_full_lifecycle(db):
    for seq in (1, 2, 3):
        ingest(db, seq)
    before = accepted_total(db)

    batch, incident = ingest(db, 4, "duplicate_event_ids", 10, with_prior=True)
    assert incident is not None
    assert "event_uniqueness" in incident.failed_rules
    assert batch.skipped_count == 100  # exact duplicates are skipped, not quarantined
    assert batch.rejected_count == 0
    assert accepted_total(db) == before + 900

    diagnosis = diagnose_incident(db, incident.id, Actor.system("test"))
    db.commit()
    assert diagnosis.suspected_causes[0].cause_key == "non_idempotent_retry"
    assert diagnosis.ai_enrichment and diagnosis.ai_enrichment.status == "disabled"
    actions = [a.action for a in diagnosis.recommended_actions]
    assert RepairAction.SKIP_IDENTICAL_DUPLICATE in actions
    db.refresh(incident)
    assert incident.state == IncidentState.ACTION_REQUIRED

    proposal = approved_proposal(db, incident.id, RepairAction.SKIP_IDENTICAL_DUPLICATE)
    steward = user(db, "steward")
    job, outcome = start_recovery(db, steward, proposal.id, "dup-key-1")
    db.commit()
    assert outcome == "created"
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED

    db.expire_all()
    db.refresh(job)
    assert job.verification["passed"] is True
    assert job.verification["outstanding_total"] == 0
    assert db.get(Incident, incident.id).state == IncidentState.RESOLVED
    assert accepted_total(db) == before + 900  # business data untouched

    # Same idempotency key again: returns the finished job, writes nothing.
    again, outcome = start_recovery(db, steward, proposal.id, "dup-key-1")
    assert outcome == "completed" and again.id == job.id
    assert accepted_total(db) == before + 900

    actions_logged = set(db.scalars(select(AuditEvent.action)))
    assert {
        "incident.opened",
        "incident.diagnosed",
        "proposal.created",
        "proposal.approved",
        "recovery.requested",
        "recovery.started",
        "recovery.succeeded",
    } <= actions_logged


def test_currency_normalization_recovers_rows(db):
    ingest(db, 1)
    _, incident = ingest(db, 2, "malformed_currency", 10)
    assert incident is not None and "currency_allowlist" in incident.failed_rules
    diagnose_incident(db, incident.id, Actor.system("test"))
    db.commit()

    proposal = approved_proposal(db, incident.id, RepairAction.NORMALIZE_CURRENCY_CASE)
    expected = proposal.dry_run_summary["counts"]["would_insert"]
    assert expected == proposal.target_count > 0
    before = accepted_total(db)

    job, _ = start_recovery(db, user(db, "steward"), proposal.id, "cur-key")
    db.commit()
    assert run_recovery(job.id) == RecoveryState.SUCCEEDED
    db.expire_all()
    assert accepted_total(db) == before + expected
    repaired = db.scalar(
        select(func.count())
        .select_from(QuarantineRecord)
        .where(QuarantineRecord.state == QuarantineState.REPAIRED)
    )
    assert repaired == expected
    # Unrepairable codes (e.g. 'US$') remain, so the incident needs more work.
    assert db.get(Incident, incident.id).state == IncidentState.ACTION_REQUIRED
