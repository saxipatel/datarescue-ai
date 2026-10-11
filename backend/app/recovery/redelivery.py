"""RETRY_MISSING_BATCH: regenerate a synthetic batch with stable IDs and ingest it."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datagen.source import spec_for_sequence
from app.models import IngestionBatch, RecoveryJob, SourceSystem
from app.models.enums import BatchStatus
from app.services.pipeline import create_batch, create_dataset, run_batch


def redeliver(session: Session, job: RecoveryJob, delivery: dict[str, Any]) -> IngestionBatch:
    target_batch = session.get_one(IngestionBatch, job.target_batch_id)
    source = session.get_one(SourceSystem, target_batch.source_id)
    seq = int(delivery["sequence_number"])
    spec = spec_for_sequence(
        source, seq, datetime.fromisoformat(delivery["anchor"]), records=int(delivery["records"])
    )
    # The request key makes a repeated redelivery return the same batch.
    request_key = f"redelivery:{source.id}:{seq}"
    existing = session.scalar(
        select(IngestionBatch).where(IngestionBatch.request_key == request_key)
    )
    if existing is None:
        dataset = create_dataset(session, source, spec)
        existing, _ = create_batch(
            session,
            source=source,
            file_name=dataset.file_name,
            input_format="jsonl",
            correlation_id=job.correlation_id,
            request_key=request_key,
            expected_count=spec.records,
            sequence_number=seq,
            dataset_id=dataset.id,
            created_by=job.created_by,
            details={"generation": spec.as_params(), "redelivery_job_id": str(job.id)},
        )
        session.commit()
    if existing.status not in (BatchStatus.COMPLETE, BatchStatus.PARTIAL):
        run_batch(session, existing)
    session.refresh(existing)
    return existing
