from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from server.infrastructure.mock.fixture_schema import FixtureDocument


class FixtureLoadError(RuntimeError):
    """The bundled mock data cannot be read or validated."""


def load_fixture(path: Path) -> FixtureDocument:
    try:
        raw_document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_document, dict):
            raise ValueError("The fixture root must be an object.")
        return FixtureDocument.model_validate(raw_document)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise FixtureLoadError("Mock fixture is unavailable or invalid.") from exc

