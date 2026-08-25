from fastapi.testclient import TestClient


def test_invalid_request_uses_stable_error_envelope(client: TestClient) -> None:
    response = client.post(
        "/v1/jobs",
        json={"model_id": " ", "range_id": "range_real_001", "extra": True},
    )

    assert response.status_code == 400
    assert set(response.json()) == {"error", "request_id"}
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["request_id"].startswith("req_")


def test_missing_query_parameter_is_invalid_request(client: TestClient) -> None:
    response = client.get("/v1/sessions/result", params={"job_id": "job_unknown"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_malformed_json_is_invalid_request(client: TestClient) -> None:
    response = client.post(
        "/v1/jobs",
        content=b'{"model_id":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_unknown_resources_have_specific_errors(client: TestClient) -> None:
    job_response = client.get(
        "/v1/jobs/sessions", params={"job_id": "job_unknown"}
    )
    assert job_response.status_code == 404
    assert job_response.json()["error"]["code"] == "JOB_NOT_FOUND"
