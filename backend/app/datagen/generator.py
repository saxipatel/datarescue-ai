"""Deterministic synthetic transaction events with labeled fault injection.

Rows are produced as a stream so 100k+ record files never sit in memory. While
generating, an oracle tracks what the ingestion engine *should* do with every row
(accept, warn, skip, quarantine); that becomes the manifest's ground truth.

All identifiers are synthetic. No card numbers or personal data are produced.
"""

import random
import uuid
from collections import Counter, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.quality.contract import CORRUPT_MARKER, derive_event_id, payload_hash

DEFAULT_GRACE = timedelta(minutes=15)

# fault -> expected disposition
FAULTS: dict[str, str] = {
    "duplicate_identical": "skipped",
    "duplicate_conflict": "quarantined",
    "null_customer_id": "quarantined",
    "negative_amount": "quarantined",
    "zero_amount": "quarantined",
    "currency_lowercase": "quarantined",
    "currency_invalid": "quarantined",
    "unknown_status": "quarantined",
    "schema_break": "quarantined",
    "unexpected_field": "quarantined",
    "missing_field": "quarantined",
    "impossible_timestamp": "quarantined",
    "late_event": "accepted_late",
    "out_of_order": "accepted_late",
    "corrupted_payload": "quarantined",
}

ROW_SCENARIOS: dict[str, dict[str, float]] = {
    "clean": {},
    "duplicate_event_ids": {"duplicate_identical": 1.0},
    "conflicting_duplicates": {"duplicate_conflict": 1.0},
    "null_customer_id": {"null_customer_id": 1.0},
    "invalid_amount": {"negative_amount": 0.8, "zero_amount": 0.2},
    "malformed_currency": {"currency_lowercase": 0.7, "currency_invalid": 0.3},
    "unknown_status": {"unknown_status": 1.0},
    "schema_break": {"schema_break": 1.0},
    "unexpected_field": {"unexpected_field": 1.0},
    "missing_field": {"missing_field": 1.0},
    "impossible_timestamp": {"impossible_timestamp": 1.0},
    "late_events": {"late_event": 1.0},
    "out_of_order": {"out_of_order": 1.0},
    "corrupted_payload": {"corrupted_payload": 1.0},
    "mixed_faults": {
        name: 1.0 for name in FAULTS if name not in {"schema_break", "out_of_order", "zero_amount"}
    },
}

_CURRENCIES = (("USD", 0.6), ("EUR", 0.25), ("GBP", 0.15))
_OFFSETS = (UTC, timezone(timedelta(hours=-5)), timezone(timedelta(hours=5, minutes=30)))


@dataclass(frozen=True)
class GenerationSpec:
    source_name: str
    records: int
    seed: int
    scenario: str = "clean"
    corruption_pct: float = 0.0
    # Last event time in the batch; events span `span_minutes` before it.
    anchor: datetime = field(default_factory=lambda: datetime(2026, 1, 5, tzinfo=UTC))
    span_minutes: int = 60
    schema_version: int = 1
    # Identical payloads from an earlier batch, used to model redelivery of a prior batch.
    prior_payloads: tuple[dict[str, Any], ...] = ()

    def as_params(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "records": self.records,
            "seed": self.seed,
            "scenario": self.scenario,
            "corruption_pct": self.corruption_pct,
            "anchor": self.anchor.isoformat(),
            "span_minutes": self.span_minutes,
            "schema_version": self.schema_version,
            "prior_payload_count": len(self.prior_payloads),
        }


@dataclass
class Label:
    row_number: int
    event_id: str
    fault: str
    expected: str


@dataclass
class Oracle:
    expected: Counter[str] = field(default_factory=Counter)
    faults: Counter[str] = field(default_factory=Counter)

    def summary(self) -> dict[str, Any]:
        return {
            "received": sum(self.expected.values()),
            "accepted": self.expected["accepted"] + self.expected["accepted_late"],
            "late_warnings": self.expected["accepted_late"],
            "quarantined": self.expected["quarantined"],
            "skipped": self.expected["skipped"],
            "faults": dict(self.faults),
        }


def _fault_plan(spec: GenerationSpec, rng: random.Random) -> dict[int, str]:
    mix = ROW_SCENARIOS.get(spec.scenario)
    if mix is None:
        raise ValueError(f"Unknown scenario {spec.scenario!r}")
    if not mix or spec.corruption_pct <= 0:
        return {}
    n_faulty = round(spec.records * spec.corruption_pct / 100)
    names, weights = zip(*mix.items(), strict=True)
    if spec.scenario == "schema_break":
        # A schema change affects every row after the producer deploys it.
        start = spec.records - n_faulty
        return dict.fromkeys(range(start, spec.records), "schema_break")
    # Row 0 stays clean so duplicate and lateness faults always have a predecessor.
    indices = rng.sample(range(1, spec.records), min(n_faulty, spec.records - 1))
    return {i: rng.choices(names, weights)[0] for i in sorted(indices)}


def _fmt_time(dt: datetime, rng: random.Random) -> str:
    # Producers emit mixed offsets; ingestion normalizes everything to UTC.
    return dt.astimezone(rng.choice(_OFFSETS)).isoformat()


