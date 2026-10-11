from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import AuthenticationError, PermissionDenied, RateLimited
from app.models import User
from app.security.rbac import Permission, has_permission
from app.security.tokens import decode_access_token
from app.workers.queue import redis_connection

_bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def current_user(
    db: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise AuthenticationError("Missing bearer token")
    user = db.get(User, decode_access_token(credentials.credentials))
    if user is None or not user.active:
        raise AuthenticationError("User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require(permission: Permission) -> Callable[[User], User]:
    def checker(user: CurrentUser) -> User:
        if not has_permission(user.role, permission):
            raise PermissionDenied(
                f"Role '{user.role}' lacks permission '{permission}'",
                details={"required": permission},
            )
        return user

    return checker


def rate_limited(bucket: str) -> Callable[[User], User]:
    """Fixed-window limit per user for costly endpoints (simulation, AI diagnosis)."""

    def checker(user: CurrentUser) -> User:
        settings = get_settings()
        if settings.task_mode == "sync":
            return user  # tests and scripts run inline without Redis
        key = f"ratelimit:{bucket}:{user.id}"
        try:
            conn = redis_connection()
            count = conn.incr(key)
            if count == 1:
                conn.expire(key, 60)
        except RedisError:
            return user  # the endpoint's own enqueue will report Redis as unavailable
        if int(count) > settings.rate_limit_per_minute:
            raise RateLimited(
                f"Too many {bucket} requests; limit is {settings.rate_limit_per_minute}/minute"
            )
        return user

    return checker


@dataclass
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit, offset)


Paging = Annotated[Pagination, Depends(pagination)]
