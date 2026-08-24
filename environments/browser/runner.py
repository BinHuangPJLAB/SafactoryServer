#!/usr/bin/env python3
"""Minimal managed environment used to verify the complete real RJob chain."""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    request = json.loads(os.environ["SAFACTORY_START_REQUEST_JSON"])
    session_id = str(request["session_id"])
    dataset = request.get("env_params", {}).get("dataset", {})
    result = {
        "session_id": session_id,
        "status": "succeeded",
        "total_reward": 1.0,
        "step_count": 1,
        "terminated": True,
        "truncated": False,
        "error_text": None,
        "metrics": {
            "environment": "browser-smoke",
            "task_idx": dataset.get("task_idx"),
            "instruction": dataset.get("instruction"),
        },
    }
    result_path = Path(os.environ["SAFACTORY_RESULT_PATH"])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix(result_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, result_path)
    print("SAFACTORY_RESULT_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
