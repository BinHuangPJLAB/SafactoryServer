from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from server.auth.schema import AuthConfig


class AuthConfigLoadError(RuntimeError):
    """The trusted API-key configuration cannot be read or validated."""


def load_auth_config(path: Path) -> AuthConfig:
    try:
        raw_document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_document, dict):
            raise ValueError("The auth configuration root must be an object.")
        return AuthConfig.model_validate(raw_document)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError):
        raise AuthConfigLoadError(
            f"Authentication configuration is unavailable or invalid: {path}"
        ) from None
