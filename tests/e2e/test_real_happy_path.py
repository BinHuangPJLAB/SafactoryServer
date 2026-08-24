from __future__ import annotations

import time
from pathlib import Path

from conftest import TEST_API_KEY, FakeClock
from fastapi.testclient import TestClient
from real_helpers import (
    FakeDataPlatform,
    FakeRJobClient,
    ReadyHealthChecker,
    write_initialization_config,
    write_real_configs,
)

from server.config import Settings
from server.infrastructure.real.rjob import RJobSnapshot, RJobState
from server.main import RealDependencies, create_app


def test_real_mode_runs_and_queries_only_the_sdk(
    tmp_path: Path, auth_config_path: Path, fake_clock: FakeClock
) -> None:
    models, ranges = write_real_configs(tmp_path)
    initialization = write_initialization_config(tmp_path)
    with auth_config_path.open("a", encoding="utf-8") as config:
        config.write("  - username: other-user\n    api_key: other-api-key\n")
    rjobs = FakeRJobClient(
        snapshots={
            "rjob_gateway": RJobSnapshot(
                "rjob_gateway",
                RJobState.RUNNING,
                ready=True,
                address="gateway.jobs.svc",
                port=8080,
            ),
            "rjob_controller": RJobSnapshot(
                "rjob_controller",
                RJobState.SUCCEEDED,
                workload_summary={
                    "total": 4,
                    "running": 0,
                    "succeeded": 4,
                    "failed": 0,
                    "collected": 4,
                },
            ),
        }
    )
    data = FakeDataPlatform()
    settings = Settings(
        mode="real",
        auth_config_path=auth_config_path,
        initialization_config_path=initialization,
        model_config_path=models,
        range_config_path=ranges,
        control_db_path=tmp_path / "control.db",
        shared_storage_root=tmp_path / "shared",
        rjob_endpoint="https://rjob.invalid",
        orchestrator_poll_seconds=0.01,
        log_level="WARNING",
    )
    application = create_app(
        settings,
        clock=fake_clock,
        real_dependencies=RealDependencies(
            rjobs=rjobs,
            data_platform=data,
            gateway_health=ReadyHealthChecker(),
        ),
    )
    with TestClient(
        application,
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as client:
        assert client.get("/v1/models").json() == {
            "items": [{"model_id": "model_real_001", "name": "Real Route"}]
        }
        created = client.post(
            "/v1/jobs",
            json={"model_id": "model_real_001", "range_id": "range_real_001"},
        )
        assert created.status_code == 202
        job_id = created.json()["job_id"]

        sessions = None
        for _ in range(100):
            sessions = client.get("/v1/jobs/sessions", params={"job_id": job_id})
            if sessions.json()["job_status"] == "succeeded":
                break
            time.sleep(0.005)
        assert sessions is not None
        assert sessions.json() == {
            "job_id": job_id,
            "job_status": "succeeded",
            "session_ids": ["session_real_001"],
        }

        result = client.get(
            "/v1/sessions/result",
            params={"job_id": job_id, "session_id": "session_real_001"},
        )
        assert result.json()["score"] == 9.25
        steps = client.get(
            "/v1/sessions/steps",
            params={"job_id": job_id, "session_id": "session_real_001"},
        )
        assert steps.json()["steps"] == [
            {"step_id": "step_real_001", "sequence_no": 1}
        ]
        trajectory = client.get(
            "/v1/sessions/steps/trajectory",
            params={
                "job_id": job_id,
                "session_id": "session_real_001",
                "step_id": "step_real_001",
            },
        )
        assert trajectory.json()["trajectory"]["model_input"]["api_key"] == (
            "[REDACTED]"
        )

        hidden = client.get(
            "/v1/jobs/sessions",
            params={"job_id": job_id},
            headers={"Authorization": "Bearer other-api-key"},
        )
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "JOB_NOT_FOUND"

    assert [spec.role for spec in rjobs.created] == ["gateway", "safactory-controller"]
    assert all(
        call[1][0] == job_id
        for call in data.calls
        if call[0] not in {"preflight"}
    )
