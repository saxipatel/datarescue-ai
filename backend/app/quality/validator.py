"""Row-level contract and business validation.

Pure functions over a single payload plus a small amount of per-batch state (the
event-time watermark). Database-backed checks such as cross-batch uniqueness live in
the ingestion engine.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.enums import TransactionStatus
from app.quality.contract import (
    CONTRACT_FIELDS,
    CORRUPT_MARKER,
    ERROR_RULES,
    REQUIRED_FIELDS,
)

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_LETTERS3_RE = re.compile(r"^[A-Za-z]{3}$")
_MIN_EVENT_TIME = datetime(2000, 1, 1, tzinfo=UTC)
_FUTURE_TOLERANCE = timedelta(minutes=5)
_STATUSES = {s.value for s in TransactionStatus}


@dataclass(frozen=True)
class RuleSet:
    """Effective rule configuration for one batch, resolved from quality_rules + source."""

    allowed_currencies: frozenset[str]
    max_amount: Decimal
    supported_schema_versions: frozenset[int]
    lateness_grace: timedelta
    reject_unexpected_fields: bool = True
    disabled_rules: frozenset[str] = frozenset()


@dataclass
class Issue:
    code: str
    field: str | None = None
    message: str = ""

    @property
    def rule(self) -> str:
        return ERROR_RULES[self.code]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass
class RowResult:
    record: dict[str, Any] | None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Watermark:
    """Max valid event_time seen so far in stream order; drives late-event detection."""

    value: datetime | None = None

    def observe(self, event_time: datetime) -> None:
        if self.value is None or event_time > self.value:
            self.value = event_time


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    # A timestamp without an offset is ambiguous; the contract requires one.
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def parse_amount(value: Any) -> Decimal | None:
    if isinstance(value, bool | float):
        # Floats are rejected outright: money must arrive as a decimal string or exact number.
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def validate_row(
    payload: dict[str, Any],
    rules: RuleSet,
    watermark: Watermark,
    *,
    now: datetime | None = None,
) -> RowResult:
    """Validate one event. Returns a typed record when there are no blocking errors.

    The watermark is only read here. Callers advance it for rows they actually accept, so
    duplicates and rejected rows never move event-time progress.
    """
    now = now or datetime.now(UTC)
    if CORRUPT_MARKER in payload:
        return RowResult(None, [Issue("CORRUPTED_PAYLOAD", None, "Row could not be parsed")])

    errors: list[Issue] = []
    warnings: list[Issue] = []

    def err(code: str, fld: str | None, message: str) -> None:
        if ERROR_RULES[code] not in rules.disabled_rules:
            errors.append(Issue(code, fld, message))

    for name in REQUIRED_FIELDS:
        if _blank(payload.get(name)):
            # Required-field checks protect NOT NULL columns, so they cannot be disabled.
            errors.append(Issue("MISSING_REQUIRED_FIELD", name, f"{name} is required"))
    for name in CONTRACT_FIELDS:
        if name not in REQUIRED_FIELDS and name != "event_id" and _blank(payload.get(name)):
            errors.append(Issue("SCHEMA_MISSING_FIELD", name, f"{name} is missing"))

    unexpected = sorted(set(payload) - set(CONTRACT_FIELDS))
    if unexpected and rules.reject_unexpected_fields:
        err("SCHEMA_UNEXPECTED_FIELD", ",".join(unexpected), f"Unexpected fields: {unexpected}")

    event_id = payload.get("event_id")
    if not _blank(event_id):
        try:
            uuid.UUID(str(event_id))
        except ValueError:
            errors.append(Issue("INVALID_EVENT_ID", "event_id", "event_id is not a UUID"))

    schema_version = None
    if not _blank(payload.get("schema_version")):
        schema_version = parse_int(payload["schema_version"])
        if schema_version is None:
            errors.append(Issue("INVALID_FIELD_TYPE", "schema_version", "Not an integer"))
        elif schema_version not in rules.supported_schema_versions:
            errors.append(
                Issue(
                    "SCHEMA_VERSION_UNSUPPORTED",
                    "schema_version",
                    f"Version {schema_version} not in {sorted(rules.supported_schema_versions)}",
                )
            )

    amount = None
    if not _blank(payload.get("amount")):
        amount = parse_amount(payload["amount"])
        if amount is None:
            errors.append(Issue("INVALID_FIELD_TYPE", "amount", "Amount is not a decimal"))
        elif amount <= 0:
            # Positivity is also a DB CHECK constraint, so it is always enforced.
            errors.append(Issue("AMOUNT_NON_POSITIVE", "amount", f"Amount {amount} <= 0"))
        elif amount.as_tuple().exponent < -2:  # type: ignore[operator]
            errors.append(Issue("AMOUNT_INVALID_PRECISION", "amount", "More than 2 decimals"))
        elif amount > rules.max_amount:
            err("AMOUNT_EXCEEDS_MAX", "amount", f"Amount {amount} > {rules.max_amount}")

    currency = payload.get("currency")
    if not _blank(currency):
        text = str(currency).strip()
        if not _CURRENCY_RE.match(text):
            # Lowercase-but-otherwise-valid codes are flagged distinctly so they can be repaired.
            errors.append(Issue("CURRENCY_INVALID_FORMAT", "currency", f"Bad code {text!r}"))
        elif text not in rules.allowed_currencies:
            err("CURRENCY_NOT_ALLOWED", "currency", f"{text} not in allowlist")

    status = payload.get("status")
    if not _blank(status) and str(status) not in _STATUSES:
        errors.append(Issue("STATUS_UNKNOWN", "status", f"Unknown status {status!r}"))

    event_time = None
    if not _blank(payload.get("event_time")):
        event_time = parse_timestamp(payload["event_time"])
        if event_time is None:
            errors.append(
                Issue("INVALID_FIELD_TYPE", "event_time", "Not an ISO-8601 timestamp with offset")
            )
        elif event_time > now + _FUTURE_TOLERANCE or event_time < _MIN_EVENT_TIME:
            errors.append(Issue("TIMESTAMP_IMPOSSIBLE", "event_time", f"{event_time} out of range"))

    if errors:
        return RowResult(None, errors, warnings)

    assert event_time is not None and amount is not None and schema_version is not None
    if (
        "late_event" not in rules.disabled_rules
        and watermark.value is not None
        and event_time < watermark.value - rules.lateness_grace
    ):
        lag = watermark.value - event_time
        warnings.append(
            Issue("LATE_EVENT", "event_time", f"{int(lag.total_seconds() // 60)} min behind")
        )

    record = {
        "transaction_id": str(payload["transaction_id"]).strip(),
        "customer_id": str(payload["customer_id"]).strip(),
        "merchant_id": str(payload["merchant_id"]).strip(),
        "amount": amount,
        "currency": str(currency).strip(),
        "status": str(status),
        "event_time": event_time,
        "schema_version": schema_version,
    }
    return RowResult(record, errors, warnings)


def normalize_currency_case(payload: dict[str, Any], allowed: frozenset[str]) -> dict | None:
    """Repair candidate for NORMALIZE_CURRENCY_CASE, or None if not safely repairable."""
    value = payload.get("currency")
    if not isinstance(value, str) or not _LETTERS3_RE.match(value.strip()):
        return None
    upper = value.strip().upper()
    if upper == value or upper not in allowed:
        return None
    return {**payload, "currency": upper}
