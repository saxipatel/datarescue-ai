from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.quality.contract import CORRUPT_MARKER, canonical_payload, derive_event_id, payload_hash
from app.quality.validator import (
    RuleSet,
    Watermark,
    normalize_currency_case,
    parse_amount,
    parse_timestamp,
    validate_row,
)

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
RULES = RuleSet(
    allowed_currencies=frozenset({"USD", "EUR", "GBP"}),
    max_amount=Decimal("50000.00"),
    supported_schema_versions=frozenset({1}),
    lateness_grace=timedelta(minutes=15),
)


def row(**overrides):
    base = {
        "event_id": "8d5e2a9c-6c5b-4b0e-9a63-1f6a7c3d2b10",
        "transaction_id": "T1",
        "customer_id": "C00001",
        "merchant_id": "M0001",
        "amount": "12.50",
        "currency": "USD",
        "status": "SETTLED",
        "event_time": "2026-01-05T11:00:00Z",
        "source_system": "payments_core",
        "schema_version": 1,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not ...}


def codes(payload, rules=RULES, watermark=None):
    result = validate_row(payload, rules, watermark or Watermark(), now=NOW)
    return sorted({i.code for i in result.errors}), result


def test_valid_row_is_typed_and_normalized():
    errs, result = codes(row(event_time="2026-01-05T16:30:00+05:30"))
    assert errs == []
    assert result.record["amount"] == Decimal("12.50")
    assert result.record["event_time"] == datetime(2026, 1, 5, 11, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "field", ["transaction_id", "customer_id", "amount", "currency", "event_time"]
)
@pytest.mark.parametrize("value", [None, "", "   ", ...])
def test_required_fields(field, value):
    errs, _ = codes(row(**{field: value}))
    assert "MISSING_REQUIRED_FIELD" in errs


@pytest.mark.parametrize("field", ["merchant_id", "status", "source_system", "schema_version"])
def test_other_contract_fields_are_schema_errors(field):
    assert codes(row(**{field: ...}))[0] == ["SCHEMA_MISSING_FIELD"]


def test_missing_event_id_is_allowed():
    assert codes(row(event_id=...))[0] == []


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0.01", []),
        ("50000.00", []),  # boundary: equal to max is allowed
        ("50000.01", ["AMOUNT_EXCEEDS_MAX"]),
        ("0", ["AMOUNT_NON_POSITIVE"]),
        ("0.00", ["AMOUNT_NON_POSITIVE"]),
        ("-1.00", ["AMOUNT_NON_POSITIVE"]),
        ("1.005", ["AMOUNT_INVALID_PRECISION"]),
        ("abc", ["INVALID_FIELD_TYPE"]),
        ("NaN", ["INVALID_FIELD_TYPE"]),
        ("Infinity", ["INVALID_FIELD_TYPE"]),
        (12.5, ["INVALID_FIELD_TYPE"]),  # floats are never accepted for money
        (Decimal("12.5"), []),
        (7, []),
    ],
)
def test_amount_rules(amount, expected):
    assert codes(row(amount=amount))[0] == expected


def test_amount_decimal_is_exact():
    assert parse_amount("0.1") + parse_amount("0.2") == Decimal("0.3")
    assert parse_amount(True) is None


@pytest.mark.parametrize(
    ("currency", "expected"),
    [
        ("EUR", []),
        ("usd", ["CURRENCY_INVALID_FORMAT"]),
        ("US$", ["CURRENCY_INVALID_FORMAT"]),
        ("EURO", ["CURRENCY_INVALID_FORMAT"]),
        ("JPY", ["CURRENCY_NOT_ALLOWED"]),
    ],
)
def test_currency_rules(currency, expected):
    assert codes(row(currency=currency))[0] == expected


@pytest.mark.parametrize("status", ["PENDING", "SETTLED", "FAILED", "REFUNDED"])
def test_known_statuses(status):
    assert codes(row(status=status))[0] == []


@pytest.mark.parametrize("status", ["settled", "SETLED", "CHARGEBACK"])
def test_unknown_statuses(status):
    assert codes(row(status=status))[0] == ["STATUS_UNKNOWN"]


