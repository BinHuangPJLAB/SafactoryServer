from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.api.schemas.common import ErrorBody, ErrorResponse
from server.domain.errors import ERROR_SPECS, DomainError, ErrorCode

LOGGER = logging.getLogger("server.errors")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return _error_response(
            request,
            exc.spec.status_code,
            exc.code,
            exc.spec.message,
            exc.details,
            exc.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            ".".join(str(part) for part in error["loc"] if part not in {"body", "query"})
            for error in exc.errors()
        ]
        spec = ERROR_SPECS[ErrorCode.INVALID_REQUEST]
        return _error_response(
            request,
            spec.status_code,
            ErrorCode.INVALID_REQUEST,
            spec.message,
            {"fields": fields},
            spec.retryable,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        spec = ERROR_SPECS[ErrorCode.INVALID_REQUEST]
        return _error_response(
            request,
            exc.status_code,
            ErrorCode.INVALID_REQUEST,
            spec.message,
            {"http_status": exc.status_code},
            spec.retryable,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        LOGGER.exception("request_id=%s unexpected_error", request_id)
        spec = ERROR_SPECS[ErrorCode.INTERNAL_ERROR]
        return _error_response(
            request,
            spec.status_code,
            ErrorCode.INTERNAL_ERROR,
            spec.message,
            {},
            spec.retryable,
        )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unavailable")


def _error_response(
    request: Request,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: dict[str, Any],
    retryable: bool,
) -> JSONResponse:
    content = ErrorResponse(
        error=ErrorBody(
            code=code.value,
            message=message,
            details=details,
            retryable=retryable,
        ),
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=status_code, content=content.model_dump(mode="json"))

