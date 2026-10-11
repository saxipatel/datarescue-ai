"""Request/response models for the REST API."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import IncidentState, RepairAction, Severity
from app.schemas.common import ORMModel

# ---- auth -----------------------------------------------------------------


class LoginRequest(BaseModel):
    # Plain string: demo accounts use the reserved .test TLD, which EmailStr rejects.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_at: datetime
    user: UserOut


class MeResponse(UserOut):
    permissions: list[str]


# ---- sources / datasets ---------------------------------------------------


class SourceOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str
    contract_version: int
    enabled: bool
    lateness_grace_minutes: int
    freshness_max_age_minutes: int
    created_at: datetime


class SourceCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    description: str = Field(default="", max_length=500)
    contract_version: int = Field(default=1, ge=1, le=100)
    lateness_grace_minutes: int = Field(default=15, ge=0, le=24 * 60)
    freshness_max_age_minutes: int = Field(default=24 * 60, ge=5, le=30 * 24 * 60)


class DatasetGenerate(BaseModel):
    source: str
    scenario: str = "clean"
    records: int = Field(default=1000, ge=1, le=200_000)
    seed: int = Field(default=42, ge=0, le=2**31)
    corruption_pct: float = Field(default=0, ge=0, le=100)
    format: Literal["jsonl", "csv"] = "jsonl"


class DatasetOut(ORMModel):
    id: uuid.UUID
    scenario: str
    seed: int
    record_count: int
    source_id: uuid.UUID
    created_at: datetime
    expected: dict[str, Any]
    format: str


# ---- ingestion / batches --------------------------------------------------


class IngestionRequest(BaseModel):
    source: str
    dataset_id: uuid.UUID
    expected_count: int | None = Field(default=None, ge=0)
    sequence_number: int | None = Field(default=None, ge=1)
    request_key: str | None = Field(default=None, max_length=200)


class IngestionAccepted(BaseModel):
    batch_id: uuid.UUID
    run_id: uuid.UUID | None
    job_id: str | None
    duplicate_request: bool
    status_url: str


class BatchOut(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    source_name: str | None = None
    sequence_number: int | None
    status: str
    input_format: str
    expected_count: int | None
    received_count: int
    processed_count: int
    accepted_count: int
    rejected_count: int
    skipped_count: int
    warning_count: int
    started_at: datetime | None
    finished_at: datetime | None
    checksum: str | None
    correlation_id: str
    error_message: str | None
    created_at: datetime
    failed_rules: int = 0
    incident_id: uuid.UUID | None = None


class QualityResultOut(ORMModel):
    id: uuid.UUID
    batch_id: uuid.UUID | None
    source_id: uuid.UUID | None
    rule_id: uuid.UUID
    rule_key: str | None = None
    rule_version: int
    severity: str
    passed: bool
    failed_count: int
    checked_count: int
    evidence: dict[str, Any]
    checked_at: datetime


class RunEventOut(ORMModel):
    level: str
    message: str
    context: dict[str, Any]
    created_at: datetime


class RunOut(ORMModel):
    id: uuid.UUID
    pipeline_name: str
    state: str
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    correlation_id: str


class BatchDetail(BatchOut):
    details: dict[str, Any]
    quality_results: list[QualityResultOut]
    runs: list[RunOut]
    run_events: list[RunEventOut]


class RuleOut(ORMModel):
    id: uuid.UUID
    rule_key: str
    description: str
    severity: str
    enabled: bool
    configuration: dict[str, Any]
    version: int
    updated_at: datetime


class RuleUpdate(BaseModel):
    severity: Severity | None = None
    enabled: bool | None = None
    configuration: dict[str, Any] | None = None
    expected_version: int | None = None
    reason: str = Field(min_length=5, max_length=500)


# ---- quarantine -----------------------------------------------------------


class QuarantineOut(ORMModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    event_id: str
    error_codes: list[str]
    state: str
    created_at: datetime
    resolved_at: datetime | None


class QuarantineDetail(QuarantineOut):
    error_details: list[dict[str, Any]]
    raw_payload: dict[str, Any]
    repair_candidate: dict[str, Any] | None
    resolution_note: str | None
    raw_event_id: uuid.UUID


# ---- incidents ------------------------------------------------------------


class IncidentOut(ORMModel):
    id: uuid.UUID
    incident_key: str
    category: str
    batch_id: uuid.UUID | None
    source_id: uuid.UUID | None
    origin_node_key: str
    severity: str
    state: str
    title: str
    summary: str
    failed_rules: list[str]
    detected_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    version: int


class EvidenceOut(ORMModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class TimelineEntry(BaseModel):
    at: datetime
    actor_id: str
    actor_role: str | None
    action: str
    details: dict[str, Any]


class IncidentDetail(IncidentOut):
    diagnosis: dict[str, Any] | None
    resolution_note: str | None
    evidence: list[EvidenceOut]
    timeline: list[TimelineEntry]
    proposals: list["ProposalOut"]
    recoveries: list["RecoveryOut"]


class TransitionRequest(BaseModel):
    note: str = Field(min_length=3, max_length=1000)
    expected_version: int | None = None


class CloseRequest(TransitionRequest):
    state: Literal["CLOSED_AS_FALSE_POSITIVE"] = "CLOSED_AS_FALSE_POSITIVE"


# ---- repairs / recoveries -------------------------------------------------


class ProposalCreate(BaseModel):
    incident_id: uuid.UUID
    action: RepairAction
    justification: str = Field(min_length=10, max_length=2000)
    target: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in ("quarantine_ids", "error_codes", "sequence_numbers"):
            if key in value and (not isinstance(value[key], list) or len(value[key]) > 10_000):
                raise ValueError(f"{key} must be a list of at most 10000 items")
        return value


class ProposalOut(ORMModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    action: str
    target_count: int
    proposed_patch: dict[str, Any]
    justification: str
    risk_level: str
    status: str
    created_by: uuid.UUID
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    dry_run_summary: dict[str, Any] | None
    dry_run_at: datetime | None
    created_at: datetime
    version: int


class ReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = None


class DryRunRequest(BaseModel):
    proposal_id: uuid.UUID


class RecoveryRequest(BaseModel):
    proposal_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    # Failure injection for demos; honoured only when CHAOS_ENABLED=true.
    chaos_crash_after: int | None = Field(default=None, ge=1)


class RecoveryOut(ORMModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    proposal_id: uuid.UUID | None
    idempotency_key: str
    state: str
    target_batch_id: uuid.UUID | None
    dry_run_summary: dict[str, Any] | None
    metrics: dict[str, Any]
    verification: dict[str, Any] | None
    attempts: int
    error_message: str | None
    correlation_id: str
    started_at: datetime | None
    heartbeat_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class RecoveryDetail(RecoveryOut):
    audit: list[TimelineEntry]


class RecoveryStarted(BaseModel):
    job: RecoveryOut
    outcome: Literal["created", "retry", "in_progress", "completed"]
    queue_job_id: str | None


# ---- simulations / metrics / audit ----------------------------------------


class SimulationRequest(BaseModel):
    scenario: str
    source: str = "payments_core"
    records: int = Field(default=1000, ge=10, le=100_000)
    corruption_pct: float = Field(default=10, ge=0, le=100)
    seed: int | None = Field(default=None, ge=0, le=2**31)
    run_label: str | None = Field(default=None, max_length=80)


class SimulationOut(ORMModel):
    id: uuid.UUID
    scenario: str
    seed: int
    record_count: int
    corruption_pct: float
    run_label: str | None
    batch_id: uuid.UUID | None
    labels: dict[str, Any]
    evaluation: dict[str, Any] | None
    created_at: datetime


class SimulationStarted(BaseModel):
    simulation: SimulationOut
    run_id: uuid.UUID | None
    job_id: str | None
    freshness_results: list[dict[str, Any]]


class AuditOut(ORMModel):
    id: uuid.UUID
    actor_id: str
    actor_role: str | None
    action: str
    entity_type: str
    entity_id: str
    before_hash: str | None
    after_hash: str | None
    details: dict[str, Any]
    correlation_id: str | None
    occurred_at: datetime


class IncidentFilter(BaseModel):
    state: IncidentState | None = None
    severity: Severity | None = None


class MoneyTotal(BaseModel):
    currency: str
    amount: Decimal


IncidentDetail.model_rebuild()
