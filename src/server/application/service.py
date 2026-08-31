from __future__ import annotations

from dataclasses import replace
from typing import Any

from server.application.ports import CatalogPort, RuntimePort
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
from server.domain.errors import DomainError, ErrorCode

SENSITIVE_TRAJECTORY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "credential",
        "credential_ref",
        "host_path",
        "mount_source",
        "password",
        "refresh_token",
        "secret",
    }
)


class JobService:
    def __init__(self, catalog: CatalogPort, runtime: RuntimePort) -> None:
        self._catalog = catalog
        self._runtime = runtime

    async def list_models(self) -> tuple[Model, ...]:
        models = await self._catalog.list_models()
        return tuple(model for model in models if model.available)

    async def list_ranges(self) -> tuple[Range, ...]:
        ranges = await self._catalog.list_ranges()
        return tuple(range_config for range_config in ranges if range_config.available)

    async def create_job(self, model_id: str, range_id: str) -> CreatedJob:
        model = await self._catalog.get_model(model_id)
        if model is None:
            raise DomainError(ErrorCode.MODEL_NOT_FOUND, {"model_id": model_id})
        if not model.available:
            raise DomainError(ErrorCode.MODEL_NOT_AVAILABLE, {"model_id": model_id})

        range_config = await self._catalog.get_range(range_id)
        if range_config is None:
            raise DomainError(ErrorCode.RANGE_NOT_FOUND, {"range_id": range_id})
        if not range_config.available:
            raise DomainError(
                ErrorCode.RANGE_NOT_AVAILABLE,
                {"range_id": range_id},
                retryable=range_config.availability_retryable,
            )
        return await self._runtime.create_job(model_id, range_id)

    async def list_sessions(self, job_id: str) -> JobSessions:
        return await self._runtime.list_sessions(job_id)

    async def get_result(self, job_id: str, session_id: str) -> SessionResult:
        return await self._runtime.get_result(job_id, session_id)

    async def get_milestones(
        self, job_id: str, session_id: str
    ) -> SessionMilestones:
        result = await self._runtime.get_milestones(job_id, session_id)
        return replace(result, snapshot=_redact_sensitive_values(result.snapshot))

    async def get_steps(self, job_id: str, session_id: str) -> SessionSteps:
        return await self._runtime.get_steps(job_id, session_id)

    async def get_trajectory(
        self, job_id: str, session_id: str, step_id: str
    ) -> StepTrajectory:
        result = await self._runtime.get_trajectory(job_id, session_id, step_id)
        return replace(result, trajectory=_redact_sensitive_values(result.trajectory))


def _redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.lower().replace("-", "_") in SENSITIVE_TRAJECTORY_KEYS
                else _redact_sensitive_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value
