from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from real_helpers import (
    FakeDataPlatform,
    FakeRJobClient,
    ReadyHealthChecker,
    write_initialization_config,
    write_real_configs,
)

from server.config import Settings
from server.infrastructure.real.data_platform import DataPlatformRepository
from server.infrastructure.real.rjob import RJobSnapshot, RJobState
from server.main import RealDependencies, create_app

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


def build_real_test_app(
    root: Path,
    auth_config_path: Path,
    clock: FakeClock,
    data_platform: DataPlatformRepository | None = None,
):
    root.mkdir(parents=True, exist_ok=True)
    write_real_configs(root)
    initialization = write_initialization_config(root)
    rjobs = FakeRJobClient(
        snapshots={
            "rjob_gateway": RJobSnapshot(
                "rjob_gateway",
                RJobState.RUNNING,
                ready=True,
                address="gateway.jobs.svc",
                port=8080,
            ),
            "rjob_controller": RJobSnapshot(
                "rjob_controller",
                RJobState.SUCCEEDED,
                workload_summary={
                    "total": 4,
                    "running": 0,
                    "succeeded": 4,
                    "failed": 0,
                    "collected": 4,
                },
            ),
        }
    )
    return create_app(
        Settings(
            auth_config_path=auth_config_path,
            initialization_config_path=initialization,
            log_level="WARNING",
        ),
        clock=clock,
        real_dependencies=RealDependencies(
            rjobs=rjobs,
            data_platform=data_platform or FakeDataPlatform(),
            gateway_health=ReadyHealthChecker(),
        ),
    )


@pytest.fixture
def client(
    tmp_path: Path, fake_clock: FakeClock, auth_config_path: Path
) -> TestClient:
    application = build_real_test_app(
        tmp_path / "runtime", auth_config_path, fake_clock
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
        json={"model_id": "kimi-k3", "range_id": "range_real_001"},
    )
    assert response.status_code == 202
    return response.json()
