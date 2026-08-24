from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app

TEST_API_KEY = "test-api-key"


@dataclass
class FakeClock:
    current: datetime = datetime(2026, 8, 20, 8, 0, 0, tzinfo=UTC)
    elapsed_seconds: float = 0.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.elapsed_seconds

    def advance(self, milliseconds: int) -> None:
        delta = timedelta(milliseconds=milliseconds)
        self.current += delta
        self.elapsed_seconds += milliseconds / 1000


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def auth_config_path(tmp_path):
    path = tmp_path / "trusted_api_keys.yaml"
    path.write_text(
        f'''schema_version: "1.0"
users:
  - username: test-user
    api_key: {TEST_API_KEY}
''',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def client(fake_clock: FakeClock, auth_config_path) -> TestClient:
    application = create_app(
        Settings(auth_config_path=auth_config_path, log_level="WARNING"),
        clock=fake_clock,
    )
    with TestClient(
        application,
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as test_client:
        yield test_client


@pytest.fixture
def created_job(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/v1/jobs",
        json={"model_id": "model_glm_001", "range_id": "range_web_001"},
    )
    assert response.status_code == 202
    return response.json()
