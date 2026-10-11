import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Incident, IngestionBatch, QualityResult, QualityRule, SourceSystem
from app.models.enums import BatchStatus
from app.services.incidents import open_freshness_incident


def check_freshness(
    session: Session,
    *,
    as_of: datetime | None = None,
    source_ids: list[uuid.UUID] | None = None,
    simulated: bool = False,
) -> list[dict[str, Any]]:
    """Evaluate the freshness rule per enabled source; open incidents for stale sources.

    `as_of` lets the failure lab advance the clock instead of waiting a day. Sources that
    have never delivered are reported as `no_history`, not as stale.
    """
    rule = session.scalar(select(QualityRule).where(QualityRule.rule_key == "freshness"))
    if rule is None or not rule.enabled:
        return []
    now = as_of or datetime.now(UTC)
    query = select(SourceSystem).where(SourceSystem.enabled.is_(True))
    if source_ids:
        query = query.where(SourceSystem.id.in_(source_ids))
    results = []
    for source in session.scalars(query):
        last = session.scalar(
            select(func.max(IngestionBatch.finished_at)).where(
                IngestionBatch.source_id == source.id,
                IngestionBatch.status.in_([BatchStatus.COMPLETE, BatchStatus.PARTIAL]),
            )
        )
        max_age = int(rule.configuration.get("max_age_minutes", source.freshness_max_age_minutes))
        evidence: dict[str, Any] = {
            "source": source.name,
            "as_of": now.isoformat(),
            "last_success_at": last.isoformat() if last else None,
            "max_age_minutes": max_age,
            "simulated_clock": simulated,
        }
        if last is None:
            evidence["status"] = "no_history"
            results.append(evidence)
            continue
        age = int((now - last).total_seconds() // 60)
        passed = age <= max_age
        evidence.update(age_minutes=age, status="fresh" if passed else "stale")
        session.add(
            QualityResult(
                source_id=source.id,
                rule_id=rule.id,
                rule_version=rule.version,
                severity=rule.severity,
                passed=passed,
                failed_count=0 if passed else 1,
                checked_count=1,
                evidence=evidence,
                checked_at=datetime.now(UTC),
            )
        )
        if not passed:
            incident: Incident = open_freshness_incident(session, source, evidence, rule.severity)
            evidence["incident_id"] = str(incident.id)
        results.append(evidence)
    session.flush()
    return results
