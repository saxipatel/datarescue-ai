"""Sources, datasets, ingestion, batches and quality rules."""

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, Paging, rate_limited, require
from app.config import get_settings
from app.datagen.generator import ROW_SCENARIOS, GenerationSpec
from app.errors import ConflictError, NotFoundError, ServiceUnavailable, ValidationFailed
from app.logging_config import correlation_id_var
from app.models import (
    Dataset,
    Incident,
    IngestionBatch,
    PipelineRun,
    PipelineRunEvent,
    QualityResult,
    QualityRule,
    SourceSystem,
    User,
)
from app.models.enums import BatchStatus, RunState
from app.repositories.pagination import paginate
from app.schemas.common import Page
from app.schemas.resources import (
    BatchDetail,
    BatchOut,
    DatasetGenerate,
    DatasetOut,
    IngestionAccepted,
    IngestionRequest,
    QualityResultOut,
    RuleOut,
    RuleUpdate,
    RunEventOut,
    RunOut,
    SourceCreate,
    SourceOut,
)
from app.security.rbac import Permission
from app.services.audit import Actor, record_audit
from app.services.pipeline import create_batch, create_dataset, create_run, data_path
from app.services.readers import detect_format, preflight
from app.workers.queue import enqueue
from app.workers.tasks import ingest_batch_task

router = APIRouter()


def _source_by_name(db: DB, name: str) -> SourceSystem:
    source = db.scalar(select(SourceSystem).where(SourceSystem.name == name))
    if source is None:
        raise NotFoundError(f"Unknown source '{name}'")
    if not source.enabled:
        raise ConflictError(f"Source '{name}' is disabled")
    return source


# ---- sources --------------------------------------------------------------


@router.get("/sources", response_model=list[SourceOut], tags=["sources"])
def list_sources(db: DB, _: CurrentUser) -> list[SourceSystem]:
    return list(db.scalars(select(SourceSystem).order_by(SourceSystem.name)))


@router.post("/sources", response_model=SourceOut, status_code=201, tags=["sources"])
def create_source(
    body: SourceCreate, db: DB, user: Annotated[User, Depends(require(Permission.MANAGE_SOURCES))]
) -> SourceSystem:
    source = SourceSystem(
        **body.model_dump(), enabled=True, settings={"base_seed": 1, "records_per_batch": 1000}
    )
    db.add(source)
    try:
        db.flush()
    except IntegrityError as exc:
        raise ConflictError(f"Source '{body.name}' already exists") from exc
    record_audit(
        db, Actor.from_user(user), "source.created", "source", source.id, after=body.model_dump()
    )
    db.commit()
    return source


# ---- datasets -------------------------------------------------------------


def _dataset_out(d: Dataset) -> DatasetOut:
    return DatasetOut(
        id=d.id,
        scenario=d.scenario,
        seed=d.seed,
        record_count=d.record_count,
        source_id=d.source_id,
        created_at=d.created_at,
        expected=d.manifest.get("expected", {}),
        format=d.manifest.get("format", "jsonl"),
    )


@router.post("/datasets/generate", response_model=DatasetOut, status_code=201, tags=["datasets"])
def generate_dataset(
    body: DatasetGenerate,
    db: DB,
    user: Annotated[User, Depends(require(Permission.INGEST))],
    _: Annotated[User, Depends(rate_limited("generate"))],
) -> DatasetOut:
    if body.scenario not in ROW_SCENARIOS:
        raise ValidationFailed("Unknown scenario", details={"allowed": sorted(ROW_SCENARIOS)})
    if body.records > get_settings().max_generated_records:
        raise ValidationFailed("Too many records requested")
    source = _source_by_name(db, body.source)
    spec = GenerationSpec(
        source_name=source.name,
        records=body.records,
        seed=body.seed,
        scenario=body.scenario,
        corruption_pct=body.corruption_pct,
        anchor=datetime.now(UTC),
        schema_version=source.contract_version,
    )
    dataset = create_dataset(db, source, spec, fmt=body.format, created_by=user.id)
    record_audit(
        db,
        Actor.from_user(user),
        "dataset.generated",
        "dataset",
        dataset.id,
        details=spec.as_params(),
    )
    db.commit()
    return _dataset_out(dataset)


