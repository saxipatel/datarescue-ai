"""Identity, source systems, datasets and pipeline run metadata."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk, utcnow
from app.models.enums import Role, RunState, sql_in


class User(UUIDPk, CreatedAt, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {sql_in(Role)}", name="role_valid"),)

    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(default=True)


class SourceSystem(UUIDPk, CreatedAt, Base):
    __tablename__ = "source_systems"
    __table_args__ = (
        CheckConstraint("contract_version > 0", name="contract_version_positive"),
        CheckConstraint("lateness_grace_minutes >= 0", name="grace_nonnegative"),
    )

    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    contract_version: Mapped[int] = mapped_column(default=1)
    enabled: Mapped[bool] = mapped_column(default=True)
    lateness_grace_minutes: Mapped[int] = mapped_column(default=15)
    freshness_max_age_minutes: Mapped[int] = mapped_column(default=24 * 60)
    # Synthetic-source parameters (base seed, batch size) used for deterministic redelivery.
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Dataset(UUIDPk, CreatedAt, Base):
    """A generated synthetic file on the shared data volume plus its ground-truth manifest."""

    __tablename__ = "datasets"

    scenario: Mapped[str] = mapped_column(String(40))
    seed: Mapped[int]
    record_count: Mapped[int]
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_systems.id"))
    # Relative to settings.data_dir; never returned by the API.
    file_name: Mapped[str] = mapped_column(Text)
    manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class PipelineRun(UUIDPk, CreatedAt, Base):
    """One execution of a background pipeline (ingest, diagnose, recover, freshness)."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint(f"state IN {sql_in(RunState)}", name="state_valid"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index("ix_pipeline_runs_name_created", "pipeline_name", "created_at"),
    )

    pipeline_name: Mapped[str] = mapped_column(String(40))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_batches.id"), index=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(String(20), default=RunState.QUEUED)
    attempts: Mapped[int] = mapped_column(default=0)
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class PipelineRunEvent(Base):
    """Structured log lines attached to a run; the diagnosis engine reads these as log excerpts."""

    __tablename__ = "pipeline_run_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
