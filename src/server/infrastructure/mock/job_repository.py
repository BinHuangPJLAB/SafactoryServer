from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from server.infrastructure.mock.fixture_schema import ScenarioFixture, SessionFixture, StepFixture


@dataclass(frozen=True, slots=True)
class MaterializedStep:
    step_id: str
    sequence_no: int
    fixture: StepFixture


@dataclass(frozen=True, slots=True)
class MaterializedSession:
    session_id: str
    fixture: SessionFixture
    steps: tuple[MaterializedStep, ...]


@dataclass(frozen=True, slots=True)
class MockJob:
    job_id: str
    model_id: str
    range_id: str
    created_at: datetime
    created_monotonic: float
    scenario: ScenarioFixture
    sessions: tuple[MaterializedSession, ...]


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, MockJob] = {}
        self._lock = asyncio.Lock()

    async def add(self, job: MockJob) -> None:
        async with self._lock:
            if job.job_id in self._jobs:
                raise ValueError(f"Duplicate job ID: {job.job_id}")
            self._jobs[job.job_id] = job

    async def get(self, job_id: str) -> MockJob | None:
        async with self._lock:
            return self._jobs.get(job_id)

