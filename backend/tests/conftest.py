import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

# Configure before any app module reads settings.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ["TASK_MODE"] = "sync"
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="datarescue-test-"))
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://datarescue:datarescue@localhost:5432/datarescue_test"
)
os.environ.setdefault("JWT_SECRET", "test-only-secret-with-at-least-32-bytes")

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_engine, get_sessionmaker
from app.services.reference import load_reference_data

BACKEND = Path(__file__).resolve().parents[1]


def _database_available() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


@pytest.fixture(scope="session")
def migrated() -> None:
    if not _database_available():
        pytest.skip("PostgreSQL not available")
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
def db(migrated: None) -> Iterator[Session]:
    """A clean, seeded-with-reference-data database per test."""
    engine = get_engine()
    with engine.begin() as conn:
        tables = conn.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
        ).all()
        # TRUNCATE bypasses the append-only row trigger on audit_events by design.
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    session = get_sessionmaker()()
    load_reference_data(session)
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
