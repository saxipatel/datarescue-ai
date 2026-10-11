"""Batch ingestion engine.

Algorithm per chunk (one DB transaction each):
  1. hash rows, derive missing event IDs, collapse identical repeats inside the chunk
  2. upsert raw_events (evidence is persisted before any validation decision)
  3. settle rows whose event_id is already accepted: identical payload -> skipped,
     different payload -> quarantined (DUPLICATE_CONFLICT)
  4. validate the remaining first occurrences in stream order (contract + business rules);
     only rows about to be accepted advance the event-time watermark
  5. insert valid rows with ON CONFLICT (event_id) DO NOTHING as a race-safe backstop
  6. write quarantine rows, raw dispositions and batch counters, advance the checkpoint

`processed_count` is the checkpoint: a re-run skips that many input rows, so a crash
between chunks never double counts. Every processed row lands in exactly one of
accepted / rejected (quarantined) / skipped; a CHECK constraint enforces this.
"""

import logging
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import islice
from typing import Any, cast

from sqlalchemy import Table, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    AcceptedTransaction,
    IngestionBatch,
    QualityRule,
    QuarantineRecord,
    RawEvent,
    SourceSystem,
)
from app.models.enums import BatchStatus, RawDisposition
from app.quality.contract import CORRUPT_MARKER, derive_event_id, payload_hash
from app.quality.validator import Issue, RuleSet, Watermark, validate_row

log = logging.getLogger(__name__)

# Core tables for executemany inserts (the ORM attribute is typed as a generic FromClause).
RAW_EVENTS = cast(Table, RawEvent.__table__)
ACCEPTED = cast(Table, AcceptedTransaction.__table__)
QUARANTINE = cast(Table, QuarantineRecord.__table__)


@dataclass
class ChunkStats:
    rows: int = 0
    accepted: int = 0
    rejected: int = 0
    skipped: int = 0
    warnings: int = 0
    schema_versions: dict[str, int] = field(default_factory=dict)


@dataclass
class _Pending:
    row_number: int
    event_id: str
    digest: str
    payload: dict[str, Any]
    occurrences: int = 1
    raw_id: uuid.UUID | None = None
    inserted: bool = True


def load_ruleset(session: Session, source: SourceSystem) -> RuleSet:
    rules = {r.rule_key: r for r in session.scalars(select(QualityRule))}

    def cfg(key: str) -> dict[str, Any]:
        rule = rules.get(key)
        return rule.configuration if rule else {}

    supported = cfg("schema_contract").get("supported_versions") or [source.contract_version]
    return RuleSet(
        allowed_currencies=frozenset(
            cfg("currency_allowlist").get("allowed_currencies", ["USD", "EUR", "GBP"])
        ),
        max_amount=Decimal(str(cfg("amount_range").get("max_amount", "50000.00"))),
        supported_schema_versions=frozenset(int(v) for v in supported),
        lateness_grace=timedelta(minutes=source.lateness_grace_minutes),
        reject_unexpected_fields=bool(cfg("schema_contract").get("reject_unexpected_fields", True)),
        disabled_rules=frozenset(k for k, r in rules.items() if not r.enabled),
    )


def _chunks(rows: Iterable[dict[str, Any]], size: int, start: int) -> Iterator[list[tuple]]:
    numbered = enumerate(rows)
    # Skip rows already committed by a previous attempt.
    for _ in islice(numbered, start):
        pass
    while chunk := list(islice(numbered, size)):
        yield chunk


def resume_watermark(session: Session, batch_id: uuid.UUID) -> Watermark:
    latest = session.scalar(
        select(func.max(AcceptedTransaction.event_time)).where(
            AcceptedTransaction.batch_id == batch_id
        )
    )
    return Watermark(latest)