def test_schema_contract():
    assert codes(row(schema_version=2))[0] == ["SCHEMA_VERSION_UNSUPPORTED"]
    assert codes(row(schema_version="x"))[0] == ["INVALID_FIELD_TYPE"]
    assert codes(row(promo="1"))[0] == ["SCHEMA_UNEXPECTED_FIELD"]
    lenient = RuleSet(**{**RULES.__dict__, "reject_unexpected_fields": False})
    assert codes(row(promo="1"), lenient)[0] == []
    assert codes(row(event_id="not-a-uuid"))[0] == ["INVALID_EVENT_ID"]
    assert codes({CORRUPT_MARKER: "{oops"})[0] == ["CORRUPTED_PAYLOAD"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-05T11:00:00", ["INVALID_FIELD_TYPE"]),  # no offset: ambiguous
        ("yesterday", ["INVALID_FIELD_TYPE"]),
        ("2099-01-01T00:00:00Z", ["TIMESTAMP_IMPOSSIBLE"]),
        ("1999-12-31T23:59:59Z", ["TIMESTAMP_IMPOSSIBLE"]),
        ("2026-01-05T12:04:59Z", []),  # within 5 min clock-skew tolerance
        ("2026-01-05T12:05:01Z", ["TIMESTAMP_IMPOSSIBLE"]),
    ],
)
def test_timestamps(value, expected):
    assert codes(row(event_time=value))[0] == expected


def test_lateness_uses_watermark_and_grace():
    wm = Watermark(datetime(2026, 1, 5, 11, 0, tzinfo=UTC))
    _, within = codes(row(event_time="2026-01-05T10:45:00Z"), watermark=wm)  # exactly grace
    assert within.warnings == []
    _, late = codes(row(event_time="2026-01-05T10:44:59Z"), watermark=wm)
    assert [w.code for w in late.warnings] == ["LATE_EVENT"]
    disabled = RuleSet(**{**RULES.__dict__, "disabled_rules": frozenset({"late_event"})})
    _, ok = codes(row(event_time="2026-01-05T09:00:00Z"), disabled, wm)
    assert ok.warnings == []


def test_validation_never_moves_the_watermark():
    wm = Watermark()
    codes(row(event_time="2026-01-05T11:00:00Z"), watermark=wm)
    codes(row(event_time="2026-01-05T11:00:00Z", amount="-1"), watermark=wm)
    assert wm.value is None  # the ingestion engine advances it for accepted rows only
    wm.observe(datetime(2026, 1, 5, 11, 0, tzinfo=UTC))
    wm.observe(datetime(2026, 1, 5, 10, 0, tzinfo=UTC))
    assert wm.value == datetime(2026, 1, 5, 11, 0, tzinfo=UTC)


def test_disabled_rule_skips_its_checks():
    rules = RuleSet(**{**RULES.__dict__, "disabled_rules": frozenset({"currency_allowlist"})})
    assert codes(row(currency="JPY"), rules)[0] == []
    # Format errors remain: the DB CHECK requires three uppercase letters.
    assert codes(row(currency="usd"), rules)[0] == ["CURRENCY_INVALID_FORMAT"]


def test_normalize_currency_case():
    allowed = RULES.allowed_currencies
    assert normalize_currency_case(row(currency="usd"), allowed)["currency"] == "USD"
    assert normalize_currency_case(row(currency="Gbp"), allowed)["currency"] == "GBP"
    assert normalize_currency_case(row(currency="jpy"), allowed) is None
    assert normalize_currency_case(row(currency="USD"), allowed) is None
    assert normalize_currency_case(row(currency="us$"), allowed) is None


def test_hash_is_format_independent_and_order_independent():
    a = {"amount": Decimal("12.50"), "schema_version": 1, "currency": "USD"}
    b = {"currency": "USD", "schema_version": "1", "amount": "12.50"}
    assert payload_hash(a) == payload_hash(b)
    assert canonical_payload(a) == canonical_payload(b)
    assert payload_hash(a) != payload_hash({**b, "amount": "12.5"})


def test_derived_event_id_is_stable():
    assert derive_event_id("src", "abc") == derive_event_id("src", "abc")
    assert derive_event_id("src", "abc") != derive_event_id("other", "abc")


def test_parse_timestamp_normalizes_to_utc():
    assert parse_timestamp("2026-01-05T06:00:00-05:00") == datetime(2026, 1, 5, 11, tzinfo=UTC)
