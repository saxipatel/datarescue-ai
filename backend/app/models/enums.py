from enum import StrEnum


class Role(StrEnum):
    ANALYST = "analyst"
    ENGINEER = "engineer"
    STEWARD = "steward"
    ADMIN = "admin"


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class BatchStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RawDisposition(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    QUARANTINED = "QUARANTINED"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"


class QuarantineState(StrEnum):
    OPEN = "OPEN"
    REPLAYED = "REPLAYED"
    REPAIRED = "REPAIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return ["low", "medium", "high", "critical"].index(self.value)


class IncidentState(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    CLOSED_AS_FALSE_POSITIVE = "CLOSED_AS_FALSE_POSITIVE"


class RunState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RepairAction(StrEnum):
    SKIP_IDENTICAL_DUPLICATE = "SKIP_IDENTICAL_DUPLICATE"
    REPLAY_QUARANTINED = "REPLAY_QUARANTINED"
    NORMALIZE_CURRENCY_CASE = "NORMALIZE_CURRENCY_CASE"
    RETRY_MISSING_BATCH = "RETRY_MISSING_BATCH"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class ProposalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"


class RecoveryState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def sql_in(enum_cls: type[StrEnum]) -> str:
    """Render enum values for a CHECK constraint."""
    return "(" + ", ".join(f"'{member.value}'" for member in enum_cls) + ")"
