"""Shared helpers for integration tests."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.datagen.source import spec_for_sequence
from app.models import AcceptedTransaction, IngestionBatch, SourceSystem, User
from app.models.enums import RepairAction
from app.recovery.proposals import create_proposal, review, run_dry_run
from app.services.pipeline import ingest_generated
from app.services.simulation import prior_payloads

ANCHOR = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def source(db: Session, name: str = "payments_core") -> SourceSystem:
    return db.scalars(select(SourceSystem).where(SourceSystem.name == name)).one()


def user(db: Session, role: str) -> User:
    return db.scalars(select(User).where(User.email == f"{role}@example.test")).one()


def ingest(
    db: Session,
    seq: int,
    scenario: str = "clean",
    pct: float = 0.0,
    records: int = 1000,
    *,
    with_prior: bool = False,
    src: SourceSystem | None = None,
) -> tuple[IngestionBatch, object]:
    src = src or source(db)
    spec = spec_for_sequence(
        src,
        seq,
        ANCHOR + timedelta(hours=seq),
        records=records,
        scenario=scenario,
        corruption_pct=pct,
        prior_payloads=prior_payloads(db, src.id) if with_prior else (),
    )
    batch, incident = ingest_generated(
        db, src, spec, sequence_number=seq, request_key=f"test:{src.name}:{seq}"
    )
    db.refresh(batch)
    return batch, incident


def accepted_total(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AcceptedTransaction)) or 0


def approved_proposal(db: Session, incident_id, action: RepairAction, **target):
    engineer, steward = user(db, "engineer"), user(db, "steward")
    proposal = create_proposal(
        db, engineer, incident_id, action, "Approved in test after reviewing evidence", target
    )
    run_dry_run(db, engineer, proposal)
    review(db, steward, proposal, approve=True, note="looks right")
    db.commit()
    return proposal
