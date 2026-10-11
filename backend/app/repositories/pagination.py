from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


def paginate(session: Session, query: Select[Any], limit: int, offset: int) -> tuple[list, int]:
    """Run `query` with limit/offset and return (rows, total matching rows)."""
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = list(session.execute(query.limit(limit).offset(offset)).all())
    return rows, int(total)
