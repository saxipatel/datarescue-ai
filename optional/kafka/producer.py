"""Publish synthetic transaction events to Kafka/Redpanda.

    python -m optional.kafka.producer --records 2000 --scenario duplicate_event_ids --pct 5

Messages are keyed by event_id so all versions of an event land in one partition.
Corrupted rows are sent as their raw text, exactly as a broken producer would.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from confluent_kafka import Producer

from app.datagen.generator import ROW_SCENARIOS, GenerationSpec, iter_events
from app.quality.contract import CORRUPT_MARKER

TOPIC = os.environ.get("KAFKA_TOPIC", "transactions")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--scenario", choices=sorted(ROW_SCENARIOS), default="clean")
    parser.add_argument("--pct", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=int(datetime.now(UTC).timestamp()))
    parser.add_argument("--source", default="payments_core")
    args = parser.parse_args()

    producer = Producer(
        {
            "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"),
            "enable.idempotence": True,
            "acks": "all",
        }
    )
    spec = GenerationSpec(
        args.source,
        args.records,
        args.seed,
        args.scenario,
        args.pct,
        datetime.now(UTC).replace(microsecond=0),
    )
    sent = 0
    for payload, _label in iter_events(spec):
        if CORRUPT_MARKER in payload:
            key, value = None, payload[CORRUPT_MARKER].encode()
        else:
            key, value = str(payload["event_id"]).encode(), json.dumps(payload).encode()
        producer.produce(TOPIC, key=key, value=value)
        sent += 1
        if sent % 1000 == 0:
            producer.flush()
    producer.flush()
    print(f"published {sent} events to {TOPIC} (scenario={args.scenario}, seed={args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