def iter_events(spec: GenerationSpec) -> Iterator[tuple[dict[str, Any], Label | None]]:
    """Yield (payload, label) pairs in delivery order. Mutates nothing outside itself."""
    rng = random.Random(spec.seed)
    plan = _fault_plan(spec, rng)
    step = timedelta(minutes=spec.span_minutes) / max(spec.records, 1)
    start = spec.anchor - timedelta(minutes=spec.span_minutes)
    merchants = [f"M{n:04d}" for n in range(1, 41)]
    customers = [f"C{n:05d}" for n in range(1, 2001)]

    open_txns: deque[dict[str, Any]] = deque(maxlen=500)
    settled: deque[dict[str, Any]] = deque(maxlen=500)
    accepted_pool: deque[dict[str, Any]] = deque(maxlen=2000)
    prior = list(spec.prior_payloads)
    watermark: datetime | None = None
    quarantined_keys: set[tuple[str, str]] = set()
    txn_counter = 0

    def new_uuid() -> str:
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))

    for i in range(spec.records):
        nominal = start + step * (i + 1)
        # ~5% arrive a few minutes late but within the grace window: valid, no warning.
        jitter = timedelta(minutes=rng.uniform(0, 10)) if rng.random() < 0.05 else timedelta(0)
        event_time = nominal - jitter

        roll = rng.random()
        if open_txns and roll < 0.45:
            txn = open_txns.popleft()
            status = "SETTLED" if rng.random() < 0.85 else "FAILED"
            if status == "SETTLED":
                settled.append(txn)
        elif settled and roll < 0.48:
            txn = settled.popleft()
            status = "REFUNDED"
        else:
            txn_counter += 1
            amount = Decimal(str(round(rng.lognormvariate(3.6, 1.0), 2))).quantize(Decimal("0.01"))
            txn = {
                "transaction_id": f"T{spec.seed:05d}-{txn_counter:07d}",
                "customer_id": rng.choice(customers),
                "merchant_id": rng.choice(merchants),
                "amount": str(max(amount, Decimal("0.50"))),
                "currency": rng.choices([c for c, _ in _CURRENCIES], [w for _, w in _CURRENCIES])[
                    0
                ],
            }
            status = "PENDING"
            open_txns.append(txn)

        payload: dict[str, Any] = {
            "event_id": new_uuid(),
            **txn,
            "status": status,
            "event_time": _fmt_time(event_time, rng),
            "source_system": spec.source_name,
            "schema_version": spec.schema_version,
        }

        fault = plan.get(i)
        if fault in {"duplicate_identical", "duplicate_conflict"} and not (prior or accepted_pool):
            fault = None
        if fault is None:
            if watermark is None or event_time > watermark:
                watermark = event_time
            accepted_pool.append(payload)
            yield payload, None
            continue

        # Late rows are accepted with a warning but never advance the watermark.
        payload = _apply_fault(fault, payload, rng, prior, accepted_pool, watermark)
        digest = payload_hash(payload)
        event_id = payload.get("event_id")
        if CORRUPT_MARKER in payload:
            event_id = derive_event_id(spec.source_name, digest)
        expected = FAULTS[fault]
        if expected == "quarantined":
            # An exact repeat of an already-quarantined row is an identical duplicate.
            key = (str(event_id), digest)
            if key in quarantined_keys:
                expected = "skipped"
            quarantined_keys.add(key)
        yield payload, Label(i, str(event_id), fault, expected)


def _apply_fault(
    fault: str,
    payload: dict[str, Any],
    rng: random.Random,
    prior: list[dict[str, Any]],
    pool: deque[dict[str, Any]],
    watermark: datetime | None,
) -> dict[str, Any]:
    if fault in {"duplicate_identical", "duplicate_conflict"}:
        # Prefer redelivering events from a previous batch when one is supplied.
        source = rng.choice(prior) if prior and rng.random() < 0.7 else rng.choice(pool)
        copy = dict(source)
        if fault == "duplicate_conflict":
            copy["amount"] = str(Decimal(str(copy["amount"])) + Decimal("1.00"))
        return copy
    p = dict(payload)
    if fault == "null_customer_id":
        p["customer_id"] = None
    elif fault == "negative_amount":
        p["amount"] = "-" + str(p["amount"])
    elif fault == "zero_amount":
        p["amount"] = "0.00"
    elif fault == "currency_lowercase":
        p["currency"] = p["currency"].lower()
    elif fault == "currency_invalid":
        p["currency"] = rng.choice(["US$", "XYZ", "EURO", "12A"])
    elif fault == "unknown_status":
        p["status"] = rng.choice(["SETLED", "CHARGEBACK", "settled"])
    elif fault == "schema_break":
        # v2 producer: amount moved to integer minor units under a new name.
        p["schema_version"] = 2
        p["amount_minor"] = int(Decimal(p.pop("amount")) * 100)
    elif fault == "unexpected_field":
        p["promo_code"] = f"PROMO{rng.randint(100, 999)}"
    elif fault == "missing_field":
        del p["merchant_id"]
    elif fault == "impossible_timestamp":
        p["event_time"] = "2099-01-01T00:00:00+00:00"
    elif fault in {"late_event", "out_of_order"}:
        assert watermark is not None
        lag = rng.uniform(30, 240) if fault == "late_event" else rng.uniform(5, 30)
        p["event_time"] = (watermark - DEFAULT_GRACE - timedelta(minutes=lag)).isoformat()
    elif fault == "corrupted_payload":
        text = '{"event_id": "' + p["event_id"] + '", "amount": ' + str(p["amount"])[:3]
        return {CORRUPT_MARKER: text}
    return p


def generate(spec: GenerationSpec) -> tuple[list[dict[str, Any]], list[Label], dict[str, Any]]:
    """In-memory variant for tests and small batches."""
    rows: list[dict[str, Any]] = []
    labels: list[Label] = []
    oracle = Oracle()
    for payload, label in iter_events(spec):
        rows.append(payload)
        record_outcome(oracle, label)
        if label:
            labels.append(label)
    return rows, labels, oracle.summary()


def record_outcome(oracle: Oracle, label: Label | None) -> None:
    if label is None:
        oracle.expected["accepted"] += 1
        return
    oracle.expected[label.expected] += 1
    oracle.faults[label.fault] += 1
