from fastapi.testclient import TestClient


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
