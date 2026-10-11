"""Ingestion correctness against generator ground truth, plus resume and idempotency."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.datagen.generator import ROW_SCENARIOS
from app.models import (
    AcceptedTransaction,
    Dataset,
    IngestionBatch,
    QuarantineRecord,
    RawEvent,
    SourceSystem,
)
from app.models.enums import BatchStatus
from app.services.pipeline import create_batch, run_batch
from tests.helpers import accepted_total, ingest

pytestmark = pytest.mark.integration


def _expected(db, batch):
    return db.get(Dataset, batch.dataset_id).manifest["expected"]


@pytest.mark.parametrize("scenario", sorted(set(ROW_SCENARIOS) - {"clean"}))
def test_scenario_outcome_matches_labels(db, scenario):
    ingest(db, 1, records=500)  # gives duplicate scenarios a prior batch to redeliver
    batch, incident = ingest(db, 2, scenario, 12, records=500, with_prior=True)
    expected = _expected(db, batch)

    assert batch.received_count == expected["received"] == 500
    assert batch.accepted_count == expected["accepted"]
    assert batch.rejected_count == expected["quarantined"]
    assert batch.skipped_count == expected["skipped"]
    assert batch.warning_count == expected["late_warnings"]
    # Every processed row is in exactly one bucket.
    assert batch.accepted_count + batch.rejected_count + batch.skipped_count == 500

    labeled = {
        lab["event_id"]
        for lab in db.get(Dataset, batch.dataset_id).manifest["labels"]
        if lab["expected"] == "quarantined"
    }
    quarantined = set(
        db.scalars(select(QuarantineRecord.event_id).where(QuarantineRecord.batch_id == batch.id))
    )
    assert quarantined == labeled  # no false positives, no misses

    if scenario in {"late_events", "out_of_order"}:
        assert incident is None  # late_event is low severity by default: recorded, no incident
    else:
        assert incident is not None


def test_schema_break_is_not_silently_coerced(db):
    """A05: v2 rows are quarantined with contract errors, never mapped onto v1 columns."""
    ingest(db, 1, records=200)
    batch, incident = ingest(db, 2, "schema_break", 30, records=200)
    assert incident is not None and "schema_contract" in incident.failed_rules
    rows = db.scalars(select(QuarantineRecord).where(QuarantineRecord.batch_id == batch.id)).all()
    assert len(rows) == 60
    for row in rows:
        assert "SCHEMA_VERSION_UNSUPPORTED" in row.error_codes
        assert "amount_minor" in row.raw_payload  # original payload preserved as received
    assert batch.details["schema_versions"] == {"1": 140, "2": 60}


def test_reingesting_same_request_is_idempotent(db):
    batch, _ = ingest(db, 1, records=300)
    again, incident = ingest(db, 1, records=300)
    assert again.id == batch.id and incident is None
    assert accepted_total(db) == 300


def test_resume_after_partial_failure_does_not_double_count(db, monkeypatch):
    """A crash mid-batch keeps committed chunks; the retry resumes from the checkpoint."""
    from app.services import ingestion

    real = ingestion.process_chunk
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated worker crash")
        return real(*args, **kwargs)

    monkeypatch.setattr(ingestion, "process_chunk", flaky)
    monkeypatch.setattr(get_settings(), "ingestion_chunk_size", 100)
    with pytest.raises(RuntimeError):
        ingest(db, 1, "duplicate_event_ids", 10, records=500)
    db.rollback()

    batch = db.scalars(select(IngestionBatch)).one()
    assert batch.processed_count == 200  # two chunks committed before the crash
    assert batch.status == BatchStatus.PROCESSING

    monkeypatch.setattr(ingestion, "process_chunk", real)
    run_batch(db, batch)
    db.refresh(batch)
    expected = _expected(db, batch)
    assert batch.processed_count == 500
    assert batch.accepted_count == expected["accepted"]
    assert batch.skipped_count == expected["skipped"]
    assert db.scalar(select(func.count()).select_from(AcceptedTransaction)) == expected["accepted"]
    occurrences = db.scalar(select(func.sum(RawEvent.occurrences)))
    assert occurrences == 500  # raw evidence accounts for every received row exactly once


def test_cross_batch_identical_redelivery_is_skipped(db):
    first, _ = ingest(db, 1, records=200)
    # Re-send the same file under a new request key: every row already exists.
    second, _ = create_batch(
        db,
        source=db.get(SourceSystem, first.source_id),
        file_name=first.file_name,
        input_format="jsonl",
        correlation_id="redeliver",
        request_key="redeliver-same-file",
    )
    db.commit()
    incident = run_batch(db, second)
    db.refresh(second)
    assert (second.accepted_count, second.skipped_count) == (0, 200)
    assert second.status == BatchStatus.COMPLETE
    assert incident is not None and incident.failed_rules == ["event_uniqueness"]
    assert accepted_total(db) == 200


def test_redelivered_events_do_not_advance_watermark(db):
    """Regression: overlapping batches. Duplicates of newer events from the previous batch
    must not make this batch's genuine rows look late."""
    src = db.scalars(select(SourceSystem).where(SourceSystem.name == "payments_core")).one()
    from app.datagen.source import spec_for_sequence
    from app.services.pipeline import ingest_generated
    from app.services.simulation import prior_payloads
    from tests.helpers import ANCHOR

    first = spec_for_sequence(src, 1, ANCHOR, records=500)
    ingest_generated(db, src, first, sequence_number=1, request_key="overlap:1")
    # Second batch covers almost the same window as the first.
    second = spec_for_sequence(
        src,
        2,
        ANCHOR + timedelta(minutes=5),
        records=500,
        scenario="duplicate_event_ids",
        corruption_pct=20,
        prior_payloads=prior_payloads(db, src.id),
    )
    batch, incident = ingest_generated(db, src, second, sequence_number=2, request_key="overlap:2")
    db.refresh(batch)
    expected = _expected(db, batch)
    assert batch.warning_count == expected["late_warnings"] == 0
    assert batch.skipped_count == expected["skipped"]
    assert incident is not None and incident.failed_rules == ["event_uniqueness"]
