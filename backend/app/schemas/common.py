from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Any = None
    correlation_id: str | None = None


class Accepted(BaseModel):
    """202 response for queued work."""

    job_id: str | None = Field(None, description="RQ job id (null when run inline)")
    status_url: str
    model_config = ConfigDict(extra="allow")
