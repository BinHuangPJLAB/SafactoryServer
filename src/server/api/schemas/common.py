from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel, ConfigDict, PlainSerializer


def serialize_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


UtcTimestamp = Annotated[
    datetime,
    PlainSerializer(serialize_utc, return_type=str, when_used="json"),
]
RequiredId = Annotated[str, Query(min_length=1, pattern=r".*\S.*")]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, Any]
    retryable: bool


class ErrorResponse(ApiModel):
    error: ErrorBody
    request_id: str


OptionalFailure = ErrorBody | None
