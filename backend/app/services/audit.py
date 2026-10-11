import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.logging_config import correlation_id_var
from app.models import AuditEvent, User


@dataclass(frozen=True)
class Actor:
    """Who performed an action: a signed-in user or a named system component."""

    actor_id: str
    role: str | None = None
    user_id: Any = None

    @classmethod
    def from_user(cls, user: User) -> "Actor":
        return cls(actor_id=str(user.id), role=user.role, user_id=user.id)

    @classmethod
    def system(cls, component: str) -> "Actor":
        return cls(actor_id=f"system:{component}", role="system")


def canonical_hash(value: Any) -> str | None:
    if value is None:
        return None
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def record_audit(
    session: Session,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: Any,
    *,
    before: Any = None,
    after: Any = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event in the caller's transaction so it commits atomically with the change."""
    event = AuditEvent(
        actor_id=actor.actor_id,
        actor_role=actor.role,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before_hash=canonical_hash(before),
        after_hash=canonical_hash(after),
        details=json.loads(json.dumps(details or {}, default=str)),
        correlation_id=correlation_id_var.get(),
    )
    session.add(event)
    return event
