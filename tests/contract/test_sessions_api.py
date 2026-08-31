import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from server.infrastructure.real.file_manager import JobFileBinding


def _job_binding(client: TestClient, job_id: str) -> JobFileBinding:
    client.portal.call(client.app.state.orchestrator.reconcile_once)
    job = client.portal.call(client.app.state.control_store.get, job_id)
    assert job is not None and job.binding_json is not None
    return JobFileBinding.from_json(job.binding_json)


def test_result_is_read_from_the_data_platform(
    client: TestClient, created_job: dict[str, str]
) -> None:
    response = client.get(
        "/v1/sessions/result",
        params={
            "job_id": created_job["job_id"],
            "session_id": "session_real_001",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session_real_001",
        "result_status": "succeeded",
        "score": 9.25,
        "completed_at": "2026-08-22T01:02:03.000Z",
    }
    assert "Retry-After" not in response.headers


def test_result_includes_the_configured_environment_artifact(
    client: TestClient, created_job: dict[str, str]
) -> None:
    job_id = created_job["job_id"]
    session_id = "session_real_001"
    binding = _job_binding(client, job_id)
    ranges_path = client.app.state.initialization_config.catalog.ranges_path
    ranges = yaml.safe_load(ranges_path.read_text(encoding="utf-8"))
    ranges["ranges"][0]["groups"][0]["result_artifact"] = (
        "runtime-test-result.json"
    )
    ranges_path.write_text(yaml.safe_dump(ranges, sort_keys=False), encoding="utf-8")
    artifact = {
        "schema_version": "runtime-test-result/v1",
        "e2e_success": True,
        "objective_state": "completed",
    }
    path = Path(
        binding.result_local_path,
        job_id,
        session_id,
        "runtime-test-result.json",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact), encoding="utf-8")

    response = client.get(
        "/v1/sessions/result",
        params={"job_id": job_id, "session_id": session_id},
    )

    assert response.status_code == 200
    assert response.json()["result"] == artifact


def test_terminal_result_rejects_a_missing_configured_artifact(
    client: TestClient, created_job: dict[str, str]
) -> None:
    ranges_path = client.app.state.initialization_config.catalog.ranges_path
    ranges = yaml.safe_load(ranges_path.read_text(encoding="utf-8"))
    ranges["ranges"][0]["groups"][0]["result_artifact"] = (
        "runtime-test-result.json"
    )
    ranges_path.write_text(yaml.safe_dump(ranges, sort_keys=False), encoding="utf-8")

    response = client.get(
        "/v1/sessions/result",
        params={
            "job_id": created_job["job_id"],
            "session_id": "session_real_001",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_milestones_are_read_fresh_from_the_job_results(
    client: TestClient, created_job: dict[str, str]
) -> None:
    job_id = created_job["job_id"]
    session_id = "session_real_001"
    binding = _job_binding(client, job_id)
    path = Path(binding.result_local_path, job_id, session_id, "milestones.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "schema_version": "agent-range.milestones/v1",
        "run_id": "run_test",
        "updated_at": "2026-08-31T06:19:56Z",
        "completed": 1,
        "total": 2,
        "latest_reached": "user-shell",
        "next_expected": "root-shell",
        "milestones": [
            {
                "ordinal": 0,
                "id": "user-shell",
                "status": "observed",
                "payload": {"api_key": "must-redact", "satisfied": True},
            }
        ],
    }
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    first = client.get(
        "/v1/sessions/milestones",
        params={"job_id": job_id, "session_id": session_id},
    )

    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    assert first.json()["milestone_status"] == "available"
    assert first.json()["snapshot"]["completed"] == 1
    assert first.json()["snapshot"]["milestones"][0]["payload"] == {
        "api_key": "[REDACTED]",
        "satisfied": True,
    }

    snapshot["completed"] = 2
    replacement = path.with_suffix(".tmp")
    replacement.write_text(json.dumps(snapshot), encoding="utf-8")
    replacement.replace(path)
    second = client.get(
        "/v1/sessions/milestones",
        params={"job_id": job_id, "session_id": session_id},
    )
    assert second.json()["snapshot"]["completed"] == 2


def test_milestones_reject_an_environment_without_range_capability(
    client: TestClient, created_job: dict[str, str]
) -> None:
    ranges_path = client.app.state.initialization_config.catalog.ranges_path
    ranges = yaml.safe_load(ranges_path.read_text(encoding="utf-8"))
    ranges["ranges"][0]["groups"][0]["supports_milestones"] = False
    ranges_path.write_text(yaml.safe_dump(ranges, sort_keys=False), encoding="utf-8")

    response = client.get(
        "/v1/sessions/milestones",
        params={
            "job_id": created_job["job_id"],
            "session_id": "session_real_001",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "MILESTONES_NOT_SUPPORTED",
        "message": "Milestones are not supported for this environment.",
        "details": {
            "job_id": created_job["job_id"],
            "session_id": "session_real_001",
        },
        "retryable": False,
    }


def test_steps_and_trajectory_are_read_and_redacted(
    client: TestClient, created_job: dict[str, str]
) -> None:
    job_id = created_job["job_id"]
    steps = client.get(
        "/v1/sessions/steps",
        params={"job_id": job_id, "session_id": "session_real_001"},
    )

    assert steps.status_code == 200
    assert steps.json() == {
        "session_id": "session_real_001",
        "step_count": 1,
        "sealed": True,
        "steps": [{"step_id": "step_real_001", "sequence_no": 1}],
    }

    trajectory = client.get(
        "/v1/sessions/steps/trajectory",
        params={
            "job_id": job_id,
            "session_id": "session_real_001",
            "step_id": "step_real_001",
        },
    )
    assert trajectory.status_code == 200
    assert trajectory.json()["trajectory"]["model_input"]["api_key"] == (
        "[REDACTED]"
    )
    assert trajectory.json()["started_at"] == "2026-08-22T01:02:00.000Z"


def test_unknown_real_job_session_and_step_have_scoped_errors(
    client: TestClient, created_job: dict[str, str]
) -> None:
    unknown_job = client.get(
        "/v1/sessions/result",
        params={"job_id": "job_unknown", "session_id": "session_real_001"},
    )
    assert unknown_job.status_code == 404
    assert unknown_job.json()["error"]["code"] == "JOB_NOT_FOUND"

    unknown_session = client.get(
        "/v1/sessions/result",
        params={"job_id": created_job["job_id"], "session_id": "session_unknown"},
    )
    assert unknown_session.status_code == 404
    assert unknown_session.json()["error"]["code"] == "SESSION_NOT_FOUND"

    unknown_step = client.get(
        "/v1/sessions/steps/trajectory",
        params={
            "job_id": created_job["job_id"],
            "session_id": "session_real_001",
            "step_id": "step_unknown",
        },
    )
    assert unknown_step.status_code == 404
    assert unknown_step.json()["error"]["code"] == "STEP_NOT_FOUND"
