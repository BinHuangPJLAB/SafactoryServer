from __future__ import annotations

from contextvars import ContextVar, Token

_authenticated_username: ContextVar[str | None] = ContextVar(
    "authenticated_username", default=None
)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_authenticated_username(username: str) -> Token[str | None]:
    return _authenticated_username.set(username)


def reset_authenticated_username(token: Token[str | None]) -> None:
    _authenticated_username.reset(token)


def get_authenticated_username() -> str | None:
    return _authenticated_username.get()


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()
