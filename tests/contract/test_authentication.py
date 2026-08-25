from __future__ import annotations

import logging
from pathlib import Path

from conftest import TEST_API_KEY, FakeClock, build_real_test_app
from fastapi.testclient import TestClient


def _create_client(
    root: Path, auth_config_path: Path, fake_clock: FakeClock
) -> TestClient:
    application = build_real_test_app(
        root,
        auth_config_path,
        fake_clock,
    )
    return TestClient(application)


def test_missing_or_invalid_bearer_token_returns_403(
    tmp_path: Path, auth_config_path: Path, fake_clock: FakeClock
) -> None:
    with _create_client(tmp_path / "runtime", auth_config_path, fake_clock) as client:
        responses = [
            client.get("/v1/models"),
            client.get("/v1/models", headers={"Authorization": "Basic dXNlcjprZXk="}),
            client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"}),
            client.get("/v1/models", headers={"Authorization": "Bearer"}),
            client.get(
                "/v1/models",
                headers=[
                    ("Authorization", f"Bearer {TEST_API_KEY}"),
                    ("Authorization", "Bearer wrong-key"),
                ],
            ),
        ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"] == {
            "code": "FORBIDDEN",
            "message": "Authentication credentials are missing or invalid.",
            "details": {},
            "retryable": False,
        }
        assert response.json()["request_id"].startswith("req_")
        assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_authentication_applies_to_non_api_http_paths(
    tmp_path: Path, auth_config_path: Path, fake_clock: FakeClock
) -> None:
    with _create_client(tmp_path / "runtime", auth_config_path, fake_clock) as client:
        assert client.get("/docs").status_code == 403
        assert client.get("/does-not-exist").status_code == 403


def test_valid_bearer_token_is_accepted_and_logs_username_and_client_ip(
    auth_config_path: Path,
    fake_clock: FakeClock,
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="server.access")
    with _create_client(tmp_path / "runtime", auth_config_path, fake_clock) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

    assert response.status_code == 200
    access_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "server.access"
    ]
    assert any(
        "auth_status=accepted" in message
        and "username=test-user" in message
        and "client_ip=testclient" in message
        for message in access_messages
    )
    assert all(TEST_API_KEY not in message for message in access_messages)
