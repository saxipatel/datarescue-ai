"""Idempotent loaders for reference data: rules, lineage, sources and demo users."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.lineage.service import DEFAULT_EDGES, DEFAULT_NODES
from app.models import LineageEdge, LineageNode, QualityRule, SourceSystem, User
from app.models.enums import Role
from app.quality.catalog import DEFAULT_RULES
from app.security.passwords import hash_password

# Development-only credentials, documented in README and docs/security.md.
DEMO_PASSWORD = "datarescue-demo"  # noqa: S105 - documented local demo credential
DEMO_USERS = [
    ("engineer@example.test", "Demo Engineer", Role.ENGINEER),
    ("steward@example.test", "Demo Steward", Role.STEWARD),
    ("admin@example.test", "Demo Admin", Role.ADMIN),
    ("analyst@example.test", "Demo Analyst", Role.ANALYST),
]
DEFAULT_SOURCES = [
    {
        "name": "payments_core",
        "description": "Synthetic card-present and e-commerce transaction events",
        "settings": {"base_seed": 42, "records_per_batch": 1000, "span_minutes": 60},
    },
    {
        "name": "partner_ledger",
        "description": "Synthetic partner settlement feed (smaller batches)",
        "settings": {"base_seed": 7, "records_per_batch": 400, "span_minutes": 60},
    },
]


def load_rules(session: Session) -> None:
    for rule in DEFAULT_RULES:
        session.execute(
            pg_insert(QualityRule)
            .values(**rule, enabled=True, version=1)
            .on_conflict_do_nothing(index_elements=["rule_key"])
        )


def load_lineage(session: Session) -> None:
    for key, node_type, name, description in DEFAULT_NODES:
        session.execute(
            pg_insert(LineageNode)
            .values(
                node_key=key,
                node_type=node_type,
                display_name=name,
                node_metadata={"description": description},
            )
            .on_conflict_do_nothing(index_elements=["node_key"])
        )
    session.flush()
    ids = {
        key: node_id
        for key, node_id in session.execute(select(LineageNode.node_key, LineageNode.id))
    }
    for up, down in DEFAULT_EDGES:
        session.execute(
            pg_insert(LineageEdge)
            .values(upstream_node_id=ids[up], downstream_node_id=ids[down], relation_type="feeds")
            .on_conflict_do_nothing(constraint="uq_lineage_edges")
        )


def load_sources(session: Session) -> None:
    for source in DEFAULT_SOURCES:
        session.execute(
            pg_insert(SourceSystem)
            .values(**source, contract_version=1, enabled=True)
            .on_conflict_do_nothing(index_elements=["name"])
        )


def load_users(session: Session, password: str = DEMO_PASSWORD) -> None:
    existing = set(session.scalars(select(User.email)))
    hashed = hash_password(password)
    for email, name, role in DEMO_USERS:
        if email not in existing:
            session.add(User(email=email, full_name=name, role=role, password_hash=hashed))


def load_reference_data(session: Session) -> None:
    load_rules(session)
    load_lineage(session)
    load_sources(session)
    load_users(session)
    session.flush()
