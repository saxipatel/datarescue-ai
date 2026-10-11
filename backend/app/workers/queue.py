"""Task dispatch. RQ in normal operation; inline execution in tests and scripts."""

import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Retry

from app.config import get_settings
from app.errors import ServiceUnavailable

log = logging.getLogger(__name__)
QUEUE_NAME = "datarescue"


@lru_cache
def redis_connection() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=5)


def redis_ok() -> bool:
    try:
        return bool(redis_connection().ping())
    except RedisError:
        return False


def enqueue(func: Callable[..., Any], *args: Any, retry: bool = True, **kwargs: Any) -> str | None:
    """Queue `func`. Raises ServiceUnavailable instead of silently dropping work."""
    settings = get_settings()
    if settings.task_mode == "sync":
        func(*args, **kwargs)
        return None
    try:
        queue = Queue(QUEUE_NAME, connection=redis_connection())
        # Exponential backoff between attempts; poison work ends in RQ's failed registry.
        policy = Retry(max=settings.task_max_retries, interval=[2, 8, 30]) if retry else None
        job = queue.enqueue_call(
            func=func,
            args=args,
            kwargs=kwargs,
            retry=policy,
            timeout=1800,
            result_ttl=3600,
            failure_ttl=7 * 24 * 3600,
        )
    except RedisError as exc:
        log.error("enqueue failed", extra={"task": func.__name__, "error": str(exc)})
        raise ServiceUnavailable(
            "Task queue unavailable; the request was not accepted", code="queue_unavailable"
        ) from exc
    return str(job.id)
