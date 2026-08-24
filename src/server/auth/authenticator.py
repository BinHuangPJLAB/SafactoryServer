from __future__ import annotations

import hmac
from collections.abc import Sequence
from dataclasses import dataclass

from server.auth.schema import AuthConfig


@dataclass(frozen=True, slots=True)
class BearerAuthenticator:
    _credentials: tuple[tuple[str, str], ...]

    @classmethod
    def from_config(cls, config: AuthConfig) -> BearerAuthenticator:
        return cls(
            tuple(
                (user.username, user.api_key.get_secret_value())
                for user in config.users
            )
        )

    def authenticate(self, authorization_headers: Sequence[str]) -> str | None:
        if len(authorization_headers) != 1:
            return None

        parts = authorization_headers[0].split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None

        presented_key = parts[1]
        username = None
        for trusted_username, trusted_key in self._credentials:
            if hmac.compare_digest(presented_key, trusted_key):
                username = trusted_username
        return username
