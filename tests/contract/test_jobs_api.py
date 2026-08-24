from conftest import FakeClock
from fastapi.testclient import TestClient


def test_creates_unique_queued_job(client: TestClient) -> None:
    payload = {"model_id": "model_glm_001", "range_id": "range_web_001"}

    first = client.post("/v1/jobs", json=payload)
    second = client.post("/v1/jobs", json=payload)

    assert first.status_code == 202
    assert set(first.json()) == {"job_id", "status", "model_id", "range_id", "created_at"}
    assert first.json()["status"] == "queued"
    assert first.json()["created_at"].endswith("Z")
    assert first.json()["job_id"] != second.json()["job_id"]
    assert first.headers["Location"] == (
        f"/v1/jobs/sessions?job_id={first.json()['job_id']}"
    )


def test_job_status_and_session_list_follow_timeline(
    client: TestClient, fake_clock: FakeClock, created_job: dict[str, str]
) -> None:
    job_id = created_job["job_id"]

    queued = client.get("/v1/jobs/sessions", params={"job_id": job_id})
    assert queued.json() == {"job_id": job_id, "job_status": "queued", "session_ids": []}
    assert queued.headers["Retry-After"] == "1"

    fake_clock.advance(150)
    preparing = client.get("/v1/jobs/sessions", params={"job_id": job_id})
    assert preparing.json()["job_status"] == "preparing"
    assert preparing.json()["session_ids"] == []

    fake_clock.advance(200)
    running = client.get("/v1/jobs/sessions", params={"job_id": job_id})
    assert running.json()["job_status"] == "running"
    assert len(running.json()["session_ids"]) == 1

    first_session_id = running.json()["session_ids"][0]
    fake_clock.advance(400)
    appended = client.get("/v1/jobs/sessions", params={"job_id": job_id})
    assert len(appended.json()["session_ids"]) == 2
    assert appended.json()["session_ids"][0] == first_session_id

    fake_clock.advance(1300)
    succeeded = client.get("/v1/jobs/sessions", params={"job_id": job_id})
    assert succeeded.json()["job_status"] == "succeeded"
    assert "Retry-After" not in succeeded.headers


def test_create_job_business_errors(client: TestClient) -> None:
    cases = [
        ("missing_model", "range_web_001", "MODEL_NOT_FOUND", False),
        ("model_archived_001", "range_web_001", "MODEL_NOT_AVAILABLE", False),
        ("model_glm_001", "missing_range", "RANGE_NOT_FOUND", False),
        ("model_glm_001", "range_maintenance_001", "RANGE_NOT_AVAILABLE", True),
        (
            "model_qwen_001",
            "range_glm_only_001",
            "MODEL_RANGE_NOT_SUPPORTED",
            False,
        ),
    ]

    for model_id, range_id, error_code, retryable in cases:
        response = client.post(
            "/v1/jobs", json={"model_id": model_id, "range_id": range_id}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == error_code
        assert response.json()["error"]["retryable"] is retryable

