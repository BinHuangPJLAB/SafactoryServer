from pathlib import Path

import pytest

from server.config import DEFAULT_FIXTURE_PATH
from server.infrastructure.mock.fixture_loader import FixtureLoadError, load_fixture


def test_loads_versioned_fixture() -> None:
    document = load_fixture(DEFAULT_FIXTURE_PATH)

    assert document.schema_version == "1.0"
    assert {model.model_id for model in document.models} >= {
        "model_glm_001",
        "model_qwen_001",
    }
    assert "web_happy" in document.scenarios


def test_rejects_unknown_scenario_reference(tmp_path: Path) -> None:
    fixture_path = tmp_path / "invalid.yaml"
    fixture_path.write_text(
        """
schema_version: "1.0"
models:
  - model_id: model_1
    name: Model
    available: true
ranges:
  - range_id: range_1
    available: true
    supported_models: [model_1]
    scenario_id: missing
scenarios:
  existing:
    preparing_after_ms: 0
    sessions:
      - fixture_id: session
        visible_after_ms: 1
        result:
          running_after_ms: 1
          completed_after_ms: 2
          score: 1
        steps: []
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(FixtureLoadError):
        load_fixture(fixture_path)

