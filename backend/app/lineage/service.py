from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, aliased

from app.lineage.graph import downstream
from app.models import AcceptedTransaction, Incident, IngestionBatch, LineageEdge, LineageNode
from app.models.enums import BatchStatus

# node_key, node_type, display name, description
DEFAULT_NODES: list[tuple[str, str, str, str]] = [
    (
        "source_transactions",
        "source",
        "Source: transaction events",
        "Upstream producers delivering batches",
    ),
    ("raw_events", "table", "raw_events", "Every received row, kept for traceability"),
    ("accepted_transactions", "table", "accepted_transactions", "Validated business records"),
    ("stg_accepted_transactions", "dbt_model", "stg_accepted_transactions", "dbt staging view"),
    ("mart_daily_revenue", "dbt_model", "mart_daily_revenue", "Daily settled revenue by currency"),
    (
        "mart_merchant_settlements",
        "dbt_model",
        "mart_merchant_settlements",
        "Per-merchant settlement totals",
    ),
    ("dashboard_revenue", "dashboard", "Revenue dashboard", "KPI: daily settled revenue"),
    ("dashboard_settlement", "dashboard", "Settlement dashboard", "KPI: merchant payouts"),
]
DEFAULT_EDGES: list[tuple[str, str]] = [
    ("source_transactions", "raw_events"),
    ("raw_events", "accepted_transactions"),
    ("accepted_transactions", "stg_accepted_transactions"),
    ("stg_accepted_transactions", "mart_daily_revenue"),
    ("stg_accepted_transactions", "mart_merchant_settlements"),
    ("mart_daily_revenue", "dashboard_revenue"),
    ("mart_merchant_settlements", "dashboard_settlement"),
]
_MART_TABLES = ("mart_daily_revenue", "mart_merchant_settlements")


def edge_keys(session: Session) -> list[tuple[str, str]]:
    up, down = aliased(LineageNode), aliased(LineageNode)
    rows = session.execute(
        select(up.node_key, down.node_key)
        .join(LineageEdge, LineageEdge.upstream_node_id == up.id)
        .join(down, LineageEdge.downstream_node_id == down.id)
    ).all()
    return [(a, b) for a, b in rows]


def last_refresh(session: Session) -> dict[str, datetime | None]:
    """Observed refresh times. Unknown stays None rather than being guessed."""
    out: dict[str, datetime | None] = {}
    out["raw_events"] = session.scalar(select(func.max(IngestionBatch.finished_at)))
    out["accepted_transactions"] = session.scalar(select(func.max(AcceptedTransaction.created_at)))
    out["source_transactions"] = session.scalar(
        select(func.max(IngestionBatch.finished_at)).where(
            IngestionBatch.status.in_([BatchStatus.COMPLETE, BatchStatus.PARTIAL])
        )
    )
    # dbt marts carry a _refreshed_at column; read it only if dbt has run.
    # The staging model is a view, so it has no refresh time of its own.
    out["stg_accepted_transactions"] = None
    for table in _MART_TABLES:
        exists = session.scalar(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"analytics.{table}"}
        )
        out[table] = (
            session.scalar(text(f"SELECT max(_refreshed_at) FROM analytics.{table}"))  # noqa: S608
            if exists
            else None
        )
    return out


def graph(session: Session) -> dict[str, Any]:
    nodes = session.scalars(select(LineageNode).order_by(LineageNode.node_key)).all()
    refreshed = last_refresh(session)
    return {
        "nodes": [
            {
                "node_key": n.node_key,
                "node_type": n.node_type,
                "display_name": n.display_name,
                "metadata": n.node_metadata,
                "last_refreshed_at": refreshed.get(n.node_key),
            }
            for n in nodes
        ],
        "edges": [{"upstream": a, "downstream": b} for a, b in edge_keys(session)],
    }


def incident_impact(session: Session, incident: Incident) -> dict[str, Any]:
    edges = edge_keys(session)
    known = {a for a, _ in edges} | {b for _, b in edges}
    if incident.origin_node_key not in known:
        # No declared lineage: report unknown impact instead of implying "no impact".
        return {
            "origin": incident.origin_node_key,
            "status": "unknown",
            "impacted": [],
            "note": "Origin node has no declared lineage; downstream impact is unknown.",
        }
    refreshed = last_refresh(session)
    impacted = downstream(edges, incident.origin_node_key)
    return {
        "origin": incident.origin_node_key,
        "status": "computed",
        "granularity": "dataset",
        "impacted": [
            {
                "node_key": n.node_key,
                "depth": n.depth,
                "path": list(n.path),
                "confidence": n.confidence,
                "last_refreshed_at": refreshed.get(n.node_key),
            }
            for n in impacted
        ],
        "note": "Dataset-level lineage from declared dependencies; not row-level.",
    }
