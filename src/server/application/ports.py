from __future__ import annotations

from typing import Protocol

from server.domain.entities import (
    CreatedJob,
    JobSessions,
    Model,
    Range,
    SessionMilestones,
    SessionResult,
    SessionSteps,
    StepTrajectory,
)


class CatalogPort(Protocol):
    async def list_models(self) -> tuple[Model, ...]: ...

    async def list_ranges(self) -> tuple[Range, ...]: ...

    async def get_model(self, model_id: str) -> Model | None: ...

    async def get_range(self, range_id: str) -> Range | None: ...


class RuntimePort(Protocol):
    async def create_job(self, model_id: str, range_id: str) -> CreatedJob: ...

    async def list_sessions(self, job_id: str) -> JobSessions: ...

    async def get_result(self, job_id: str, session_id: str) -> SessionResult: ...

    async def get_milestones(
        self, job_id: str, session_id: str
    ) -> SessionMilestones: ...

    async def get_steps(self, job_id: str, session_id: str) -> SessionSteps: ...

    async def get_trajectory(
        self, job_id: str, session_id: str, step_id: str
    ) -> StepTrajectory: ...
