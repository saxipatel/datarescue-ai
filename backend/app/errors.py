from typing import Any


class AppError(Exception):
    """Domain error rendered as {code, message, details, correlation_id}."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: Any = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthorized"


class PermissionDenied(AppError):
    status_code = 403
    code = "forbidden"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
