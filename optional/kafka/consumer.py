"""Kafka -> micro-batch ingestion adapter (optional, compose profile `optional-streaming`).

Delivery semantics, stated plainly:
  * Kafka delivery is at-least-once: offsets are committed only after the batch is
    durably ingested, so a crash in between replays the same messages.
  * The database side is idempotent: each micro-batch gets a request key derived from
    its topic/partition/offset range, so a replayed range maps to the existing batch, and
    accepted_transactions.event_id is unique. Replays therefore do not create duplicate
    accepted rows.
  * This is not end-to-end exactly-once (no Kafka transactions spanning the DB write).

Messages that keep failing after bounded retries are copied to a dead-letter topic with
the error, and their offsets are committed so the partition is not blocked.
"""

import logging
import os
import signal
import time
import uuid
from pathlib import Path
from typing import Any

from confluent_kafka import Consumer, KafkaError, Message, Producer, TopicPartition

from app.config import get_settings
from app.db import session_scope
from app.logging_config import configure_logging
from app.models import SourceSystem
from app.services.pipeline import create_batch, data_path, run_batch

log = logging.getLogger("datarescue.kafka")

TOPIC = os.environ.get("KAFKA_TOPIC", "transactions")
DLQ_TOPIC = os.environ.get("KAFKA_DLQ_TOPIC", "transactions.dlq")
GROUP = os.environ.get("KAFKA_GROUP", "datarescue-ingest")
SOURCE = os.environ.get("KAFKA_SOURCE", "payments_core")
MAX_BATCH = int(os.environ.get("KAFKA_MAX_BATCH", "1000"))
MAX_WAIT_SECONDS = float(os.environ.get("KAFKA_MAX_WAIT_SECONDS", "5"))
MAX_ATTEMPTS = 3


def request_key(messages: list[Message]) -> str:
    first, last = messages[0], messages[-1]
    return f"kafka:{first.topic()}:{first.partition()}:{first.offset()}-{last.offset()}"


def write_micro_batch(messages: list[Message]) -> str:
    """Persist the raw message values as JSONL; unparseable values stay as raw text."""
    relative = f"kafka/{TOPIC}/{messages[0].partition()}-{messages[0].offset()}-{uuid.uuid4().hex[:8]}.jsonl"
    path: Path = data_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for msg in messages:
            out.write(
                (msg.value() or b"").decode("utf-8", errors="replace").replace("\n", " ") + "\n"
            )
    return relative


def ingest(messages: list[Message]) -> dict[str, Any]:
    key = request_key(messages)
    with session_scope() as session:
        source = session.query(SourceSystem).filter_by(name=SOURCE).one()
        batch, created = create_batch(
            session,
            source=source,
            file_name=write_micro_batch(messages),
            input_format="jsonl",
            correlation_id=uuid.uuid4().hex,
            request_key=key,
            expected_count=len(messages),
            details={
                "kafka": {
                    "topic": TOPIC,
                    "partition": messages[0].partition(),
                    "first_offset": messages[0].offset(),
                    "last_offset": messages[-1].offset(),
                }
            },
        )
        session.commit()
        run_batch(session, batch)  # no-op if this offset range was already ingested
        session.refresh(batch)
        return {
            "batch_id": str(batch.id),
            "new_batch": created,
            "status": batch.status,
            "accepted": batch.accepted_count,
            "request_key": key,
        }


def dead_letter(producer: Producer, messages: list[Message], error: str) -> None:
    for msg in messages:
        producer.produce(
            DLQ_TOPIC,
            key=msg.key(),
            value=msg.value(),
            headers={
                "error": error[:500],
                "source_topic": msg.topic(),
                "source_partition": str(msg.partition()),
                "source_offset": str(msg.offset()),
            },
        )
    producer.flush()


def run() -> None:
    configure_logging(get_settings().log_level)
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": GROUP,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    producer = Producer({"bootstrap.servers": bootstrap})
    consumer.subscribe([TOPIC])
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("consumer started", extra={"topic": TOPIC, "group": GROUP})

    while not stopping:
        polled = consumer.consume(num_messages=MAX_BATCH, timeout=MAX_WAIT_SECONDS)
        messages = []
        for m in polled:
            if m.error() is None:
                messages.append(m)
            elif m.error().code() != KafkaError._PARTITION_EOF:
                log.error("consumer error", extra={"error": str(m.error())})
        if not messages:
            continue
        # Micro-batches never span partitions, so an offset range identifies them.
        by_partition: dict[int, list[Message]] = {}
        for msg in messages:
            by_partition.setdefault(msg.partition(), []).append(msg)
        for part_messages in by_partition.values():
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    result = ingest(part_messages)
                    log.info("micro-batch ingested", extra=result)
                    break
                except Exception as exc:
                    log.warning("micro-batch failed", extra={"attempt": attempt, "error": str(exc)})
                    if attempt == MAX_ATTEMPTS:
                        dead_letter(producer, part_messages, str(exc))
                        log.error(
                            "sent to dead-letter topic",
                            extra={"count": len(part_messages), "topic": DLQ_TOPIC},
                        )
                    else:
                        time.sleep(2**attempt)
            # Commit only after the batch is durable (or dead-lettered).
            last = part_messages[-1]
            consumer.commit(offsets=[_next_offset(last)], asynchronous=False)
    consumer.close()


def _next_offset(msg: Message) -> TopicPartition:
    return TopicPartition(msg.topic(), msg.partition(), msg.offset() + 1)


if __name__ == "__main__":
    run()
