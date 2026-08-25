from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    FORBIDDEN = "FORBIDDEN"
    INVALID_REQUEST = "INVALID_REQUEST"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    RANGE_NOT_FOUND = "RANGE_NOT_FOUND"
    RANGE_NOT_AVAILABLE = "RANGE_NOT_AVAILABLE"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    STEP_NOT_FOUND = "STEP_NOT_FOUND"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status_code: int
    message: str
    retryable: bool


ERROR_SPECS = {
    ErrorCode.FORBIDDEN: ErrorSpec(
        403, "Authentication credentials are missing or invalid.", False
    ),
    ErrorCode.INVALID_REQUEST: ErrorSpec(400, "The request is invalid.", False),
    ErrorCode.MODEL_NOT_FOUND: ErrorSpec(422, "The selected model does not exist.", False),
    ErrorCode.MODEL_NOT_AVAILABLE: ErrorSpec(
        422, "The selected model is not available.", False
    ),
    ErrorCode.RANGE_NOT_FOUND: ErrorSpec(422, "The selected range does not exist.", False),
    ErrorCode.RANGE_NOT_AVAILABLE: ErrorSpec(
        422, "The selected range is not available.", False
    ),
    ErrorCode.JOB_NOT_FOUND: ErrorSpec(404, "The specified job does not exist.", False),
    ErrorCode.SESSION_NOT_FOUND: ErrorSpec(
        404, "The specified session does not exist for this job.", False
    ),
    ErrorCode.STEP_NOT_FOUND: ErrorSpec(
        404, "The specified step does not exist for this session.", False
    ),
    ErrorCode.DEPENDENCY_UNAVAILABLE: ErrorSpec(
        503, "A required service dependency is unavailable.", True
    ),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(500, "An internal server error occurred.", True),
}


class DomainError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        details: dict[str, Any] | None = None,
        *,
        retryable: bool | None = None,
    ) -> None:
        self.code = code
        self.spec = ERROR_SPECS[code]
        self.details = details or {}
        self.retryable = self.spec.retryable if retryable is None else retryable
        super().__init__(self.spec.message)