def process_chunk(
    session: Session,
    batch: IngestionBatch,
    source: SourceSystem,
    rules: RuleSet,
    watermark: Watermark,
    chunk: list[tuple[int, dict[str, Any]]],
    *,
    now: datetime | None = None,
    recovery_job_id: uuid.UUID | None = None,
) -> ChunkStats:
    """Process one chunk inside the caller's transaction. Caller commits."""
    stats = ChunkStats(rows=len(chunk))
    pending: dict[tuple[str, str], _Pending] = {}
    for row_number, payload in chunk:
        digest = payload_hash(payload)
        raw_id = payload.get("event_id")
        if raw_id in (None, "") or CORRUPT_MARKER in payload:
            event_id = derive_event_id(source.name, digest)
        else:
            event_id = str(raw_id)[:100]
        key = (event_id, digest)
        if key in pending:
            # Identical repeat inside this chunk; counted as skipped once dispositions are known.
            pending[key].occurrences += 1
        else:
            pending[key] = _Pending(row_number, event_id, digest, payload)
        version = str(payload.get("schema_version", "unknown"))
        stats.schema_versions[version] = stats.schema_versions.get(version, 0) + 1

    items = list(pending.values())
    if not items:
        return stats

    # (2) Raw evidence; a conflict means the same row already appeared earlier in this batch.
    # Core executemany (insertmanyvalues) compiles once per statement shape and is cached,
    # which matters at 1,000 rows per chunk.
    raw_table = RAW_EVENTS
    insert_raw = pg_insert(raw_table)
    upsert_raw: Any = insert_raw.on_conflict_do_update(
        constraint="uq_raw_events_identity",
        set_={"occurrences": raw_table.c.occurrences + insert_raw.excluded.occurrences},
    ).returning(
        raw_table.c.id,
        raw_table.c.event_id,
        raw_table.c.payload_hash,
        # xmax = 0 only for freshly inserted tuples, not for ON CONFLICT updates.
        literal_column("(xmax = 0)").label("inserted"),
    )
    raw_rows = [
        {
            "id": uuid.uuid4(),
            "batch_id": batch.id,
            "row_number": p.row_number,
            "event_id": p.event_id,
            "payload": p.payload,
            "payload_hash": p.digest,
            "occurrences": p.occurrences,
            "disposition": RawDisposition.PENDING.value,
            "duplicate_confirmed": False,
            "received_at": datetime.now(UTC),
        }
        for p in items
    ]
    for row in session.connection().execute(upsert_raw, raw_rows):
        p = pending[(row.event_id, row.payload_hash)]
        p.raw_id, p.inserted = row.id, bool(row.inserted)

    dispositions: dict[uuid.UUID, str] = {}
    quarantine: list[dict[str, Any]] = []
    candidates: dict[str, tuple[_Pending, dict[str, Any], list[Issue]]] = {}

    def to_quarantine(p: _Pending, issues: list[Issue]) -> None:
        assert p.raw_id is not None
        dispositions[p.raw_id] = RawDisposition.QUARANTINED
        quarantine.append(
            {
                "id": uuid.uuid4(),
                "raw_event_id": p.raw_id,
                "batch_id": batch.id,
                "event_id": p.event_id,
                "error_codes": sorted({i.code for i in issues}),
                "error_details": [i.as_dict() for i in issues],
                "raw_payload": p.payload,
            }
        )
        stats.rejected += 1
        stats.skipped += p.occurrences - 1

    # (3) Settle rows whose event_id is already accepted (earlier batch or earlier in this
    # chunk) before validation, so a redelivered old event never advances the watermark.
    known: dict[str, str] = {
        eid: digest
        for eid, digest in session.execute(
            select(AcceptedTransaction.event_id, AcceptedTransaction.payload_hash).where(
                AcceptedTransaction.event_id.in_({p.event_id for p in items})
            )
        )
    }

    def settle_known_duplicate(p: _Pending) -> bool:
        digest = known.get(p.event_id)
        if digest is None:
            return False
        assert p.raw_id is not None
        if digest == p.digest:
            dispositions[p.raw_id] = RawDisposition.DUPLICATE_SKIPPED
            stats.skipped += p.occurrences
        else:
            to_quarantine(
                p, [Issue("DUPLICATE_CONFLICT", "event_id", "event_id accepted with other payload")]
            )
        return True

    # (4) Validation in stream order. Only rows about to be accepted advance the watermark.
    for p in sorted(items, key=lambda x: x.row_number):
        if not p.inserted:
            stats.skipped += p.occurrences
            continue
        if settle_known_duplicate(p):
            continue
        try:
            result = validate_row(p.payload, rules, watermark, now=now)
        except Exception as exc:  # poison row: quarantine instead of failing the batch
            log.warning("validator crashed on row", extra={"row": p.row_number, "err": str(exc)})
            to_quarantine(p, [Issue("CORRUPTED_PAYLOAD", None, f"validator error: {exc}")])
            continue
        if not result.ok:
            to_quarantine(p, result.errors)
            continue
        assert result.record is not None
        candidates[p.event_id] = (p, result.record, result.warnings)
        known[p.event_id] = p.digest
        watermark.observe(result.record["event_time"])

    # (5) Accepted rows. The unique index stays the final arbiter if another writer raced us.
    if candidates:
        insert_rows = [
            {
                "id": uuid.uuid4(),
                "event_id": event_id,
                **record,
                "source_id": source.id,
                "batch_id": batch.id,
                "raw_event_id": p.raw_id,
                "payload_hash": p.digest,
                "warning_codes": [w.code for w in warnings],
                "recovery_job_id": recovery_job_id,
                "created_at": datetime.now(UTC),
            }
            for event_id, (p, record, warnings) in candidates.items()
        ]
        accepted_table = ACCEPTED
        insert_accepted: Any = (
            pg_insert(accepted_table)
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(accepted_table.c.event_id)
        )
        inserted = set(session.connection().execute(insert_accepted, insert_rows).scalars())
        conflicts = [eid for eid in candidates if eid not in inserted]
        existing: dict[str, str] = {}
        if conflicts:
            existing = dict(
                session.execute(  # type: ignore[arg-type]
                    select(AcceptedTransaction.event_id, AcceptedTransaction.payload_hash).where(
                        AcceptedTransaction.event_id.in_(conflicts)
                    )
                ).all()
            )
        for event_id, (p, _record, warnings) in candidates.items():
            assert p.raw_id is not None
            if event_id in inserted:
                dispositions[p.raw_id] = RawDisposition.ACCEPTED
                stats.accepted += 1
                stats.skipped += p.occurrences - 1
                stats.warnings += 1 if warnings else 0
            elif existing.get(event_id) == p.digest:
                dispositions[p.raw_id] = RawDisposition.DUPLICATE_SKIPPED
                stats.skipped += p.occurrences
            else:
                to_quarantine(
                    p,
                    [
                        Issue(
                            "DUPLICATE_CONFLICT", "event_id", "Accepted row has a different payload"
                        )
                    ],
                )

    # (6) Quarantine rows and raw dispositions.
    if quarantine:
        session.connection().execute(
            pg_insert(QUARANTINE).on_conflict_do_nothing(index_elements=["raw_event_id"]),
            quarantine,
        )
    if dispositions:
        # One set-based UPDATE instead of an executemany of single-row updates.
        session.execute(
            text(
                "UPDATE raw_events AS r SET disposition = v.d "
                "FROM unnest(CAST(:ids AS uuid[]), CAST(:ds AS text[])) AS v(id, d) "
                "WHERE r.id = v.id"
            ),
            {"ids": list(dispositions), "ds": [str(d) for d in dispositions.values()]},
        )
    return stats


