"""Kafka micro-batch adapter: idempotency of replays, without a broker.

The consumer's ingest path is exercised with stand-in Message objects. The broker loop
itself (polling, commits) was verified manually against Redpanda; see ADR 0005.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

pytest.importorskip("confluent_kafka")
REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:  # optional/ lives at the repository root
    sys.path.insert(0, str(REPO))
consumer = pytest.importorskip("optional.kafka.consumer")

from app.datagen.generator import GenerationSpec, iter_events  # noqa: E402
from app.models import AcceptedTransaction, IngestionBatch  # noqa: E402
from app.quality.contract import CORRUPT_MARKER  # noqa: E402

pytestmark = pytest.mark.integration


class FakeMessage:
    def __init__(self, offset: int, value: bytes, partition: int = 0):
        self._offset, self._value, self._partition = offset, value, partition

    def topic(self) -> str:
        return "transactions"

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def value(self) -> bytes:
        return self._value

    def key(self) -> bytes | None:
        return None

    def error(self) -> None:
        return None


def messages(records: int, scenario: str = "clean", pct: float = 0.0) -> list[FakeMessage]:
    spec = GenerationSpec(
        "payments_core", records, 99, scenario, pct, datetime(2026, 1, 5, 12, tzinfo=UTC)
    )
    out = []
    for offset, (payload, _) in enumerate(iter_events(spec)):
        text = payload[CORRUPT_MARKER] if CORRUPT_MARKER in payload else json.dumps(payload)
        out.append(FakeMessage(offset, text.encode()))
    return out


def accepted(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(AcceptedTransaction)) or 0


def batches(db) -> int:
    return db.scalar(select(func.count()).select_from(IngestionBatch)) or 0


def test_micro_batch_ingest_and_same_range_replay(db):
    msgs = messages(300, "corrupted_payload", 5)
    first = consumer.ingest(msgs)
    assert first["new_batch"] is True
    assert first["request_key"] == "kafka:transactions:0:0-299"
    assert first["status"] == "PARTIAL"  # corrupted lines quarantined, batch not failed
    rows = accepted(db)
    assert rows == first["accepted"] == 285

    # Consumer crashed before committing offsets: the same range is redelivered.
    again = consumer.ingest(msgs)
    assert again["new_batch"] is False and again["batch_id"] == first["batch_id"]
    assert accepted(db) == rows and batches(db) == 1


def test_differently_chunked_replay_adds_no_accepted_rows(db):
    msgs = messages(300)
    consumer.ingest(msgs)
    rows = accepted(db)

    # Replay split at a different boundary: new request keys, so new batches, but every
    # event_id is already accepted and the rows are skipped as identical duplicates.
    a, b = consumer.ingest(msgs[:120]), consumer.ingest(msgs[120:])
    assert a["new_batch"] and b["new_batch"]
    assert a["request_key"] == "kafka:transactions:0:0-119"
    assert accepted(db) == rows == 300
    replayed = db.scalars(
        select(IngestionBatch).where(
            IngestionBatch.request_key.in_([a["request_key"], b["request_key"]])
        )
    ).all()
    assert sum(r.skipped_count for r in replayed) == 300
    assert all(r.accepted_count == 0 for r in replayed)


def test_failed_range_goes_to_dead_letter_topic(monkeypatch):
    sent = []

    class FakeProducer:
        def produce(self, topic, key=None, value=None, headers=None):
            sent.append((topic, value, headers))

        def flush(self):
            pass

    msgs = messages(3)
    consumer.dead_letter(FakeProducer(), msgs, "boom")
    assert [t for t, _, _ in sent] == ["transactions.dlq"] * 3
    assert sent[0][2]["error"] == "boom" and sent[0][2]["source_offset"] == "0"
