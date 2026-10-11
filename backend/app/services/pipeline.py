"""Batch orchestration: create (idempotently), run, finalize, and record run events."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.datagen.files import write_dataset
from app.datagen.generator import GenerationSpec
from app.models import (
    Dataset,
    Incident,
    IngestionBatch,
    PipelineRun,
    PipelineRunEvent,
    SourceSystem,
)
from app.models.enums import BatchStatus, RunState
from app.services.audit import Actor, record_audit
from app.services.incidents import open_batch_incident
from app.services.ingestion import ingest_rows
from app.services.quality import accepted_checksum, decide_status, evaluate_batch
from app.services.readers import iter_rows


def data_path(relative: str) -> Path:
    root = get_settings().data_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path escapes the data directory")
    return path


def log_event(
    session: Session, run: PipelineRun | None, level: str, message: str, **context: Any
) -> None:
    if run is not None:
        session.add(
            PipelineRunEvent(
                run_id=run.id,
                level=level,
                message=message,
                context=json.loads(json.dumps(context, default=str)),
            )
        )


def create_dataset(
    session: Session,
    source: SourceSystem,
    spec: GenerationSpec,
    *,
    fmt: str = "jsonl",
    created_by: uuid.UUID | None = None,
) -> Dataset:
    dataset_id = uuid.uuid4()
    relative = f"generated/{source.name}/{dataset_id}.{fmt}"
    manifest = write_dataset(spec, data_path(relative), fmt)
    dataset = Dataset(
        id=dataset_id,
        scenario=spec.scenario,
        seed=spec.seed,
        record_count=spec.records,
        source_id=source.id,
        file_name=relative,
        manifest=manifest,
        created_by=created_by,
    )
    session.add(dataset)
    session.flush()
    return dataset


def create_batch(
    session: Session,
    *,
    source: SourceSystem,
    file_name: str,
    input_format: str,
    correlation_id: str,
    request_key: str | None = None,
    expected_count: int | None = None,
    sequence_number: int | None = None,
    dataset_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> tuple[IngestionBatch, bool]:
    """Return (batch, created). A repeated request_key returns the original batch."""
    if request_key:
        existing = session.scalar(
            select(IngestionBatch).where(IngestionBatch.request_key == request_key)
        )
        if existing:
            return existing, False
    batch = IngestionBatch(
        source_id=source.id,
        dataset_id=dataset_id,
        request_key=request_key,
        sequence_number=sequence_number,
        status=BatchStatus.PENDING,
        input_format=input_format,
        file_name=file_name,
        expected_count=expected_count,
        correlation_id=correlation_id,
        created_by=created_by,
        details=details or {},
    )
    session.add(batch)
    try:
        session.flush()
    except IntegrityError:
        # Lost a race on request_key or (source, sequence); return whoever won.
        session.rollback()
        if request_key:
            existing = session.scalar(
                select(IngestionBatch).where(IngestionBatch.request_key == request_key)
            )
            if existing:
                return existing, False
        raise
    return batch, True


def create_run(
    session: Session,
    pipeline_name: str,
    correlation_id: str,
    *,
    batch_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    parameters: dict[str, Any] | None = None,
) -> PipelineRun:
    run = PipelineRun(
        pipeline_name=pipeline_name,
        batch_id=batch_id,
        subject_id=subject_id,
        state=RunState.QUEUED,
        correlation_id=correlation_id,
        created_by=created_by,
        parameters=parameters or {},
    )
    session.add(run)
    session.flush()
    return run


def run_batch(
    session: Session, batch: IngestionBatch, run: PipelineRun | None = None
) -> Incident | None:
    """Ingest a batch's file from its checkpoint, then finalize. Safe to call repeatedly."""
    if batch.status in (BatchStatus.COMPLETE, BatchStatus.PARTIAL) and batch.finished_at:
        return None  # already done; callers treat this as idempotent success
    settings = get_settings()
    path = data_path(batch.file_name)
    log_event(
        session,
        run,
        "INFO",
        "batch started",
        batch_id=batch.id,
        resume_from=batch.processed_count,
        file=Path(batch.file_name).name,
    )
    session.commit()

    ingest_rows(
        session,
        batch,
        iter_rows(path, batch.input_format),
        chunk_size=settings.ingestion_chunk_size,
    )
    return finalize_batch(session, batch, run)


def finalize_batch(
    session: Session, batch: IngestionBatch, run: PipelineRun | None = None
) -> Incident | None:
    session.refresh(batch)
    batch.status = decide_status(batch)
    batch.checksum = accepted_checksum(session, batch.id)
    batch.finished_at = datetime.now(UTC)
    batch.error_message = None
    results = evaluate_batch(session, batch)
    failed = [r for r in results if not r.passed]
    versions = batch.details.get("schema_versions", {})
    log_event(session, run, "INFO", "schema versions observed", versions=versions)
    log_event(
        session,
        run,
        "WARN" if failed else "INFO",
        "batch finished",
        status=batch.status,
        accepted=batch.accepted_count,
        rejected=batch.rejected_count,
        skipped=batch.skipped_count,
        failed_rules=len(failed),
    )
    incident = open_batch_incident(session, batch)
    record_audit(
        session,
        Actor.system("ingestion"),
        "batch.completed",
        "batch",
        batch.id,
        after={"status": batch.status, "checksum": batch.checksum},
        details={
            "received": batch.received_count,
            "accepted": batch.accepted_count,
            "rejected": batch.rejected_count,
            "skipped": batch.skipped_count,
            "incident_id": incident.id if incident else None,
        },
    )
    session.commit()
    return incident


def mark_batch_failed(session: Session, batch_id: uuid.UUID, error: str) -> None:
    """Record a failure while keeping the checkpoint so a retry resumes, not restarts."""
    session.rollback()
    batch = session.get(IngestionBatch, batch_id)
    if batch and batch.status not in (BatchStatus.COMPLETE, BatchStatus.PARTIAL):
        batch.status = BatchStatus.FAILED
        batch.error_message = error[:2000]
        session.commit()


def ingest_generated(
    session: Session,
    source: SourceSystem,
    spec: GenerationSpec,
    *,
    sequence_number: int | None,
    request_key: str,
    correlation_id: str | None = None,
    created_by: uuid.UUID | None = None,
) -> tuple[IngestionBatch, Incident | None]:
    """Generate a dataset and ingest it synchronously (seed script, tests, benchmarks)."""
    existing = session.scalar(
        select(IngestionBatch).where(IngestionBatch.request_key == request_key)
    )
    if existing is not None and existing.finished_at is not None:
        return existing, None
    if existing is None:
        dataset = create_dataset(session, source, spec, created_by=created_by)
        existing, _ = create_batch(
            session,
            source=source,
            file_name=dataset.file_name,
            input_format="jsonl",
            correlation_id=correlation_id or uuid.uuid4().hex,
            request_key=request_key,
            expected_count=spec.records,
            sequence_number=sequence_number,
            dataset_id=dataset.id,
            created_by=created_by,
            details={"generation": spec.as_params()},
        )
        session.commit()
    incident = run_batch(session, existing)
    return existing, incident
