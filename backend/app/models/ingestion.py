"""Batch, raw evidence, accepted business rows and quarantine."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk, utcnow
from app.models.enums import (
    BatchStatus,
    QuarantineState,
    RawDisposition,
    TransactionStatus,
    sql_in,
)


class IngestionBatch(UUIDPk, CreatedAt, Base):
    __tablename__ = "ingestion_batches"
    __table_args__ = (
        CheckConstraint(f"status IN {sql_in(BatchStatus)}", name="status_valid"),
        CheckConstraint(
            "received_count >= 0 AND processed_count >= 0 AND accepted_count >= 0 "
            "AND rejected_count >= 0 AND skipped_count >= 0 AND warning_count >= 0",
            name="counts_nonnegative",
        ),
        CheckConstraint("expected_count IS NULL OR expected_count >= 0", name="expected_nonneg"),
        # Every processed row ends up in exactly one bucket.
        CheckConstraint(
            "accepted_count + rejected_count + skipped_count = processed_count",
            name="counts_reconcile",
        ),
        UniqueConstraint("source_id", "sequence_number", name="uq_batches_source_sequence"),
        Index("ix_ingestion_batches_source_started", "source_id", "started_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_systems.id"))
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("datasets.id"))
    # Client- or server-derived key making repeated ingestion requests return the same batch.
    request_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    sequence_number: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(20), default=BatchStatus.PENDING, index=True)
    input_format: Mapped[str] = mapped_column(String(10))
    file_name: Mapped[str] = mapped_column(Text)
    expected_count: Mapped[int | None]
    received_count: Mapped[int] = mapped_column(default=0)
    # Checkpoint: rows fully committed. A resumed run skips this many input rows.
    processed_count: Mapped[int] = mapped_column(default=0)
    accepted_count: Mapped[int] = mapped_column(default=0)
    rejected_count: Mapped[int] = mapped_column(default=0)
    skipped_count: Mapped[int] = mapped_column(default=0)
    warning_count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    checksum: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class RawEvent(UUIDPk, Base):
    __tablename__ = "raw_events"
    __table_args__ = (
        UniqueConstraint("batch_id", "event_id", "payload_hash", name="uq_raw_events_identity"),
        CheckConstraint(f"disposition IN {sql_in(RawDisposition)}", name="disposition_valid"),
        CheckConstraint("occurrences >= 1", name="occurrences_positive"),
        Index("ix_raw_events_batch_disposition", "batch_id", "disposition"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_batches.id"))
    row_number: Mapped[int]
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]]
    payload_hash: Mapped[str] = mapped_column(String(64))
    # Identical rows repeated inside one batch collapse into one raw row.
    occurrences: Mapped[int] = mapped_column(default=1)
    disposition: Mapped[str] = mapped_column(String(24), default=RawDisposition.PENDING)
    # Set by an approved SKIP_IDENTICAL_DUPLICATE recovery after re-checking the hash.
    duplicate_confirmed: Mapped[bool] = mapped_column(default=False)
    received_at: Mapped[datetime] = mapped_column(default=utcnow)


class AcceptedTransaction(UUIDPk, CreatedAt, Base):
    __tablename__ = "accepted_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(f"status IN {sql_in(TransactionStatus)}", name="status_valid"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        Index("ix_accepted_transactions_event_time", "event_time"),
    )

    event_id: Mapped[str] = mapped_column(String(100), unique=True)
    transaction_id: Mapped[str] = mapped_column(String(100), index=True)
    customer_id: Mapped[str] = mapped_column(String(100))
    merchant_id: Mapped[str] = mapped_column(String(100), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(12))
    event_time: Mapped[datetime]
    schema_version: Mapped[int]
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_systems.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_batches.id"), index=True)
    raw_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_events.id"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    warning_codes: Mapped[list[Any]] = mapped_column(default=list)
    recovery_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recovery_jobs.id"))


class QuarantineRecord(UUIDPk, CreatedAt, Base):
    __tablename__ = "quarantine_records"
    __table_args__ = (
        CheckConstraint(f"state IN {sql_in(QuarantineState)}", name="state_valid"),
        Index("ix_quarantine_batch_state", "batch_id", "state"),
        Index("ix_quarantine_error_codes", "error_codes", postgresql_using="gin"),
    )

    raw_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_events.id"), unique=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_batches.id"))
    event_id: Mapped[str] = mapped_column(String(100), index=True)
    error_codes: Mapped[list[Any]]
    error_details: Mapped[list[Any]] = mapped_column(default=list)
    raw_payload: Mapped[dict[str, Any]]
    state: Mapped[str] = mapped_column(String(20), default=QuarantineState.OPEN)
    repair_candidate: Mapped[dict[str, Any] | None]
    resolution_note: Mapped[str | None] = mapped_column(Text)
    recovery_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recovery_jobs.id"))
    resolved_at: Mapped[datetime | None]
