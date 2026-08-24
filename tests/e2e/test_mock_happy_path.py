from conftest import FakeClock
from fastapi.testclient import TestClient


def test_complete_mock_query_flow(client: TestClient, fake_clock: FakeClock) -> None:
    models = client.get("/v1/models").json()["items"]
    created = client.post(
        "/v1/jobs",
        json={"model_id": models[0]["model_id"], "range_id": "range_web_001"},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    fake_clock.advance(2100)
    sessions_response = client.get(
        "/v1/jobs/sessions", params={"job_id": job_id}
    )
    assert sessions_response.json()["job_status"] == "succeeded"
    session_ids = sessions_response.json()["session_ids"]
    assert len(session_ids) == 2

    for session_id in session_ids:
        result = client.get(
            "/v1/sessions/result",
            params={"job_id": job_id, "session_id": session_id},
        )
        assert result.json()["result_status"] == "succeeded"
        assert result.json()["score"] is not None

        steps = client.get(
            "/v1/sessions/steps",
            params={"job_id": job_id, "session_id": session_id},
        ).json()
        assert steps["sealed"] is True
        assert steps["step_count"] == len(steps["steps"])

        for step in steps["steps"]:
            trajectory = client.get(
                "/v1/sessions/steps/trajectory",
                params={
                    "job_id": job_id,
                    "session_id": session_id,
                    "step_id": step["step_id"],
                },
            )
            assert trajectory.status_code == 200
            assert trajectory.json()["step_id"] == step["step_id"]
