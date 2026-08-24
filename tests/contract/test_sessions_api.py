from conftest import FakeClock
from fastapi.testclient import TestClient


def test_result_moves_from_pending_to_running_to_succeeded(
    client: TestClient, fake_clock: FakeClock, created_job: dict[str, str]
) -> None:
    job_id = created_job["job_id"]
    fake_clock.advance(350)
    session_id = client.get(
        "/v1/jobs/sessions", params={"job_id": job_id}
    ).json()["session_ids"][0]

    pending = client.get(
        "/v1/sessions/result", params={"job_id": job_id, "session_id": session_id}
    )
    assert pending.json() == {
        "session_id": session_id,
        "result_status": "pending",
        "score": None,
        "completed_at": None,
    }
    assert pending.headers["Retry-After"] == "1"

    fake_clock.advance(200)
    running = client.get(
        "/v1/sessions/result", params={"job_id": job_id, "session_id": session_id}
    )
    assert running.json()["result_status"] == "running"
    assert running.json()["score"] is None

    fake_clock.advance(1000)
    succeeded = client.get(
        "/v1/sessions/result", params={"job_id": job_id, "session_id": session_id}
    )
    assert succeeded.json()["result_status"] == "succeeded"
    assert succeeded.json()["score"] == 8.5
    assert succeeded.json()["completed_at"] == "2026-08-20T08:00:01.500Z"
    assert "Retry-After" not in succeeded.headers


def test_steps_and_trajectory_are_incremental_and_stable(
    client: TestClient, fake_clock: FakeClock, created_job: dict[str, str]
) -> None:
    job_id = created_job["job_id"]
    fake_clock.advance(350)
    session_id = client.get(
        "/v1/jobs/sessions", params={"job_id": job_id}
    ).json()["session_ids"][0]

    empty = client.get(
        "/v1/sessions/steps", params={"job_id": job_id, "session_id": session_id}
    )
    assert empty.json() == {
        "session_id": session_id,
        "step_count": 0,
        "sealed": False,
        "steps": [],
    }

    fake_clock.advance(300)
    partial = client.get(
        "/v1/sessions/steps", params={"job_id": job_id, "session_id": session_id}
    )
    assert partial.json()["step_count"] == 1
    assert partial.json()["sealed"] is False
    first_step = partial.json()["steps"][0]
    assert first_step["sequence_no"] == 1

    trajectory = client.get(
        "/v1/sessions/steps/trajectory",
        params={
            "job_id": job_id,
            "session_id": session_id,
            "step_id": first_step["step_id"],
        },
    )
    assert trajectory.status_code == 200
    assert trajectory.json()["sequence_no"] == 1
    assert set(trajectory.json()["trajectory"]) == {
        "model_input",
        "model_output",
        "action",
        "observation",
    }
    assert (
        trajectory.json()["trajectory"]["model_input"]["headers"]["authorization"]
        == "[REDACTED]"
    )
    assert trajectory.json()["started_at"].endswith("Z")

    fake_clock.advance(850)
    complete = client.get(
        "/v1/sessions/steps", params={"job_id": job_id, "session_id": session_id}
    )
    assert complete.json()["step_count"] == 2
    assert complete.json()["sealed"] is True
    assert complete.json()["steps"][0] == first_step


def test_ids_cannot_cross_job_or_session_boundaries(
    client: TestClient, fake_clock: FakeClock
) -> None:
    payload = {"model_id": "model_glm_001", "range_id": "range_web_001"}
    first_job = client.post("/v1/jobs", json=payload).json()["job_id"]
    fake_clock.advance(750)
    first_sessions = client.get(
        "/v1/jobs/sessions", params={"job_id": first_job}
    ).json()["session_ids"]
    first_steps = client.get(
        "/v1/sessions/steps",
        params={"job_id": first_job, "session_id": first_sessions[0]},
    ).json()["steps"]

    second_job = client.post("/v1/jobs", json=payload).json()["job_id"]
    cross_job = client.get(
        "/v1/sessions/result",
        params={"job_id": second_job, "session_id": first_sessions[0]},
    )
    assert cross_job.status_code == 404
    assert cross_job.json()["error"]["code"] == "SESSION_NOT_FOUND"

    cross_session = client.get(
        "/v1/sessions/steps/trajectory",
        params={
            "job_id": first_job,
            "session_id": first_sessions[1],
            "step_id": first_steps[0]["step_id"],
        },
    )
    assert cross_session.status_code == 404
    assert cross_session.json()["error"]["code"] == "STEP_NOT_FOUND"
