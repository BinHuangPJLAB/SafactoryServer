from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from server.domain.entities import (
    ResultStatus,
    SessionResult,
    SessionSteps,
    StepIndex,
    StepTrajectory,
)
from server.infrastructure.real.rjob import RJobSnapshot, RJobSpec, RJobState


def write_real_configs(root: Path) -> tuple[Path, Path]:
    models = root / "models.yaml"
    ranges = root / "ranges.yaml"
    agent = root / "agent.yaml"
    dataset = root / "dataset.jsonl"
    start = root / "browser.start.yaml"
    models.write_text(
        """
schema_version: "1.0"
models:
  - model_id: model_real_001
    name: Real Route
    available: true
    gateway:
      route:
        provider: deployment-route
      environment:
        MODEL_CREDENTIAL_REF: secret/model-real
""".strip(),
        encoding="utf-8",
    )
    ranges.write_text(
        """
schema_version: "1.0"
ranges:
  - range_id: range_real_001
    available: true
    supported_models: [model_real_001]
    agent_config: agent.yaml
    groups:
      - env_name: browser
        dataset: dataset.jsonl
        start_config: browser.start.yaml
""".strip(),
        encoding="utf-8",
    )
    agent.write_text(
        """
environments:
  - env_name: browser
    env_image: registry.example/env@sha256:abc
    env_num: 2
    dataset: placeholder.jsonl
    env_params:
      locale: en-US
""".strip(),
        encoding="utf-8",
    )
    dataset.write_text('{"task": 1}\n{"task": 2}\n', encoding="utf-8")
    start.write_text(
        """
agent_name: browser
container:
  runner_entrypoint:
    source: /trusted/runner.py
    target: /app/runner.py
    command: python /app/runner.py
rjob:
  mount_config:
    - trusted-results:/app/results
""".strip(),
        encoding="utf-8",
    )
    return models, ranges


def write_initialization_config(root: Path) -> Path:
    path = root / "initialization.yaml"
    path.write_text(
        """
schema_version: "1.0"
gateway_base_image: registry/gateway@sha256:1
safactory_base_image: registry/controller@sha256:2
image_pull_policy: IfNotPresent
placeholder: true
""".strip(),
        encoding="utf-8",
    )
    return path


@dataclass
class FakeRJobClient:
    snapshots: dict[str, RJobSnapshot] = field(default_factory=dict)
    created: list[RJobSpec] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    preflight_calls: int = 0

    async def preflight(self) -> None:
        self.preflight_calls += 1

    async def create(self, spec: RJobSpec) -> str:
        self.created.append(spec)
        rjob_id = "rjob_gateway" if spec.role == "gateway" else "rjob_controller"
        if rjob_id not in self.snapshots:
            self.snapshots[rjob_id] = RJobSnapshot(rjob_id, RJobState.PENDING)
        return rjob_id

    async def get(self, rjob_id: str) -> RJobSnapshot:
        return self.snapshots[rjob_id]

    async def stop(self, rjob_id: str) -> None:
        self.stopped.append(rjob_id)


class ReadyHealthChecker:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def ready(self, gateway_url: str, health_path: str) -> bool:
        self.urls.append(f"{gateway_url}{health_path}")
        return True


class FakeDataPlatform:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def preflight(self) -> None:
        self.calls.append(("preflight", ()))

    async def list_session_ids(self, job_id: str) -> tuple[str, ...]:
        self.calls.append(("sessions", (job_id,)))
        return ("session_real_001",)

    async def get_result(self, job_id: str, session_id: str) -> SessionResult | None:
        self.calls.append(("result", (job_id, session_id)))
        if session_id != "session_real_001":
            return None
        return SessionResult(
            session_id,
            ResultStatus.SUCCEEDED,
            9.25,
            datetime(2026, 8, 22, 1, 2, 3, tzinfo=UTC),
        )

    async def get_steps(self, job_id: str, session_id: str) -> SessionSteps | None:
        self.calls.append(("steps", (job_id, session_id)))
        if session_id != "session_real_001":
            return None
        return SessionSteps(session_id, 1, True, (StepIndex("step_real_001", 1),))

    async def get_trajectory(
        self, job_id: str, session_id: str, step_id: str
    ) -> StepTrajectory | None:
        self.calls.append(("trajectory", (job_id, session_id, step_id)))
        if session_id != "session_real_001" or step_id != "step_real_001":
            return None
        timestamp = datetime(2026, 8, 22, 1, 2, 0, tzinfo=UTC)
        return StepTrajectory(
            session_id,
            step_id,
            1,
            timestamp,
            timestamp,
            {
                "model_input": {"messages": [], "api_key": "must-redact"},
                "model_output": {"content": "done"},
                "action": {},
                "observation": {},
            },
        )
