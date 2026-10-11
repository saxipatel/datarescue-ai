from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DB, CurrentUser
from app.config import get_settings
from app.errors import AuthenticationError
from app.models import User
from app.models.enums import Role
from app.schemas.resources import LoginRequest, MeResponse, TokenResponse, UserOut
from app.security.passwords import verify_password
from app.security.rbac import ROLE_PERMISSIONS
from app.security.tokens import create_access_token
from app.services.audit import Actor, record_audit
from app.workers.queue import redis_ok

router = APIRouter()


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/ready", tags=["system"])
def ready(db: DB) -> JSONResponse:
    """Readiness: database and queue reachable. 503 if either is down."""
    checks: dict[str, Any] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    settings = get_settings()
    checks["queue"] = (
        "inline" if settings.task_mode == "sync" else ("ok" if redis_ok() else "error: unreachable")
    )
    ok = all(v in ("ok", "inline") for v in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )


@router.get("/system/info", tags=["system"])
def info(user: CurrentUser) -> dict[str, Any]:
    settings = get_settings()
    return {
        "environment": settings.environment,
        "task_mode": settings.task_mode,
        "llm": {
            "enabled": settings.llm_enabled,
            "active": settings.llm_active,
            "provider": settings.llm_provider if settings.llm_active else None,
            "model": settings.llm_model if settings.llm_active else None,
        },
        "chaos_enabled": settings.chaos_enabled,
        "integrations": {
            "kafka": "optional profile `optional-streaming`",
            "airflow": "optional profile `optional-orchestration`",
            "dbt": "optional profile `analytics`",
        },
    }


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(body: LoginRequest, db: DB) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Same error for unknown user and bad password to avoid account enumeration.
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        raise AuthenticationError("Invalid email or password")
    token, expires_at = create_access_token(user.id, user.role)
    record_audit(db, Actor.from_user(user), "auth.login", "user", user.id)
    db.commit()
    return TokenResponse(
        access_token=token, expires_at=expires_at, user=UserOut.model_validate(user)
    )


@router.get("/auth/me", response_model=MeResponse, tags=["auth"])
def me(user: CurrentUser) -> MeResponse:
    permissions = sorted(p.value for p in ROLE_PERMISSIONS[Role(user.role)])
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        permissions=permissions,
    )
