"""Failure lab: inject a labeled fault scenario and score how the platform detected it."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.datagen.generator import ROW_SCENARIOS
from app.datagen.source import spec_for_sequence
from app.errors import ValidationFailed
from app.models import (
    AcceptedTransaction,
    Dataset,
    Incident,
    IngestionBatch,
    QuarantineRecord,
    RawEvent,
    SimulationRun,
    SourceSystem,
    User,
)
from app.models.enums import BatchStatus
from app.services.audit import Actor, record_audit
from app.services.freshness import check_freshness
from app.services.pipeline import create_batch, create_dataset

BATCH_SCENARIOS = {"missing_batch", "stale_pipeline", "volume_drop"}
SCENARIOS = sorted((set(ROW_SCENARIOS) - {"clean"}) | BATCH_SCENARIOS | {"clean"})
MAX_PRIOR_PAYLOADS = 2000


def next_sequence(session: Session, source_id: uuid.UUID) -> int:
    current = session.scalar(
        select(func.max(IngestionBatch.sequence_number)).where(
            IngestionBatch.source_id == source_id
        )
    )
    return (current or 0) + 1


def prior_payloads(session: Session, source_id: uuid.UUID) -> tuple[dict[str, Any], ...]:
    """Accepted raw payloads from the most recent successful batch (for redelivery faults)."""
    last = session.scalar(
        select(IngestionBatch.id)
        .where(
            IngestionBatch.source_id == source_id,
            IngestionBatch.status.in_([BatchStatus.COMPLETE, BatchStatus.PARTIAL]),
        )
        .order_by(IngestionBatch.finished_at.desc())
        .limit(1)
    )
    if last is None:
        return ()
    # Only payloads accepted unchanged. A row repaired by a recovery (e.g. currency case)
    # is stored in normalized form, so re-sending its raw payload is a conflict, not a copy.
    rows = session.scalars(
        select(RawEvent.payload)
        .join(
            AcceptedTransaction,
            (AcceptedTransaction.raw_event_id == RawEvent.id)
            & (AcceptedTransaction.payload_hash == RawEvent.payload_hash),
        )
        .where(RawEvent.batch_id == last)
        .order_by(RawEvent.row_number)
        .limit(MAX_PRIOR_PAYLOADS)
    ).all()
    return tuple(rows)


def start_simulation(
    session: Session,
    user: User,
    *,
    scenario: str,
    source: SourceSystem,
    records: int,
    corruption_pct: float,
    seed: int | None,
    run_label: str | None,
    correlation_id: str,
) -> tuple[SimulationRun, IngestionBatch | None, list[dict[str, Any]]]:
    """Create the faulty batch (not yet ingested) or run a source-level check.

    Returns (simulation, batch_to_ingest, freshness_results).
    """
    if scenario not in SCENARIOS:
        raise ValidationFailed("Unknown scenario", details={"allowed": SCENARIOS})
    seq = next_sequence(session, source.id)
    anchor = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    sim = SimulationRun(
        scenario=scenario,
        seed=seed if seed is not None else 0,
        record_count=records,
        corruption_pct=corruption_pct,
        run_label=run_label,
        source_id=source.id,
        created_by=user.id,
    )
    session.add(sim)
    session.flush()

    if scenario == "stale_pipeline":
        # Advance the clock past the freshness window instead of waiting for real time.
        as_of = datetime.now(UTC) + timedelta(minutes=source.freshness_max_age_minutes + 1)
        results = check_freshness(session, as_of=as_of, source_ids=[source.id], simulated=True)
        sim.labels = {"expected": {"freshness_incident": True}, "as_of": as_of.isoformat()}
        incident_ids = [r.get("incident_id") for r in results if r.get("incident_id")]
        sim.evaluation = {"incident_opened": bool(incident_ids), "incident_ids": incident_ids}
        _audit(session, user, sim)
        return sim, None, results

    labels: dict[str, Any] = {}
    row_scenario, pct, size = scenario, corruption_pct, records
    if scenario == "missing_batch":
        labels["missing_sequence"] = seq
        seq += 1  # sequence `seq` is "lost upstream"; deliver the next one
        row_scenario, pct = "clean", 0.0
    elif scenario == "volume_drop":
        size = max(10, round(records * (1 - corruption_pct / 100)))
        row_scenario, pct = "clean", 0.0
        labels["delivered_records"] = size

    spec = spec_for_sequence(
        source,
        seq,
        anchor,
        records=size,
        scenario=row_scenario,
        corruption_pct=pct,
        prior_payloads=prior_payloads(session, source.id) if "duplicate" in scenario else (),
    )
    if seed is not None:
        spec = replace(spec, seed=seed)
    sim.seed = spec.seed
    dataset = create_dataset(session, source, spec, created_by=user.id)
    labels["expected"] = dataset.manifest["expected"]
    batch, _ = create_batch(
        session,
        source=source,
        file_name=dataset.file_name,
        input_format="jsonl",
        correlation_id=correlation_id,
        request_key=f"simulation:{sim.id}",
        # Volume drop keeps the producer's manifest at the normal size.
        expected_count=records if scenario == "volume_drop" else size,
        sequence_number=seq,
        dataset_id=dataset.id,
        created_by=user.id,
        details={"generation": spec.as_params(), "simulation_id": str(sim.id)},
    )
    sim.dataset_id = dataset.id
    sim.batch_id = batch.id
    sim.labels = labels
    _audit(session, user, sim)
    return sim, batch, []


def _audit(session: Session, user: User, sim: SimulationRun) -> None:
    record_audit(
        session,
        Actor.from_user(user),
        "simulation.started",
        "simulation",
        sim.id,
        details={
            "scenario": sim.scenario,
            "seed": sim.seed,
            "corruption_pct": sim.corruption_pct,
            "batch_id": sim.batch_id,
        },
    )


def evaluate_simulation(session: Session, sim: SimulationRun) -> dict[str, Any]:
    """Compare what ingestion did with the generator's ground truth."""
    if sim.batch_id is None:
        return sim.evaluation or {}
    batch = session.get_one(IngestionBatch, sim.batch_id)
    expected = sim.labels.get("expected", {})
    actual = {
        "received": batch.received_count,
        "accepted": batch.accepted_count,
        "quarantined": batch.rejected_count,
        "skipped": batch.skipped_count,
        "late_warnings": batch.warning_count,
    }
    labeled = set()
    if sim.dataset_id:
        dataset = session.get_one(Dataset, sim.dataset_id)
        labeled = {
            lab["event_id"]
            for lab in dataset.manifest.get("labels", [])
            if lab["expected"] == "quarantined"
        }
    quarantined = set(
        session.scalars(
            select(QuarantineRecord.event_id).where(QuarantineRecord.batch_id == batch.id)
        )
    )
    incident = session.scalar(select(Incident).where(Incident.batch_id == batch.id))
    detection_seconds = None
    if incident and batch.started_at:
        detection_seconds = round((incident.detected_at - batch.started_at).total_seconds(), 3)
    evaluation = {
        "expected": {k: expected.get(k) for k in actual},
        "actual": actual,
        "counts_match": all(
            expected.get(k) == v for k, v in actual.items() if expected.get(k) is not None
        ),
        "quarantine_true_positives": len(quarantined & labeled),
        "quarantine_false_positives": len(quarantined - labeled),
        "quarantine_false_negatives": len(labeled - quarantined),
        "incident_opened": incident is not None,
        "incident_id": str(incident.id) if incident else None,
        "detection_latency_seconds": detection_seconds,
    }
    if sim.scenario == "missing_batch":
        evaluation["missing_sequence_detected"] = bool(
            incident and "completeness" in incident.failed_rules
        )
    sim.evaluation = evaluation
    return evaluation
