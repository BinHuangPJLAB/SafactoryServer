from fastapi.testclient import TestClient


def test_lists_only_available_public_model_fields(client: TestClient) -> None:
    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.json() == {
        "items": [
            {"model_id": "kimi-k3", "name": "kimi-k3"},
            {"model_id": "qwen-max", "name": "qwen-max"},
        ]
    }
    assert response.headers["X-Request-ID"].startswith("req_")
