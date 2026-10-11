"""Quality rules/results, incidents, lineage, recovery, audit and simulations."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk, utcnow
from app.models.enums import (
    IncidentState,
    ProposalStatus,
    RecoveryState,
    RepairAction,
    Severity,
    sql_in,
)


class QualityRule(UUIDPk, CreatedAt, Base):
    __tablename__ = "quality_rules"
    __table_args__ = (
        CheckConstraint(f"severity IN {sql_in(Severity)}", name="severity_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    rule_key: Mapped[str] = mapped_column(String(40), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(10))
    enabled: Mapped[bool] = mapped_column(default=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class QualityResult(UUIDPk, Base):
    __tablename__ = "quality_results"
    __table_args__ = (
        CheckConstraint("failed_count >= 0 AND checked_count >= 0", name="counts_nonnegative"),
        # One result per rule per batch; freshness checks have no batch and keep history.
        Index(
            "uq_quality_results_batch_rule",
            "batch_id",
            "rule_id",
            unique=True,
            postgresql_where="batch_id IS NOT NULL",
        ),
        Index("ix_quality_results_checked_at", "checked_at"),
    )

    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_batches.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_systems.id"))
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quality_rules.id"), index=True)
    rule_version: Mapped[int]
    severity: Mapped[str] = mapped_column(String(10))
    passed: Mapped[bool]
    failed_count: Mapped[int] = mapped_column(default=0)
    checked_count: Mapped[int] = mapped_column(default=0)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)


class Incident(UUIDPk, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(f"state IN {sql_in(IncidentState)}", name="state_valid"),
        CheckConstraint(f"severity IN {sql_in(Severity)}", name="severity_valid"),
        Index("ix_incidents_state_severity", "state", "severity"),
        Index("ix_incidents_detected_at", "detected_at"),
    )

    incident_key: Mapped[str] = mapped_column(String(160), unique=True)
    category: Mapped[str] = mapped_column(String(30))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_batches.id"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_systems.id"))
    origin_node_key: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(30), default=IncidentState.OPEN)
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text)
    failed_rules: Mapped[list[Any]] = mapped_column(default=list)
    detected_at: Mapped[datetime] = mapped_column(default=utcnow)
    acknowledged_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None] = mapped_column(Text)
    diagnosis: Mapped[dict[str, Any] | None]
    # Optimistic concurrency token for state transitions.
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    __mapper_args__ = {"version_id_col": version}


class IncidentEvidence(UUIDPk, CreatedAt, Base):
    __tablename__ = "incident_evidence"

    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]]


class LineageNode(UUIDPk, Base):
    __tablename__ = "lineage_nodes"

    node_key: Mapped[str] = mapped_column(String(80), unique=True)
    node_type: Mapped[str] = mapped_column(String(30))
    display_name: Mapped[str] = mapped_column(String(120))
    node_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)


class LineageEdge(UUIDPk, Base):
    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint(
            "upstream_node_id", "downstream_node_id", "relation_type", name="uq_lineage_edges"
        ),
        CheckConstraint("upstream_node_id <> downstream_node_id", name="no_self_loop"),
    )

    upstream_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lineage_nodes.id"), index=True)
    downstream_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lineage_nodes.id"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(30), default="feeds")


class RepairProposal(UUIDPk, CreatedAt, Base):
    __tablename__ = "repair_proposals"
    __table_args__ = (
        CheckConstraint(f"status IN {sql_in(ProposalStatus)}", name="status_valid"),
        CheckConstraint(f"action IN {sql_in(RepairAction)}", name="action_valid"),
        CheckConstraint(f"risk_level IN {sql_in(Severity)}", name="risk_valid"),
        CheckConstraint("target_count >= 0", name="target_count_nonneg"),
        # Separation of duties is enforced in the service; this blocks the obvious bypass.
        CheckConstraint(
            "NOT (risk_level IN ('high','critical') AND reviewed_by = created_by)",
            name="no_self_approval_high_risk",
        ),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[dict[str, Any]]
    target_count: Mapped[int]
    proposed_patch: Mapped[dict[str, Any]] = mapped_column(default=dict)
    justification: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12), default=ProposalStatus.PENDING, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None]
    review_note: Mapped[str | None] = mapped_column(Text)
    dry_run_summary: Mapped[dict[str, Any] | None]
    dry_run_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}


class RecoveryJob(UUIDPk, CreatedAt, Base):
    __tablename__ = "recovery_jobs"
    __table_args__ = (
        CheckConstraint(f"state IN {sql_in(RecoveryState)}", name="state_valid"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repair_proposals.id"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    state: Mapped[str] = mapped_column(String(12), default=RecoveryState.QUEUED)
    target_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_batches.id"))
    dry_run_summary: Mapped[dict[str, Any] | None]
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    verification: Mapped[dict[str, Any] | None]
    attempts: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None]
    heartbeat_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class AuditEvent(UUIDPk, Base):
    """Append-only at the application layer; a trigger also rejects UPDATE/DELETE."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_occurred_at", "occurred_at"),
    )

    actor_id: Mapped[str] = mapped_column(String(80))
    actor_role: Mapped[str | None] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)


class SimulationRun(UUIDPk, CreatedAt, Base):
    """A fault injection with ground-truth labels, used to score detection."""

    __tablename__ = "simulation_runs"
    __table_args__ = (
        CheckConstraint(
            "corruption_pct >= 0 AND corruption_pct <= 100", name="corruption_pct_range"
        ),
    )

    scenario: Mapped[str] = mapped_column(String(40))
    seed: Mapped[int]
    record_count: Mapped[int]
    corruption_pct: Mapped[float]
    run_label: Mapped[str | None] = mapped_column(String(80))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_systems.id"))
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("datasets.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_batches.id"))
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    labels: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evaluation: Mapped[dict[str, Any] | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
