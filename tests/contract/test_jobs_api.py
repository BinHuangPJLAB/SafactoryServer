from fastapi.testclient import TestClient


def test_creates_unique_queued_job(client: TestClient) -> None:
    payload = {"model_id": "kimi-k3", "range_id": "range_real_001"}

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


def test_job_sessions_come_from_the_data_platform(
    client: TestClient, created_job: dict[str, str]
) -> None:
    job_id = created_job["job_id"]

    response = client.get("/v1/jobs/sessions", params={"job_id": job_id})

    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["job_status"] in {
        "queued",
        "preparing",
        "running",
        "succeeded",
    }
    assert response.json()["session_ids"] == ["session_real_001"]


def test_create_job_business_errors(client: TestClient) -> None:
    cases = [
        ("missing_model", "range_real_001", "MODEL_NOT_FOUND", False),
        ("kimi-k3", "missing_range", "RANGE_NOT_FOUND", False),
    ]

    for model_id, range_id, error_code, retryable in cases:
        response = client.post(
            "/v1/jobs", json={"model_id": model_id, "range_id": range_id}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == error_code
        assert response.json()["error"]["retryable"] is retryable


def test_requested_model_is_not_restricted_by_the_range(client: TestClient) -> None:
    response = client.post(
        "/v1/jobs",
        json={"model_id": "qwen-max", "range_id": "range_real_001"},
    )

    assert response.status_code == 202
    assert response.json()["model_id"] == "qwen-max"