def apply_stats(session: Session, batch: IngestionBatch, stats: ChunkStats) -> None:
    """Advance counters and the checkpoint atomically with the chunk's writes."""
    session.execute(
        update(IngestionBatch)
        .where(IngestionBatch.id == batch.id)
        .values(
            processed_count=IngestionBatch.processed_count + stats.rows,
            received_count=IngestionBatch.received_count + stats.rows,
            accepted_count=IngestionBatch.accepted_count + stats.accepted,
            rejected_count=IngestionBatch.rejected_count + stats.rejected,
            skipped_count=IngestionBatch.skipped_count + stats.skipped,
            warning_count=IngestionBatch.warning_count + stats.warnings,
        )
    )


def ingest_rows(
    session: Session,
    batch: IngestionBatch,
    rows: Iterable[dict[str, Any]],
    *,
    chunk_size: int,
    on_chunk: Callable[[ChunkStats], None] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Stream rows through process_chunk, committing after each chunk."""
    source = session.get_one(SourceSystem, batch.source_id)
    rules = load_ruleset(session, source)
    watermark = resume_watermark(session, batch.id)
    versions: dict[str, int] = dict(batch.details.get("schema_versions", {}))

    if batch.status != BatchStatus.PROCESSING:
        batch.status = BatchStatus.PROCESSING
        batch.started_at = batch.started_at or datetime.now(UTC)
        session.commit()

    for chunk in _chunks(rows, chunk_size, batch.processed_count):
        stats = process_chunk(session, batch, source, rules, watermark, chunk, now=now)
        apply_stats(session, batch, stats)
        for key, count in stats.schema_versions.items():
            versions[key] = versions.get(key, 0) + count
        batch.details = {**batch.details, "schema_versions": versions}
        session.commit()
        session.refresh(batch)
        if on_chunk:
            on_chunk(stats)
    return {
        "processed": batch.processed_count,
        "accepted": batch.accepted_count,
        "rejected": batch.rejected_count,
        "skipped": batch.skipped_count,
    }