@router.get("/datasets", response_model=Page[DatasetOut], tags=["datasets"])
def list_datasets(db: DB, _: CurrentUser, paging: Paging) -> Page[DatasetOut]:
    rows, total = paginate(
        db, select(Dataset).order_by(Dataset.created_at.desc()), paging.limit, paging.offset
    )
    return Page(
        items=[_dataset_out(r[0]) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


# ---- ingestion ------------------------------------------------------------


def _queue_batch(db: DB, batch: IngestionBatch, user: User) -> tuple[PipelineRun, str | None]:
    run = create_run(db, "ingest", batch.correlation_id, batch_id=batch.id, created_by=user.id)
    db.commit()
    try:
        job_id = enqueue(ingest_batch_task, str(run.id))
    except ServiceUnavailable:
        run.state = RunState.FAILED
        run.error_message = "queue unavailable at submission"
        batch.status = BatchStatus.FAILED
        batch.error_message = "Not queued: task queue unavailable. Resubmit or resume."
        db.commit()
        raise
    return run, job_id


def _accepted(
    batch: IngestionBatch, run: PipelineRun | None, job_id: str | None, duplicate: bool
) -> IngestionAccepted:
    return IngestionAccepted(
        batch_id=batch.id,
        run_id=run.id if run else None,
        job_id=job_id,
        duplicate_request=duplicate,
        status_url=f"/api/v1/batches/{batch.id}",
    )


@router.post("/ingestions", response_model=IngestionAccepted, status_code=202, tags=["ingestion"])
def start_ingestion(
    body: IngestionRequest,
    db: DB,
    response: Response,
    user: Annotated[User, Depends(require(Permission.INGEST))],
) -> IngestionAccepted:
    """Ingest a generated dataset. Repeating the same request returns the original batch."""
    source = _source_by_name(db, body.source)
    dataset = db.get(Dataset, body.dataset_id)
    if dataset is None or dataset.source_id != source.id:
        raise NotFoundError("Dataset not found for this source")
    key = body.request_key or f"dataset:{dataset.id}"
    batch, created = create_batch(
        db,
        source=source,
        file_name=dataset.file_name,
        input_format=dataset.manifest.get("format", "jsonl"),
        correlation_id=correlation_id_var.get() or uuid.uuid4().hex,
        request_key=key,
        expected_count=body.expected_count
        if body.expected_count is not None
        else dataset.record_count,
        sequence_number=body.sequence_number,
        dataset_id=dataset.id,
        created_by=user.id,
        details={"generation": dataset.manifest.get("params", {})},
    )
    if not created and batch.status in (
        BatchStatus.COMPLETE,
        BatchStatus.PARTIAL,
        BatchStatus.PROCESSING,
    ):
        response.status_code = status.HTTP_200_OK
        return _accepted(batch, None, None, duplicate=True)
    record_audit(
        db,
        Actor.from_user(user),
        "ingestion.requested",
        "batch",
        batch.id,
        details={"dataset_id": dataset.id, "request_key": key, "resubmitted": not created},
    )
    run, job_id = _queue_batch(db, batch, user)
    return _accepted(batch, run, job_id, duplicate=not created)


@router.post(
    "/ingestions/upload", response_model=IngestionAccepted, status_code=202, tags=["ingestion"]
)
async def upload_ingestion(
    db: DB,
    response: Response,
    user: Annotated[User, Depends(require(Permission.INGEST))],
    file: Annotated[UploadFile, File(description=".csv or .jsonl, UTF-8")],
    source: Annotated[str, Form()],
    expected_count: Annotated[int | None, Form(ge=0)] = None,
    sequence_number: Annotated[int | None, Form(ge=1)] = None,
) -> IngestionAccepted:
    settings = get_settings()
    fmt = detect_format(file.filename or "")
    src = _source_by_name(db, source)
    relative = f"uploads/{uuid.uuid4()}.{fmt}"
    path = data_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                path.unlink(missing_ok=True)
                raise ValidationFailed(
                    "File exceeds the upload limit",
                    code="file_too_large",
                    details={"max_bytes": settings.max_upload_bytes},
                )
            digest.update(chunk)
            out.write(chunk)
    try:
        preflight(path, fmt)
    except ValidationFailed:
        path.unlink(missing_ok=True)
        raise
    key = f"upload:{src.id}:{digest.hexdigest()}"
    batch, created = create_batch(
        db,
        source=src,
        file_name=relative,
        input_format=fmt,
        correlation_id=correlation_id_var.get() or uuid.uuid4().hex,
        request_key=key,
        expected_count=expected_count,
        sequence_number=sequence_number,
        created_by=user.id,
        details={
            "upload": {
                "filename": (file.filename or "")[:200],
                "bytes": size,
                "sha256": digest.hexdigest(),
            }
        },
    )
    if not created:
        path.unlink(missing_ok=True)
        if batch.status in (BatchStatus.COMPLETE, BatchStatus.PARTIAL, BatchStatus.PROCESSING):
            response.status_code = status.HTTP_200_OK
            return _accepted(batch, None, None, duplicate=True)
    record_audit(
        db,
        Actor.from_user(user),
        "ingestion.uploaded",
        "batch",
        batch.id,
        details={"bytes": size, "format": fmt},
    )
    run, job_id = _queue_batch(db, batch, user)
    return _accepted(batch, run, job_id, duplicate=not created)


# ---- batches --------------------------------------------------------------


def _batch_out(db: DB, batch: IngestionBatch, source_name: str | None = None) -> BatchOut:
    out = BatchOut.model_validate(batch)
    out.source_name = source_name or db.scalar(
        select(SourceSystem.name).where(SourceSystem.id == batch.source_id)
    )
    out.failed_rules = (
        db.scalar(
            select(func.count())
            .select_from(QualityResult)
            .where(QualityResult.batch_id == batch.id, QualityResult.passed.is_(False))
        )
        or 0
    )
    out.incident_id = db.scalar(select(Incident.id).where(Incident.batch_id == batch.id))
    return out


@router.get("/batches", response_model=Page[BatchOut], tags=["batches"])
def list_batches(
    db: DB,
    _: CurrentUser,
    paging: Paging,
    source: str | None = None,
    status_: Annotated[BatchStatus | None, Query(alias="status")] = None,
) -> Page[BatchOut]:
    query = (
        select(IngestionBatch, SourceSystem.name)
        .join(SourceSystem, SourceSystem.id == IngestionBatch.source_id)
        .order_by(IngestionBatch.created_at.desc())
    )
    if source:
        query = query.where(SourceSystem.name == source)
    if status_:
        query = query.where(IngestionBatch.status == status_)
    rows, total = paginate(db, query, paging.limit, paging.offset)
    return Page(
        items=[_batch_out(db, b, name) for b, name in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


def _results_out(db: DB, query: Any) -> list[QualityResultOut]:
    out = []
    for result, rule_key in db.execute(query).all():
        item = QualityResultOut.model_validate(result)
        item.rule_key = rule_key
        out.append(item)
    return out


@router.get("/batches/{batch_id}", response_model=BatchDetail, tags=["batches"])
def get_batch(batch_id: uuid.UUID, db: DB, _: CurrentUser) -> BatchDetail:
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:
        raise NotFoundError("Batch not found")
    base = _batch_out(db, batch)
    results = _results_out(
        db,
        select(QualityResult, QualityRule.rule_key)
        .join(QualityRule, QualityRule.id == QualityResult.rule_id)
        .where(QualityResult.batch_id == batch.id)
        .order_by(QualityResult.passed, QualityRule.rule_key),
    )
    runs = db.scalars(
        select(PipelineRun).where(PipelineRun.batch_id == batch.id).order_by(PipelineRun.created_at)
    ).all()
    events = (
        db.scalars(
            select(PipelineRunEvent)
            .where(PipelineRunEvent.run_id.in_([r.id for r in runs]))
            .order_by(PipelineRunEvent.id)
            .limit(100)
        ).all()
        if runs
        else []
    )
    return BatchDetail(
        **base.model_dump(),
        details=batch.details,
        quality_results=results,
        runs=[RunOut.model_validate(r) for r in runs],
        run_events=[RunEventOut.model_validate(e) for e in events],
    )


@router.post(
    "/batches/{batch_id}/resume",
    response_model=IngestionAccepted,
    status_code=202,
    tags=["batches"],
)
def resume_batch(
    batch_id: uuid.UUID, db: DB, user: Annotated[User, Depends(require(Permission.INGEST))]
) -> IngestionAccepted:
    """Re-queue a FAILED or stuck batch; processing resumes from its checkpoint."""
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:
        raise NotFoundError("Batch not found")
    if batch.status in (BatchStatus.COMPLETE, BatchStatus.PARTIAL):
        raise ConflictError(f"Batch is already {batch.status}")
    record_audit(
        db,
        Actor.from_user(user),
        "batch.resume_requested",
        "batch",
        batch.id,
        details={"checkpoint": batch.processed_count},
    )
    run, job_id = _queue_batch(db, batch, user)
    return _accepted(batch, run, job_id, duplicate=False)


# ---- quality rules and results -------------------------------------------


@router.get("/quality/rules", response_model=list[RuleOut], tags=["quality"])
def list_rules(db: DB, _: CurrentUser) -> list[QualityRule]:
    return list(db.scalars(select(QualityRule).order_by(QualityRule.rule_key)))


_NUMERIC_KEYS = {
    "max_failure_pct": (0, 100),
    "tolerance_pct": (0, 1000),
    "min_history": (1, 100),
    "window": (1, 100),
    "min_ratio": (0, 1),
    "max_age_minutes": (5, 43200),
}


def _validate_config(rule: QualityRule, config: dict[str, Any]) -> dict[str, Any]:
    merged = {**rule.configuration}
    for key, value in config.items():
        if key == "locked":
            raise ValidationFailed("'locked' is not editable")
        if key in _NUMERIC_KEYS:
            low, high = _NUMERIC_KEYS[key]
            if not isinstance(value, int | float) or not low <= value <= high:
                raise ValidationFailed(f"{key} must be a number in [{low}, {high}]")
        elif key == "allowed_currencies":
            if (
                not isinstance(value, list)
                or not value
                or not all(
                    isinstance(c, str) and len(c) == 3 and c.isalpha() and c.isupper()
                    for c in value
                )
            ):
                raise ValidationFailed("allowed_currencies must be uppercase 3-letter codes")
        elif key == "max_amount":
            try:
                if Decimal(str(value)) <= 0:
                    raise ValidationFailed("max_amount must be positive")
            except InvalidOperation as exc:
                raise ValidationFailed("max_amount must be a decimal string") from exc
            value = str(value)
        elif key == "reject_unexpected_fields":
            if not isinstance(value, bool):
                raise ValidationFailed("reject_unexpected_fields must be boolean")
        elif key == "supported_versions":
            if not isinstance(value, list) or not all(isinstance(v, int) for v in value):
                raise ValidationFailed("supported_versions must be a list of integers")
        else:
            raise ValidationFailed(f"Unknown configuration key '{key}' for {rule.rule_key}")
        merged[key] = value
    return merged


@router.patch("/quality/rules/{rule_id}", response_model=RuleOut, tags=["quality"])
def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdate,
    db: DB,
    user: Annotated[User, Depends(require(Permission.MANAGE_RULES))],
) -> QualityRule:
    rule = db.get(QualityRule, rule_id)
    if rule is None:
        raise NotFoundError("Rule not found")
    if body.expected_version is not None and body.expected_version != rule.version:
        raise ConflictError(
            "Rule changed since you loaded it", details={"current_version": rule.version}
        )
    before = {
        "severity": rule.severity,
        "enabled": rule.enabled,
        "configuration": rule.configuration,
        "version": rule.version,
    }
    if body.enabled is False and rule.configuration.get("locked"):
        raise ConflictError(
            f"{rule.rule_key} protects database constraints and cannot be disabled; "
            "adjust its severity or thresholds instead",
            code="rule_locked",
        )
    if body.severity is not None:
        rule.severity = body.severity
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.configuration:
        rule.configuration = _validate_config(rule, body.configuration)
    rule.version += 1
    after = {
        "severity": rule.severity,
        "enabled": rule.enabled,
        "configuration": rule.configuration,
        "version": rule.version,
    }
    record_audit(
        db,
        Actor.from_user(user),
        "rule.updated",
        "quality_rule",
        rule.id,
        before=before,
        after=after,
        details={
            "rule_key": rule.rule_key,
            "reason": body.reason,
            "before": before,
            "after": after,
        },
    )
    db.commit()
    return rule


@router.get("/quality/results", response_model=Page[QualityResultOut], tags=["quality"])
def list_results(
    db: DB,
    _: CurrentUser,
    paging: Paging,
    batch_id: uuid.UUID | None = None,
    rule_key: str | None = None,
    passed: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[QualityResultOut]:
    query = (
        select(QualityResult, QualityRule.rule_key)
        .join(QualityRule, QualityRule.id == QualityResult.rule_id)
        .order_by(QualityResult.checked_at.desc())
    )
    if batch_id:
        query = query.where(QualityResult.batch_id == batch_id)
    if rule_key:
        query = query.where(QualityRule.rule_key == rule_key)
    if passed is not None:
        query = query.where(QualityResult.passed.is_(passed))
    if since:
        query = query.where(QualityResult.checked_at >= since)
    if until:
        query = query.where(QualityResult.checked_at < until)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = _results_out(db, query.limit(paging.limit).offset(paging.offset))
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)
