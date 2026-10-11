"""Target resolution, previews and dry runs for repair actions.

Targets are always resolved server-side from the incident's batch and bounded by
`recovery_max_targets`. Clients can narrow a target set (by IDs or error codes) but can
never widen it, pass SQL, or touch rows outside the incident's batch.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.datagen.generator import iter_events
from app.datagen.source import spec_for_sequence
from app.errors import ConflictError, ValidationFailed
from app.models import (
    AcceptedTransaction,
    Incident,
    IngestionBatch,
    QuarantineRecord,
    RawEvent,
    SourceSystem,
)
from app.models.base import has_any
from app.models.enums import QuarantineState, RawDisposition, RepairAction, Severity
from app.quality.contract import payload_hash
from app.quality.validator import RuleSet, Watermark, normalize_currency_case, validate_row
from app.services.ingestion import load_ruleset, resume_watermark
from app.services.quality import sequence_gaps

PREVIEW_LIMIT = 20
A = RepairAction


@dataclass
class ReplayOutcome:
    kind: str  # insert | already_present | conflict | invalid
    digest: str
    record: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def evaluate_replay(
    session: Session,
    payload: dict[str, Any],
    event_id: str,
    rules: RuleSet,
    watermark: Watermark,
) -> ReplayOutcome:
    """Decide what replaying one payload would do, without writing anything."""
    digest = payload_hash(payload)
    result = validate_row(payload, rules, watermark)
    if not result.ok:
        return ReplayOutcome("invalid", digest, errors=sorted({e.code for e in result.errors}))
    existing = session.scalar(
        select(AcceptedTransaction.payload_hash).where(AcceptedTransaction.event_id == event_id)
    )
    if existing is None:
        return ReplayOutcome(
            "insert", digest, record=result.record, warnings=[w.code for w in result.warnings]
        )
    return ReplayOutcome("already_present" if existing == digest else "conflict", digest)


def _bounded(ids: list[Any]) -> list[Any]:
    limit = get_settings().recovery_max_targets
    if len(ids) > limit:
        raise ValidationFailed(
            f"Target set has {len(ids)} records; the limit is {limit}. Narrow the selection.",
            code="target_limit_exceeded",
        )
    return ids


def _open_quarantine(
    session: Session, batch_id: uuid.UUID, requested: dict[str, Any]
) -> list[QuarantineRecord]:
    query = select(QuarantineRecord).where(
        QuarantineRecord.batch_id == batch_id, QuarantineRecord.state == QuarantineState.OPEN
    )
    if ids := requested.get("quarantine_ids"):
        query = query.where(QuarantineRecord.id.in_([uuid.UUID(str(i)) for i in ids]))
    if codes := requested.get("error_codes"):
        query = query.where(has_any(QuarantineRecord.error_codes, list(codes)))
    return list(session.scalars(query.order_by(QuarantineRecord.created_at)))


def currency_candidates(
    session: Session, batch_id: uuid.UUID, rules: RuleSet, requested: dict[str, Any] | None = None
) -> list[tuple[QuarantineRecord, dict[str, Any]]]:
    """Open records whose only problem is currency case and that fully validate once fixed."""
    out = []
    for q in _open_quarantine(session, batch_id, requested or {}):
        if q.error_codes != ["CURRENCY_INVALID_FORMAT"]:
            continue
        candidate = normalize_currency_case(q.raw_payload, rules.allowed_currencies)
        if candidate is None:
            continue
        if validate_row(candidate, rules, Watermark()).ok:
            out.append((q, candidate))
    return out


def duplicate_targets(session: Session, batch_id: uuid.UUID) -> list[RawEvent]:
    return list(
        session.scalars(
            select(RawEvent)
            .where(
                RawEvent.batch_id == batch_id,
                RawEvent.duplicate_confirmed.is_(False),
                (RawEvent.disposition == RawDisposition.DUPLICATE_SKIPPED)
                | (RawEvent.occurrences > 1),
            )
            .order_by(RawEvent.row_number)
        )
    )


def _risk(action: RepairAction, count: int) -> Severity:
    base = {
        A.SKIP_IDENTICAL_DUPLICATE: Severity.LOW,
        A.MANUAL_REVIEW_REQUIRED: Severity.LOW,
        A.NORMALIZE_CURRENCY_CASE: Severity.MEDIUM,
        A.REPLAY_QUARANTINED: Severity.MEDIUM,
        A.RETRY_MISSING_BATCH: Severity.MEDIUM,
    }[action]
    # Larger writes to business data need a second person.
    if action in {A.REPLAY_QUARANTINED, A.NORMALIZE_CURRENCY_CASE, A.RETRY_MISSING_BATCH} and (
        count > 1000
    ):
        return Severity.HIGH
    return base


def resolve(
    session: Session, incident: Incident, action: RepairAction, requested: dict[str, Any]
) -> tuple[dict[str, Any], int, dict[str, Any], Severity]:
    """Return (target, target_count, preview_patch, risk) for a proposal."""
    if incident.batch_id is None:
        raise ValidationFailed("This incident is not tied to a batch; no repair targets exist")
    batch = session.get_one(IngestionBatch, incident.batch_id)
    source = session.get_one(SourceSystem, batch.source_id)
    rules = load_ruleset(session, source)

    if action == A.SKIP_IDENTICAL_DUPLICATE:
        raws = duplicate_targets(session, batch.id)
        ids = _bounded([str(r.id) for r in raws])
        preview = {
            "kind": "duplicate_confirmation",
            "rows": [
                {
                    "raw_event_id": str(r.id),
                    "event_id": r.event_id,
                    "occurrences": r.occurrences,
                    "disposition": r.disposition,
                }
                for r in raws[:PREVIEW_LIMIT]
            ],
        }
        return (
            {"batch_id": str(batch.id), "raw_event_ids": ids},
            len(ids),
            preview,
            _risk(action, len(ids)),
        )

    if action == A.NORMALIZE_CURRENCY_CASE:
        pairs = currency_candidates(session, batch.id, rules, requested)
        ids = _bounded([str(q.id) for q, _ in pairs])
        preview = {
            "kind": "field_patch",
            "field": "currency",
            "rows": [
                {
                    "quarantine_id": str(q.id),
                    "event_id": q.event_id,
                    "before": q.raw_payload.get("currency"),
                    "after": c["currency"],
                }
                for q, c in pairs[:PREVIEW_LIMIT]
            ],
        }
        return (
            {"batch_id": str(batch.id), "quarantine_ids": ids},
            len(ids),
            preview,
            _risk(action, len(ids)),
        )

    if action in {A.REPLAY_QUARANTINED, A.MANUAL_REVIEW_REQUIRED}:
        records = _open_quarantine(session, batch.id, requested)
        ids = _bounded([str(q.id) for q in records])
        preview = {
            "kind": "replay" if action == A.REPLAY_QUARANTINED else "state_change",
            "rows": [
                {"quarantine_id": str(q.id), "event_id": q.event_id, "error_codes": q.error_codes}
                for q in records[:PREVIEW_LIMIT]
            ],
        }
        return (
            {"batch_id": str(batch.id), "quarantine_ids": ids},
            len(ids),
            preview,
            _risk(action, len(ids)),
        )

    if action == A.RETRY_MISSING_BATCH:
        gaps = sequence_gaps(session, batch)
        wanted = requested.get("sequence_numbers") or gaps
        if not wanted or any(n not in gaps for n in wanted):
            raise ValidationFailed(
                "Only sequence numbers detected as missing can be redelivered",
                details={"missing_sequences": gaps, "requested": wanted},
            )
        generation = batch.details.get("generation")
        if not generation or batch.sequence_number is None:
            # Real sources would need a redelivery request to the producer; only the
            # synthetic source can regenerate a batch deterministically.
            raise ValidationFailed("Redelivery is only available for synthetic sequenced batches")
        anchor = datetime.fromisoformat(generation["anchor"])
        span = timedelta(minutes=int(source.settings.get("span_minutes", 60)))
        deliveries = []
        for seq in sorted(wanted):
            # Earlier sequences cover earlier time windows.
            seq_anchor = anchor - span * (batch.sequence_number - seq)
            # Size the redelivery like its neighbour; the producer's batch size is stable.
            spec = spec_for_sequence(source, seq, seq_anchor, records=int(generation["records"]))
            deliveries.append({"sequence_number": seq, **spec.as_params()})
        count = sum(d["records"] for d in deliveries)
        preview = {"kind": "redelivery", "deliveries": deliveries}
        target = {"batch_id": str(batch.id), "source_id": str(source.id), "deliveries": deliveries}
        return target, count, preview, _risk(action, count)

    raise ValidationFailed(f"Unsupported action {action}")


def dry_run(session: Session, action: RepairAction, target: dict[str, Any]) -> dict[str, Any]:
    """Expected effect of executing the proposal now. Never writes business data."""
    batch = session.get_one(IngestionBatch, uuid.UUID(target["batch_id"]))
    source = session.get_one(SourceSystem, batch.source_id)
    rules = load_ruleset(session, source)
    counts = {
        "would_insert": 0,
        "would_update": 0,
        "would_skip": 0,
        "would_remain_quarantined": 0,
        "blocked": 0,
    }
    notes: list[str] = []

    if action == A.SKIP_IDENTICAL_DUPLICATE:
        ids = [uuid.UUID(i) for i in target["raw_event_ids"]]
        rows = session.execute(
            text(
                "SELECT r.id, r.disposition, r.duplicate_confirmed, "
                "EXISTS (SELECT 1 FROM accepted_transactions a WHERE a.event_id = r.event_id "
                "AND a.payload_hash = r.payload_hash) AS hash_match "
                "FROM raw_events r WHERE r.id = ANY(:ids)"
            ),
            {"ids": ids},
        ).all()
        for r in rows:
            if r.duplicate_confirmed:
                counts["would_skip"] += 1
            elif r.hash_match:
                counts["would_update"] += 1
            else:
                counts["blocked"] += 1
        notes.append("Marks duplicates as confirmed; accepted_transactions is not modified.")

    elif action in {A.REPLAY_QUARANTINED, A.NORMALIZE_CURRENCY_CASE}:
        ids = [uuid.UUID(i) for i in target["quarantine_ids"]]
        watermark = resume_watermark(session, batch.id)
        for q in session.scalars(select(QuarantineRecord).where(QuarantineRecord.id.in_(ids))):
            if q.state != QuarantineState.OPEN:
                counts["would_skip"] += 1
                continue
            payload = q.raw_payload
            if action == A.NORMALIZE_CURRENCY_CASE:
                payload = normalize_currency_case(payload, rules.allowed_currencies) or payload
            outcome = evaluate_replay(session, payload, q.event_id, rules, watermark)
            if outcome.kind == "insert":
                counts["would_insert"] += 1
            elif outcome.kind == "already_present":
                counts["would_skip"] += 1
            else:
                counts["would_remain_quarantined"] += 1
        if action == A.REPLAY_QUARANTINED and counts["would_insert"] == 0:
            notes.append("No record passes current rules; replay would change nothing.")

    elif action == A.MANUAL_REVIEW_REQUIRED:
        ids = [uuid.UUID(i) for i in target["quarantine_ids"]]
        states = session.scalars(
            select(QuarantineRecord.state).where(QuarantineRecord.id.in_(ids))
        ).all()
        counts["would_update"] = sum(1 for s in states if s == QuarantineState.OPEN)
        counts["would_skip"] = len(states) - counts["would_update"]
        notes.append("Records stay quarantined and are flagged for manual review.")

    elif action == A.RETRY_MISSING_BATCH:
        for delivery in target["deliveries"]:
            spec = spec_for_sequence(
                source,
                delivery["sequence_number"],
                datetime.fromisoformat(delivery["anchor"]),
                records=int(delivery["records"]),
            )
            event_ids = [p["event_id"] for p, _ in iter_events(spec) if "event_id" in p]
            present = set(
                session.scalars(
                    select(AcceptedTransaction.event_id).where(
                        AcceptedTransaction.event_id.in_(event_ids)
                    )
                )
            )
            counts["would_insert"] += len(event_ids) - len(present)
            counts["would_skip"] += len(present)
        notes.append("Redelivery is ingested as a normal batch with all quality rules applied.")
    else:
        raise ConflictError(f"No dry run for {action}")

    return {
        "action": action,
        "counts": counts,
        "notes": notes,
        "target_count": _target_count(target),
    }


def _target_count(target: dict[str, Any]) -> int:
    for key in ("raw_event_ids", "quarantine_ids"):
        if key in target:
            return len(target[key])
    return sum(d["records"] for d in target.get("deliveries", []))


def repairability(session: Session, incident: Incident) -> dict[str, Any]:
    """Counts of records each action could address; used as diagnosis evidence."""
    if incident.batch_id is None:
        return {}
    batch = session.get_one(IngestionBatch, incident.batch_id)
    source = session.get_one(SourceSystem, batch.source_id)
    rules = load_ruleset(session, source)
    fields = session.execute(
        text(
            "SELECT d->>'field' AS field, count(*) AS n FROM quarantine_records q, "
            "jsonb_array_elements(q.error_details) d WHERE q.batch_id = :b "
            "AND d->>'code' = 'MISSING_REQUIRED_FIELD' GROUP BY 1"
        ),
        {"b": batch.id},
    ).all()
    open_count = session.scalar(
        select(text("count(*)"))
        .select_from(QuarantineRecord)
        .where(
            QuarantineRecord.batch_id == batch.id, QuarantineRecord.state == QuarantineState.OPEN
        )
    )
    return {
        "open_quarantine": int(open_count or 0),
        "normalize_currency_candidates": len(currency_candidates(session, batch.id, rules)),
        "unresolved_duplicate_rows": len(duplicate_targets(session, batch.id)),
        "missing_sequences": sequence_gaps(session, batch),
        "field_breakdown": {f: int(n) for f, n in fields},
    }
