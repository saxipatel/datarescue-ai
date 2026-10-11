"""Incident lifecycle: creation from failed rules, evidence and state transitions."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.diagnosis.redaction import redact_payload
from app.errors import ConflictError, ValidationFailed
from app.models import (
    Incident,
    IncidentEvidence,
    IngestionBatch,
    PipelineRun,
    PipelineRunEvent,
    QualityResult,
    QualityRule,
    QuarantineRecord,
    SourceSystem,
)
from app.models.enums import IncidentState, Severity
from app.quality.catalog import INCIDENT_MIN_SEVERITY
from app.services.audit import Actor, record_audit
from app.services.quality import code_breakdown

S = IncidentState
TRANSITIONS: dict[IncidentState, set[IncidentState]] = {
    S.OPEN: {S.INVESTIGATING, S.ACKNOWLEDGED, S.CLOSED_AS_FALSE_POSITIVE},
    S.INVESTIGATING: {S.ACTION_REQUIRED, S.ACKNOWLEDGED, S.CLOSED_AS_FALSE_POSITIVE},
    S.ACTION_REQUIRED: {S.INVESTIGATING, S.RECOVERING, S.ACKNOWLEDGED, S.CLOSED_AS_FALSE_POSITIVE},
    S.ACKNOWLEDGED: {S.INVESTIGATING, S.ACTION_REQUIRED, S.RECOVERING, S.CLOSED_AS_FALSE_POSITIVE},
    # A failed recovery hands the incident back to a human.
    S.RECOVERING: {S.RESOLVED, S.ACTION_REQUIRED},
    S.RESOLVED: set(),
    S.CLOSED_AS_FALSE_POSITIVE: set(),
}
NEEDS_JUSTIFICATION = {S.ACKNOWLEDGED, S.CLOSED_AS_FALSE_POSITIVE}
TERMINAL = {S.RESOLVED, S.CLOSED_AS_FALSE_POSITIVE}

_TITLES = {
    "event_uniqueness": "Duplicate event IDs",
    "schema_contract": "Schema contract violations",
    "amount_range": "Invalid transaction amounts",
    "required_fields": "Missing required fields",
    "currency_allowlist": "Invalid currency codes",
    "enum_status": "Unknown transaction statuses",
    "completeness": "Incomplete batch delivery",
    "volume_anomaly": "Batch volume outside baseline",
    "late_event": "Late events above threshold",
    "freshness": "Source is stale",
}
# Where in the lineage graph each failure enters. Delivery problems start at the source.
_ORIGIN = {
    "completeness": "source_transactions",
    "volume_anomaly": "source_transactions",
    "freshness": "source_transactions",
}


def transition(
    session: Session,
    incident: Incident,
    target: IncidentState,
    actor: Actor,
    *,
    note: str | None = None,
    expected_version: int | None = None,
) -> Incident:
    current = IncidentState(incident.state)
    if expected_version is not None and expected_version != incident.version:
        raise ConflictError(
            "Incident was modified concurrently",
            details={"expected_version": expected_version, "actual_version": incident.version},
        )
    if target not in TRANSITIONS[current]:
        raise ConflictError(
            f"Invalid transition {current} -> {target}",
            code="invalid_transition",
            details={"from": current, "to": target, "allowed": sorted(TRANSITIONS[current])},
        )
    if target in NEEDS_JUSTIFICATION and not (note and note.strip()):
        raise ValidationFailed(f"A justification is required to move to {target}")
    before = {"state": incident.state, "version": incident.version}
    incident.state = target
    now = datetime.now(UTC)
    if target == S.ACKNOWLEDGED:
        incident.acknowledged_at = now
    if target in TERMINAL:
        incident.resolved_at = now
        incident.resolution_note = note
    session.flush()  # bumps version; raises StaleDataError on a concurrent update
    record_audit(
        session,
        actor,
        "incident.transition",
        "incident",
        incident.id,
        before=before,
        after={"state": incident.state, "version": incident.version},
        details={"from": before["state"], "to": target, "note": note},
    )
    return incident


def _primary_rule(
    failed: list[tuple[QualityResult, QualityRule]],
) -> tuple[QualityResult, QualityRule]:
    return max(failed, key=lambda fr: (Severity(fr[0].severity).rank, fr[0].failed_count))


def open_batch_incident(session: Session, batch: IngestionBatch) -> Incident | None:
    """Open (or return the existing) incident for a batch whose rules breached thresholds."""
    rows = session.execute(
        select(QualityResult, QualityRule)
        .join(QualityRule, QualityRule.id == QualityResult.rule_id)
        .where(QualityResult.batch_id == batch.id, QualityResult.passed.is_(False))
    ).all()
    failed = [
        (r, rule) for r, rule in rows if Severity(r.severity).rank >= INCIDENT_MIN_SEVERITY.rank
    ]
    if not failed:
        return None
    key = f"batch:{batch.id}"
    existing = session.scalar(select(Incident).where(Incident.incident_key == key))
    if existing:
        return existing  # finalize re-ran after a resume; evidence already captured

    source = session.get_one(SourceSystem, batch.source_id)
    _, primary_rule = _primary_rule(failed)
    severity = max((Severity(r.severity) for r, _ in failed), key=lambda s: s.rank)
    rule_keys = [rule.rule_key for _, rule in failed]
    title = (
        f"{_TITLES.get(primary_rule.rule_key, primary_rule.rule_key)} in "
        f"{source.name} batch {str(batch.id)[:8]}"
    )
    parts = [f"{rule.rule_key}: {r.failed_count}/{r.checked_count}" for r, rule in failed]
    summary = (
        f"{len(failed)} rule(s) breached thresholds ({'; '.join(parts)}). "
        f"Batch received {batch.received_count} rows: {batch.accepted_count} accepted, "
        f"{batch.rejected_count} quarantined, {batch.skipped_count} skipped."
    )
    incident = Incident(
        incident_key=key,
        category="data_quality" if primary_rule.rule_key not in _ORIGIN else "delivery",
        batch_id=batch.id,
        source_id=batch.source_id,
        origin_node_key=_ORIGIN.get(primary_rule.rule_key, "raw_events"),
        severity=severity,
        state=S.OPEN,
        title=title[:240],
        summary=summary,
        failed_rules=rule_keys,
    )
    session.add(incident)
    session.flush()
    _collect_batch_evidence(session, incident, batch, source, failed)
    record_audit(
        session,
        Actor.system("ingestion"),
        "incident.opened",
        "incident",
        incident.id,
        after={"state": incident.state, "severity": incident.severity},
        details={"batch_id": batch.id, "failed_rules": rule_keys},
    )
    return incident


def add_evidence(session: Session, incident: Incident, kind: str, payload: dict[str, Any]) -> None:
    session.add(IncidentEvidence(incident_id=incident.id, kind=kind, payload=payload))


def _collect_batch_evidence(
    session: Session,
    incident: Incident,
    batch: IngestionBatch,
    source: SourceSystem,
    failed: list[tuple[QualityResult, QualityRule]],
) -> None:
    for result, rule in failed:
        add_evidence(
            session,
            incident,
            "rule_failure",
            {
                "rule_key": rule.rule_key,
                "rule_version": result.rule_version,
                "severity": result.severity,
                "failed_count": result.failed_count,
                "checked_count": result.checked_count,
                "details": result.evidence,
            },
        )

    previous = session.execute(
        select(
            IngestionBatch.id,
            IngestionBatch.sequence_number,
            IngestionBatch.status,
            IngestionBatch.finished_at,
            IngestionBatch.received_count,
        )
        .where(
            IngestionBatch.source_id == batch.source_id,
            IngestionBatch.id != batch.id,
            IngestionBatch.created_at < batch.created_at,
        )
        .order_by(IngestionBatch.created_at.desc())
        .limit(5)
    ).all()
    add_evidence(
        session,
        incident,
        "batch_metadata",
        {
            "batch_id": str(batch.id),
            "source": source.name,
            "source_contract_version": source.contract_version,
            "sequence_number": batch.sequence_number,
            "input_format": batch.input_format,
            "expected_count": batch.expected_count,
            "received": batch.received_count,
            "accepted": batch.accepted_count,
            "rejected": batch.rejected_count,
            "skipped": batch.skipped_count,
            "late_warnings": batch.warning_count,
            "schema_versions_observed": batch.details.get("schema_versions", {}),
            "error_code_breakdown": code_breakdown(session, batch.id),
            "recent_batches": [
                {
                    "batch_id": str(p.id),
                    "sequence_number": p.sequence_number,
                    "status": p.status,
                    "finished_at": p.finished_at.isoformat() if p.finished_at else None,
                    "received": p.received_count,
                }
                for p in previous
            ],
        },
    )

    if "event_uniqueness" in incident.failed_rules:
        origins = session.execute(
            text(
                "SELECT a.batch_id, b.sequence_number, count(*) AS n "
                "FROM raw_events r JOIN accepted_transactions a ON a.event_id = r.event_id "
                "JOIN ingestion_batches b ON b.id = a.batch_id "
                "WHERE r.batch_id = :b AND r.disposition = 'DUPLICATE_SKIPPED' "
                "AND a.batch_id <> :b GROUP BY a.batch_id, b.sequence_number ORDER BY n DESC"
            ),
            {"b": batch.id},
        ).all()
        in_batch = session.scalar(
            text("SELECT COALESCE(SUM(occurrences - 1), 0) FROM raw_events WHERE batch_id = :b"),
            {"b": batch.id},
        )
        add_evidence(
            session,
            incident,
            "duplicate_analysis",
            {
                "originals_in_previous_batches": [
                    {
                        "batch_id": str(o.batch_id),
                        "sequence_number": o.sequence_number,
                        "count": int(o.n),
                    }
                    for o in origins
                ],
                "cross_batch_duplicates": sum(int(o.n) for o in origins),
                "in_batch_repeats": int(in_batch or 0),
            },
        )

    samples = session.scalars(
        select(QuarantineRecord)
        .where(QuarantineRecord.batch_id == batch.id)
        .order_by(QuarantineRecord.created_at)
        .limit(5)
    ).all()
    if samples:
        add_evidence(
            session,
            incident,
            "sample_records",
            {
                "records": [
                    {
                        "quarantine_id": str(q.id),
                        "event_id": q.event_id,
                        "error_codes": q.error_codes,
                        "payload": redact_payload(q.raw_payload),
                    }
                    for q in samples
                ]
            },
        )

    log_lines = session.execute(
        select(
            PipelineRunEvent.level,
            PipelineRunEvent.message,
            PipelineRunEvent.context,
            PipelineRunEvent.created_at,
            PipelineRun.attempts,
        )
        .join(PipelineRun, PipelineRun.id == PipelineRunEvent.run_id)
        .where(PipelineRun.batch_id == batch.id)
        .order_by(PipelineRunEvent.id.desc())
        .limit(20)
    ).all()
    if log_lines:
        add_evidence(
            session,
            incident,
            "pipeline_log",
            {
                "lines": [
                    {
                        "level": e.level,
                        "message": e.message,
                        "context": e.context,
                        "at": e.created_at.isoformat(),
                        "run_attempt": e.attempts,
                    }
                    for e in reversed(log_lines)
                ]
            },
        )


def open_freshness_incident(
    session: Session, source: SourceSystem, evidence: dict[str, Any], severity: str
) -> Incident:
    """One open freshness incident per source; repeated checks append evidence."""
    existing = session.scalar(
        select(Incident).where(
            Incident.source_id == source.id,
            Incident.category == "freshness",
            Incident.state.not_in([s.value for s in TERMINAL]),
        )
    )
    if existing:
        add_evidence(session, existing, "freshness_check", evidence)
        return existing
    incident = Incident(
        incident_key=f"freshness:{source.name}:{uuid.uuid4().hex[:8]}",
        category="freshness",
        source_id=source.id,
        origin_node_key="source_transactions",
        severity=severity,
        state=S.OPEN,
        title=f"Source is stale: {source.name}",
        summary=(
            f"No successful batch from {source.name} for "
            f"{evidence.get('age_minutes')} minutes (limit {evidence.get('max_age_minutes')})."
        ),
        failed_rules=["freshness"],
    )
    session.add(incident)
    session.flush()
    add_evidence(session, incident, "freshness_check", evidence)
    record_audit(
        session,
        Actor.system("scheduler"),
        "incident.opened",
        "incident",
        incident.id,
        after={"state": incident.state},
        details={"source": source.name, **evidence},
    )
    return incident
