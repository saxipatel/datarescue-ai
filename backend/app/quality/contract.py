"""Transaction event contract: fields, error codes and canonical hashing."""

import hashlib
import json
import uuid
from typing import Any

# Fields the source must send (v1). Ingestion assigns ingested_at, batch_id and payload_hash.
CONTRACT_FIELDS = (
    "event_id",
    "transaction_id",
    "customer_id",
    "merchant_id",
    "amount",
    "currency",
    "status",
    "event_time",
    "source_system",
    "schema_version",
)
# Missing any of these is a required_fields failure; other missing contract fields
# (other than event_id, which can be derived) are schema_contract failures.
REQUIRED_FIELDS = ("transaction_id", "customer_id", "amount", "currency", "event_time")

CORRUPT_MARKER = "_unparseable"

# error code -> owning rule. Codes are stable API values; see docs/data_contracts.md.
ERROR_RULES: dict[str, str] = {
    "MISSING_REQUIRED_FIELD": "required_fields",
    "AMOUNT_NON_POSITIVE": "amount_range",
    "AMOUNT_EXCEEDS_MAX": "amount_range",
    "AMOUNT_INVALID_PRECISION": "amount_range",
    "CURRENCY_INVALID_FORMAT": "currency_allowlist",
    "CURRENCY_NOT_ALLOWED": "currency_allowlist",
    "STATUS_UNKNOWN": "enum_status",
    "DUPLICATE_CONFLICT": "event_uniqueness",
    "DUPLICATE_IDENTICAL": "event_uniqueness",
    "LATE_EVENT": "late_event",
    "SCHEMA_MISSING_FIELD": "schema_contract",
    "SCHEMA_UNEXPECTED_FIELD": "schema_contract",
    "SCHEMA_VERSION_UNSUPPORTED": "schema_contract",
    "INVALID_FIELD_TYPE": "schema_contract",
    "INVALID_EVENT_ID": "schema_contract",
    "TIMESTAMP_IMPOSSIBLE": "schema_contract",
    "CORRUPTED_PAYLOAD": "schema_contract",
}

ROW_RULES = (
    "required_fields",
    "amount_range",
    "currency_allowlist",
    "enum_status",
    "event_uniqueness",
    "late_event",
    "schema_contract",
)
BATCH_RULES = ("completeness", "volume_anomaly")
SOURCE_RULES = ("freshness",)

_EVENT_NAMESPACE = uuid.UUID("6f1c3a52-6a8e-4d7e-9a43-5d1f0b7c2e11")


def codes_for_rule(rule_key: str) -> list[str]:
    return [code for code, rule in ERROR_RULES.items() if rule == rule_key]


def canonical_payload(payload: dict[str, Any]) -> str:
    """Format-independent serialization: CSV "12.50" and JSON 12.50 hash the same."""

    def norm(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k): norm(v) for k, v in value.items()}
        if isinstance(value, list):
            return [norm(v) for v in value]
        return str(value)

    return json.dumps(norm(payload), sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload).encode()).hexdigest()


def derive_event_id(source_name: str, digest: str) -> str:
    """Stable ID for rows that arrive without one, so replays map to the same key."""
    return str(uuid.uuid5(_EVENT_NAMESPACE, f"{source_name}:{digest}"))
