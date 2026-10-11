"""Dashboard statistics computed directly from operational tables."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _one(session: Session, sql: str, **params: Any) -> dict[str, Any]:
    row = session.execute(text(sql), params).mappings().one()
    return dict(row)


def _ratio(num: float | None, den: float | None) -> float | None:
    if not den:
        return None
    return round(float(num or 0) / float(den), 4)


def overview(session: Session) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=24)
    batches = _one(
        session,
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE created_at >= :since) AS last_24h, "
        "count(*) FILTER (WHERE status = 'COMPLETE') AS complete, "
        "count(*) FILTER (WHERE status = 'PARTIAL') AS partial, "
        "count(*) FILTER (WHERE status = 'FAILED') AS failed, "
        "count(*) FILTER (WHERE status IN ('PENDING','PROCESSING')) AS in_flight, "
        "COALESCE(sum(received_count),0) AS received, COALESCE(sum(accepted_count),0) AS accepted, "
        "COALESCE(sum(rejected_count),0) AS rejected, COALESCE(sum(skipped_count),0) AS skipped "
        "FROM ingestion_batches",
        since=since,
    )
    incidents = _one(
        session,
        "SELECT count(*) FILTER (WHERE state NOT IN ('RESOLVED','CLOSED_AS_FALSE_POSITIVE')) "
        "  AS open, "
        "count(*) FILTER (WHERE state NOT IN ('RESOLVED','CLOSED_AS_FALSE_POSITIVE') "
        "  AND severity = 'critical') AS open_critical, "
        "count(*) FILTER (WHERE state NOT IN ('RESOLVED','CLOSED_AS_FALSE_POSITIVE') "
        "  AND severity = 'high') AS open_high, "
        "count(*) FILTER (WHERE state NOT IN ('RESOLVED','CLOSED_AS_FALSE_POSITIVE') "
        "  AND severity = 'medium') AS open_medium, "
        "count(*) FILTER (WHERE state = 'RESOLVED') AS resolved, "
        "count(*) FILTER (WHERE state = 'CLOSED_AS_FALSE_POSITIVE') AS false_positives, "
        "(avg(extract(epoch FROM resolved_at - detected_at)) "
        "  FILTER (WHERE state = 'RESOLVED'))::float8 AS mean_duration_seconds "
        "FROM incidents",
    )
    mttd = _one(
        session,
        "SELECT avg(extract(epoch FROM i.detected_at - b.started_at))::float8 AS seconds, "
        "count(*) AS samples FROM incidents i JOIN ingestion_batches b ON b.id = i.batch_id "
        "WHERE b.started_at IS NOT NULL",
    )
    recoveries = _one(
        session,
        "SELECT count(*) FILTER (WHERE state = 'SUCCEEDED') AS succeeded, "
        "count(*) FILTER (WHERE state = 'FAILED') AS failed, "
        "count(*) FILTER (WHERE state IN ('QUEUED','RUNNING','VERIFYING')) AS active, "
        "(avg(extract(epoch FROM completed_at - started_at)) "
        "  FILTER (WHERE state = 'SUCCEEDED'))::float8 AS mean_duration_seconds "
        "FROM recovery_jobs",
    )
    recent = (
        session.execute(
            text(
                "SELECT r.id, r.state, r.attempts, r.completed_at, r.created_at, p.action, "
                "i.title AS incident_title, i.id AS incident_id FROM recovery_jobs r "
                "JOIN repair_proposals p ON p.id = r.proposal_id JOIN incidents i ON i.id = r.incident_id "
                "ORDER BY r.created_at DESC LIMIT 5"
            )
        )
        .mappings()
        .all()
    )
    open_quarantine = session.execute(
        text("SELECT count(*) FROM quarantine_records WHERE state = 'OPEN'")
    ).scalar()
    sources = (
        session.execute(
            text(
                "SELECT s.id, s.name, s.enabled, s.freshness_max_age_minutes, "
                "(SELECT max(finished_at) FROM ingestion_batches b WHERE b.source_id = s.id "
                "  AND b.status IN ('COMPLETE','PARTIAL')) AS last_success_at, "
                "(SELECT status FROM ingestion_batches b WHERE b.source_id = s.id "
                "  ORDER BY created_at DESC LIMIT 1) AS last_status, "
                "(SELECT count(*) FROM incidents i WHERE i.source_id = s.id "
                "  AND i.state NOT IN ('RESOLVED','CLOSED_AS_FALSE_POSITIVE')) AS open_incidents "
                "FROM source_systems s ORDER BY s.name"
            )
        )
        .mappings()
        .all()
    )
    simulations = _one(
        session,
        "SELECT count(*) AS runs, "
        "COALESCE(sum((evaluation->>'quarantine_false_positives')::int),0) AS false_positives, "
        "COALESCE(sum((evaluation->>'quarantine_false_negatives')::int),0) AS false_negatives, "
        "count(*) FILTER (WHERE (evaluation->>'counts_match')::boolean) AS counts_matched "
        "FROM simulation_runs WHERE evaluation ? 'actual'",
    )
    finished = batches["complete"] + batches["partial"] + batches["failed"]
    now = datetime.now(UTC)
    return {
        "generated_at": now,
        "batches": batches,
        "records": {
            "received": batches["received"],
            "accepted": batches["accepted"],
            "quarantined": batches["rejected"],
            "skipped": batches["skipped"],
            "open_quarantine": open_quarantine,
            "quarantine_rate": _ratio(batches["rejected"], batches["received"]),
        },
        "processing_success_rate": _ratio(batches["complete"] + batches["partial"], finished),
        "incidents": incidents,
        "mean_time_to_detect_seconds": mttd["seconds"],
        "mttd_samples": mttd["samples"],
        "recoveries": {
            **recoveries,
            "success_rate": _ratio(
                recoveries["succeeded"], recoveries["succeeded"] + recoveries["failed"]
            ),
            "recent": [dict(r) for r in recent],
        },
        "sources": [
            {
                **dict(s),
                "freshness": (
                    "no_history"
                    if s["last_success_at"] is None
                    else "stale"
                    if (now - s["last_success_at"]).total_seconds() / 60
                    > s["freshness_max_age_minutes"]
                    else "fresh"
                ),
            }
            for s in sources
        ],
        "simulations": simulations,
    }


def timeseries(session: Session, bucket: str, hours: int) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(
                "WITH b AS (SELECT date_trunc(:bucket, COALESCE(finished_at, created_at)) AS t, * "
                "  FROM ingestion_batches WHERE created_at >= now() - make_interval(hours => :hours)), "
                "i AS (SELECT date_trunc(:bucket, detected_at) AS t, count(*) AS n FROM incidents "
                "  WHERE detected_at >= now() - make_interval(hours => :hours) GROUP BY 1) "
                "SELECT COALESCE(b.t, i.t) AS bucket, "
                "  COALESCE(sum(b.received_count),0) AS received, "
                "  COALESCE(sum(b.accepted_count),0) AS accepted, "
                "  COALESCE(sum(b.rejected_count),0) AS quarantined, "
                "  COALESCE(sum(b.skipped_count),0) AS skipped, count(b.id) AS batches, "
                "  COALESCE(max(i.n),0) AS incidents_opened "
                "FROM b FULL OUTER JOIN i ON i.t = b.t GROUP BY 1 ORDER BY 1"
            ),
            {"bucket": bucket, "hours": hours},
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        item = dict(r)
        item["pass_rate"] = _ratio(item["accepted"], item["received"])
        out.append(item)
    return out


def revenue(session: Session) -> dict[str, Any]:
    """Read the dbt mart if it has been built; never synthesize numbers."""
    exists = session.execute(
        text("SELECT to_regclass('analytics.mart_daily_revenue') IS NOT NULL")
    ).scalar()
    if not exists:
        return {"available": False, "reason": "dbt marts not built (run `make dbt`)", "rows": []}
    rows = (
        session.execute(
            text(
                "SELECT revenue_date, currency, settled_amount, settled_count, refunded_amount, "
                "_refreshed_at FROM analytics.mart_daily_revenue ORDER BY revenue_date DESC, currency "
                "LIMIT 90"
            )
        )
        .mappings()
        .all()
    )
    return {"available": True, "rows": [dict(r) for r in rows]}
