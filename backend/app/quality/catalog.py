"""Default quality rules and pure threshold/baseline evaluation."""

from dataclasses import dataclass
from statistics import median
from typing import Any

from app.models.enums import Severity

# `locked` rules protect database invariants (NOT NULL, CHECK, UNIQUE) and cannot be
# disabled; their severity and thresholds remain editable.
DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "rule_key": "required_fields",
        "description": "transaction_id, customer_id, amount, currency and event_time present",
        "severity": Severity.HIGH,
        "configuration": {"locked": True, "max_failure_pct": 0},
    },
    {
        "rule_key": "amount_range",
        "description": "Amount > 0, at most 2 decimals and <= max_amount",
        "severity": Severity.HIGH,
        "configuration": {"locked": True, "max_failure_pct": 0, "max_amount": "50000.00"},
    },
    {
        "rule_key": "currency_allowlist",
        "description": "ISO-4217 style uppercase code in the allowed set",
        "severity": Severity.MEDIUM,
        "configuration": {
            "locked": True,
            "max_failure_pct": 0,
            "allowed_currencies": ["USD", "EUR", "GBP"],
        },
    },
    {
        "rule_key": "enum_status",
        "description": "status in PENDING, SETTLED, FAILED, REFUNDED",
        "severity": Severity.MEDIUM,
        "configuration": {"locked": True, "max_failure_pct": 0},
    },
    {
        "rule_key": "event_uniqueness",
        "description": "event_id unique; identical repeats skipped, conflicting repeats quarantined",
        "severity": Severity.HIGH,
        "configuration": {"locked": True, "max_failure_pct": 0},
    },
    {
        "rule_key": "freshness",
        "description": "A successful batch arrived within the source's freshness window",
        "severity": Severity.MEDIUM,
        "configuration": {"locked": False},
    },
    {
        "rule_key": "completeness",
        "description": "received >= expected_count from the manifest; no sequence gaps",
        "severity": Severity.HIGH,
        "configuration": {"locked": False, "min_ratio": 1.0},
    },
    {
        "rule_key": "late_event",
        "description": "event_time behind the batch watermark by more than the grace window",
        "severity": Severity.LOW,
        "configuration": {"locked": False, "max_failure_pct": 5},
    },
    {
        "rule_key": "schema_contract",
        "description": "Supported schema_version, no missing or unexpected fields, parseable types",
        "severity": Severity.CRITICAL,
        "configuration": {"locked": True, "max_failure_pct": 0, "reject_unexpected_fields": True},
    },
    {
        "rule_key": "volume_anomaly",
        "description": "Batch size within tolerance of the median of recent successful batches",
        "severity": Severity.MEDIUM,
        "configuration": {"locked": False, "tolerance_pct": 50, "min_history": 3, "window": 10},
    },
]

# Severity at or above which a failed rule opens an incident.
INCIDENT_MIN_SEVERITY = Severity.MEDIUM


def threshold_passed(failed: int, checked: int, config: dict[str, Any]) -> bool:
    limit = float(config.get("max_failure_pct", 0))
    if failed == 0:
        return True
    if limit <= 0 or checked == 0:
        return False
    return (failed / checked) * 100 <= limit


@dataclass(frozen=True)
class VolumeVerdict:
    passed: bool
    evaluated: bool
    baseline_median: float | None
    lower: float | None
    upper: float | None
    reason: str


def evaluate_volume(received: int, history: list[int], config: dict[str, Any]) -> VolumeVerdict:
    """Compare against a simple median band. This is a heuristic, not a statistical test."""
    min_history = int(config.get("min_history", 3))
    if len(history) < min_history:
        return VolumeVerdict(
            True, False, None, None, None, f"insufficient history ({len(history)}/{min_history})"
        )
    tolerance = float(config.get("tolerance_pct", 50)) / 100
    base = float(median(history))
    lower, upper = base * (1 - tolerance), base * (1 + tolerance)
    passed = lower <= received <= upper
    reason = "within baseline band" if passed else "outside baseline band"
    return VolumeVerdict(passed, True, base, lower, upper, reason)
