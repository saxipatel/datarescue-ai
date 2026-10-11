"""Batch-level quality evaluation, computed from persisted state.

Results are derived from quarantine/accepted rows rather than in-flight counters, so a
batch that was resumed after a crash produces the same numbers as an uninterrupted run.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    AcceptedTransaction,
    IngestionBatch,
    QualityResult,
    QualityRule,
    QuarantineRecord,
)
from app.models.base import has_any, has_key
from app.models.enums import BatchStatus, QuarantineState
from app.quality.catalog import evaluate_volume, threshold_passed
from app.quality.contract import codes_for_rule

SUCCESS_STATES = (BatchStatus.COMPLETE, BatchStatus.PARTIAL)
SAMPLE_SIZE = 5


def _quarantine_failures(
    session: Session, batch_id: uuid.UUID, rule_key: str, *, open_only: bool = False
) -> tuple[int, list[dict[str, str]]]:
    codes = codes_for_rule(rule_key)
    query = select(QuarantineRecord.id, QuarantineRecord.event_id).where(
        QuarantineRecord.batch_id == batch_id,
        has_any(QuarantineRecord.error_codes, codes),
    )
    if open_only:
        query = query.where(QuarantineRecord.state == QuarantineState.OPEN)
    count = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    sample = [
        {"quarantine_id": str(r.id), "event_id": r.event_id}
        for r in session.execute(query.order_by(QuarantineRecord.created_at).limit(SAMPLE_SIZE))
    ]
    return count, sample


def code_breakdown(session: Session, batch_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        text(
            "SELECT code, count(*) FROM quarantine_records q, "
            "jsonb_array_elements_text(q.error_codes) AS code "
            "WHERE q.batch_id = :b GROUP BY code ORDER BY count(*) DESC"
        ),
        {"b": batch_id},
    )
    return {code: int(n) for code, n in rows}


def unresolved_duplicates(session: Session, batch_id: uuid.UUID) -> int:
    """Identical duplicates not yet confirmed by an approved recovery, plus in-batch repeats."""
    return int(
        session.scalar(
            text(
                "SELECT COALESCE(SUM(CASE WHEN disposition = 'DUPLICATE_SKIPPED' "
                "THEN occurrences ELSE occurrences - 1 END), 0) "
                "FROM raw_events WHERE batch_id = :b AND NOT duplicate_confirmed"
            ),
            {"b": batch_id},
        )
        or 0
    )


def sequence_gaps(session: Session, batch: IngestionBatch) -> list[int]:
    if batch.sequence_number is None:
        return []
    seqs = set(
        session.scalars(
            select(IngestionBatch.sequence_number).where(
                IngestionBatch.source_id == batch.source_id,
                IngestionBatch.sequence_number.is_not(None),
                IngestionBatch.sequence_number <= batch.sequence_number,
            )
        )
    )
    known = {s for s in seqs if s is not None}
    if not known:
        return []
    return [n for n in range(min(known), batch.sequence_number) if n not in known]


def evaluate_rule(
    session: Session, batch: IngestionBatch, rule: QualityRule
) -> tuple[bool, int, int, dict[str, Any]] | None:
    """Return (passed, failed, checked, evidence) or None if the rule does not apply."""
    checked = batch.processed_count
    key = rule.rule_key
    config = rule.configuration

    if key in {
        "required_fields",
        "amount_range",
        "currency_allowlist",
        "enum_status",
        "schema_contract",
    }:
        failed, sample = _quarantine_failures(session, batch.id, key)
        evidence: dict[str, Any] = {"sample": sample}
        if key == "schema_contract":
            evidence["schema_versions"] = batch.details.get("schema_versions", {})
        return threshold_passed(failed, checked, config), failed, checked, evidence

    if key == "event_uniqueness":
        conflicts, sample = _quarantine_failures(session, batch.id, key)
        identical = batch.skipped_count
        failed = conflicts + identical
        evidence = {
            "identical_duplicates_skipped": identical,
            "conflicting_duplicates_quarantined": conflicts,
            "sample_conflicts": sample,
        }
        return threshold_passed(failed, checked, config), failed, checked, evidence

    if key == "late_event":
        late_query = select(AcceptedTransaction.event_id).where(
            AcceptedTransaction.batch_id == batch.id,
            has_key(AcceptedTransaction.warning_codes, "LATE_EVENT"),
        )
        late = session.scalar(select(func.count()).select_from(late_query.subquery())) or 0
        sample = list(session.scalars(late_query.limit(SAMPLE_SIZE)))
        return (
            threshold_passed(late, checked, config),
            late,
            checked,
            {"late_events": late, "sample_event_ids": sample},
        )

    if key == "completeness":
        gaps = sequence_gaps(session, batch)
        evidence = {
            "expected_count": batch.expected_count,
            "received": batch.received_count,
            "missing_sequences": gaps,
        }
        if batch.expected_count is None and batch.sequence_number is None:
            return None
        shortfall = 0
        if batch.expected_count is not None:
            min_ratio = float(config.get("min_ratio", 1.0))
            needed = int(batch.expected_count * min_ratio)
            shortfall = max(0, needed - batch.received_count)
        failed = shortfall + len(gaps)
        return failed == 0, failed, batch.expected_count or checked, evidence

    if key == "volume_anomaly":
        window = int(config.get("window", 10))
        history = list(
            session.scalars(
                select(IngestionBatch.received_count)
                .where(
                    IngestionBatch.source_id == batch.source_id,
                    IngestionBatch.id != batch.id,
                    IngestionBatch.status.in_(SUCCESS_STATES),
                )
                .order_by(IngestionBatch.finished_at.desc())
                .limit(window)
            )
        )
        verdict = evaluate_volume(batch.received_count, history, config)
        evidence = {
            "received": batch.received_count,
            "history": history,
            "baseline_median": verdict.baseline_median,
            "band": [verdict.lower, verdict.upper],
            "evaluated": verdict.evaluated,
            "reason": verdict.reason,
        }
        return verdict.passed, 0 if verdict.passed else 1, 1, evidence

    return None  # freshness is evaluated per source, not per batch


def evaluate_batch(session: Session, batch: IngestionBatch) -> list[QualityResult]:
    rules = session.scalars(
        select(QualityRule).where(QualityRule.enabled.is_(True)).order_by(QualityRule.rule_key)
    ).all()
    now = datetime.now(UTC)
    for rule in rules:
        outcome = evaluate_rule(session, batch, rule)
        if outcome is None:
            continue
        passed, failed, checked, evidence = outcome
        values = {
            "id": uuid.uuid4(),
            "batch_id": batch.id,
            "source_id": batch.source_id,
            "rule_id": rule.id,
            "rule_version": rule.version,
            "severity": rule.severity,
            "passed": passed,
            "failed_count": failed,
            "checked_count": checked,
            "evidence": evidence,
            "checked_at": now,
        }
        stmt = pg_insert(QualityResult).values(values)
        update_cols = {
            k: stmt.excluded[k] for k in values if k not in {"id", "batch_id", "rule_id"}
        }
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["batch_id", "rule_id"],
                index_where=QualityResult.batch_id.is_not(None),
                set_=update_cols,
            )
        )
    session.flush()
    return list(session.scalars(select(QualityResult).where(QualityResult.batch_id == batch.id)))


def accepted_checksum(session: Session, batch_id: uuid.UUID) -> str | None:
    """Order-independent digest of the batch's accepted rows (event_id:payload_hash)."""
    return session.scalar(
        text(
            "SELECT encode(sha256(convert_to(string_agg(event_id || ':' || payload_hash, ',' "
            "ORDER BY event_id), 'UTF8')), 'hex') FROM accepted_transactions WHERE batch_id = :b"
        ),
        {"b": batch_id},
    )


def decide_status(batch: IngestionBatch) -> BatchStatus:
    if batch.processed_count > 0 and batch.accepted_count == 0 and batch.rejected_count > 0:
        return BatchStatus.FAILED
    if batch.rejected_count > 0:
        return BatchStatus.PARTIAL
    return BatchStatus.COMPLETE
