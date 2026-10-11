"""Load reference data and ingest a few clean batches per source.

Idempotent: reference rows use ON CONFLICT DO NOTHING and every batch has a stable
request key, so running it again changes nothing. Batches go through the normal
ingestion engine (inline, no worker needed).

    python scripts/seed.py [--batches 6]
"""

import argparse
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault("TASK_MODE", "sync")
import _bootstrap  # noqa: F401
from sqlalchemy import select

from app.datagen.source import spec_for_sequence
from app.db import session_scope
from app.models import SourceSystem
from app.services.pipeline import ingest_generated
from app.services.reference import load_reference_data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batches", type=int, default=6, help="clean batches per source")
    args = parser.parse_args()

    with session_scope() as session:
        load_reference_data(session)
    print("reference data: rules, lineage, sources, demo users loaded")

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    with session_scope() as session:
        for source in session.scalars(select(SourceSystem).order_by(SourceSystem.name)):
            for seq in range(1, args.batches + 1):
                # Space batches an hour apart, ending at the current hour.
                anchor = now - timedelta(hours=args.batches - seq)
                spec = spec_for_sequence(source, seq, anchor)
                batch, incident = ingest_generated(
                    session,
                    source,
                    spec,
                    sequence_number=seq,
                    request_key=f"seed:{source.name}:{seq}",
                    correlation_id=uuid.uuid4().hex,
                )
                session.refresh(batch)
                note = f" incident={incident.id}" if incident else ""
                print(
                    f"  {source.name} seq={seq} status={batch.status} "
                    f"accepted={batch.accepted_count}{note}"
                )
    print("seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
