from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def test_managed_smoke_runner_writes_atomic_result(tmp_path: Path, monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    runner_path = root / "environments/browser/runner.py"
    result_path = tmp_path / "job/session/result.json"
    monkeypatch.setenv(
        "SAFACTORY_START_REQUEST_JSON",
        json.dumps(
            {
                "session_id": "session_smoke",
                "env_params": {"dataset": {"task_idx": 0, "instruction": "smoke"}},
            }
        ),
    )
    monkeypatch.setenv("SAFACTORY_RESULT_PATH", str(result_path))
    spec = importlib.util.spec_from_file_location("managed_smoke_runner", runner_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main() == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["session_id"] == "session_smoke"
    assert result["status"] == "succeeded"
    assert result["total_reward"] == 1.0
