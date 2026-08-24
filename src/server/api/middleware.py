from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from server.api.schemas.common import ErrorBody, ErrorResponse
from server.auth.authenticator import BearerAuthenticator
from server.auth.context import (
    reset_authenticated_username,
    reset_request_id,
    set_authenticated_username,
    set_request_id,
)
from server.domain.errors import ERROR_SPECS, ErrorCode
from server.infrastructure.identifiers import IdentifierFactory

LOGGER = logging.getLogger("server.access")


def install_request_middleware(
    app: FastAPI,
    identifiers: IdentifierFactory,
    authenticator: BearerAuthenticator,
) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = identifiers.new("req")
        request.state.request_id = request_id
        started_at = time.perf_counter()
        client_ip = request.client.host if request.client is not None else "unknown"
        username = authenticator.authenticate(
            request.headers.getlist("authorization")
        )

        if username is None:
            response = _forbidden_response(request_id)
            auth_status = "rejected"
        else:
            request.state.authenticated_username = username
            context_token = set_authenticated_username(username)
            request_token = set_request_id(request_id)
            try:
                response = await call_next(request)
            finally:
                reset_request_id(request_token)
                reset_authenticated_username(context_token)
            auth_status = "accepted"

        response.headers["X-Request-ID"] = request_id
        if response.headers.get("Content-Type") == "application/json":
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        duration_ms = (time.perf_counter() - started_at) * 1000
        LOGGER.info(
            "request_id=%s auth_status=%s username=%s client_ip=%s "
            "method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            auth_status,
            username or "-",
            client_ip,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


def _forbidden_response(request_id: str) -> JSONResponse:
    spec = ERROR_SPECS[ErrorCode.FORBIDDEN]
    content = ErrorResponse(
        error=ErrorBody(
            code=ErrorCode.FORBIDDEN.value,
            message=spec.message,
            details={},
            retryable=spec.retryable,
        ),
        request_id=request_id,
    )
    return JSONResponse(status_code=spec.status_code, content=content.model_dump(mode="json"))
