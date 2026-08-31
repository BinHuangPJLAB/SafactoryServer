from fastapi.testclient import TestClient


def test_lists_available_ranges_as_a_direct_array(client: TestClient) -> None:
    response = client.get("/v1/ranges")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == [
        {
            "range_id": "range_real_001",
            "description": "Browser test range",
        }
    ]
