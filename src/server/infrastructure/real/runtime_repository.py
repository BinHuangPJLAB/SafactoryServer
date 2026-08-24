from __future__ import annotations

import logging
from collections.abc import Callable

from server.auth.context import get_authenticated_username, get_request_id
from server.domain.entities import (
    CreatedJob,
    FailureInfo,
    JobSessions,
    JobStatus,
    SessionResult,
    SessionSteps,
    StepTrajectory,
)
from server.domain.errors import DomainError, ErrorCode
from server.infrastructure.clock import Clock
from server.infrastructure.identifiers import IdentifierFactory
from server.infrastructure.real.configuration import RealCatalog, TrustedConfigError
from server.infrastructure.real.control_store import (
    SQLiteControlStore,
    new_control_job,
)
from server.infrastructure.real.data_platform import DataPlatformError, DataPlatformRepository

LOGGER = logging.getLogger("server.data_queries")


class RealRuntimeRepository:
    def __init__(
        self,
        *,
        catalog: RealCatalog,
        store: SQLiteControlStore,
        data: DataPlatformRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
        gateway_image: str,
        safactory_image: str,
        retry_after_seconds: int,
        wake_orchestrator: Callable[[], None],
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._data = data
        self._clock = clock
        self._identifiers = identifiers
        self._gateway_image = gateway_image
        self._safactory_image = safactory_image
        self._retry_after_seconds = retry_after_seconds
        self._wake_orchestrator = wake_orchestrator

    async def create_job(self, model_id: str, range_id: str) -> CreatedJob:
        try:
            model = self._catalog.resolve_model(model_id)
            range_config = self._catalog.resolve_range(range_id)
            model_checksum = self._catalog.model_checksum()
        except TrustedConfigError as exc:
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if model is None or range_config is None:
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE)

        job_id = self._identifiers.new("job")
        created_at = self._clock.now()
        job = new_control_job(
            job_id=job_id,
            request_id=get_request_id() or "req_internal",
            owner_username=get_authenticated_username() or "system",
            model_id=model_id,
            model_checksum=model_checksum,
            model_gateway_json=model.gateway.model_dump_json(),
            range_id=range_id,
            gateway_image=self._gateway_image,
            safactory_image=self._safactory_image,
            now=created_at,
        )
        await self._store.add(job)
        await self._store.add_event(
            job_id, "job_created", job.phase, created_at, {"model_id": model_id}
        )
        self._wake_orchestrator()
        return CreatedJob(job_id, JobStatus.QUEUED, model_id, range_id, created_at)

    async def list_sessions(self, job_id: str) -> JobSessions:
        job = await self._require_job(job_id)
        LOGGER.info("job_id=%s operation=list_sessions", job_id)
        try:
            session_ids = await self._data.list_session_ids(job_id)
        except DataPlatformError as exc:
            await self._query_failed(job_id, "list_sessions")
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        status = JobStatus(job.status)
        retry_after = None if job.terminal else self._retry_after_seconds
        failure = None
        if status == JobStatus.FAILED:
            failure = FailureInfo(
                code=job.status_reason or "JOB_EXECUTION_FAILED",
                message="The job failed during execution.",
                details={},
                retryable=False,
            )
        return JobSessions(job_id, status, session_ids, retry_after, failure)

    async def get_result(self, job_id: str, session_id: str) -> SessionResult:
        await self._require_job(job_id)
        LOGGER.info("job_id=%s session_id=%s operation=get_result", job_id, session_id)
        try:
            result = await self._data.get_result(job_id, session_id)
        except DataPlatformError as exc:
            await self._query_failed(job_id, "get_result")
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if result is None:
            raise DomainError(
                ErrorCode.SESSION_NOT_FOUND,
                {"job_id": job_id, "session_id": session_id},
            )
        if result.result_status.value in {"pending", "running"}:
            return SessionResult(
                result.session_id,
                result.result_status,
                result.score,
                result.completed_at,
                self._retry_after_seconds,
            )
        return result

    async def get_steps(self, job_id: str, session_id: str) -> SessionSteps:
        await self._require_job(job_id)
        LOGGER.info("job_id=%s session_id=%s operation=get_steps", job_id, session_id)
        try:
            result = await self._data.get_steps(job_id, session_id)
        except DataPlatformError as exc:
            await self._query_failed(job_id, "get_steps")
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if result is None:
            raise DomainError(
                ErrorCode.SESSION_NOT_FOUND,
                {"job_id": job_id, "session_id": session_id},
            )
        return result

    async def get_trajectory(
        self, job_id: str, session_id: str, step_id: str
    ) -> StepTrajectory:
        await self._require_job(job_id)
        LOGGER.info(
            "job_id=%s session_id=%s step_id=%s operation=get_trajectory",
            job_id,
            session_id,
            step_id,
        )
        try:
            result = await self._data.get_trajectory(job_id, session_id, step_id)
        except DataPlatformError as exc:
            await self._query_failed(job_id, "get_trajectory")
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if result is None:
            # Distinguish an unknown Session from a Step that is absent in a known Session.
            try:
                session = await self._data.get_steps(job_id, session_id)
            except DataPlatformError as exc:
                raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
            if session is None:
                raise DomainError(
                    ErrorCode.SESSION_NOT_FOUND,
                    {"job_id": job_id, "session_id": session_id},
                )
            raise DomainError(
                ErrorCode.STEP_NOT_FOUND,
                {"session_id": session_id, "step_id": step_id},
            )
        return result

    async def _require_job(self, job_id: str):
        job = await self._store.get(job_id)
        username = get_authenticated_username()
        if job is None or (username is not None and job.owner_username != username):
            raise DomainError(ErrorCode.JOB_NOT_FOUND, {"job_id": job_id})
        return job

    async def _query_failed(self, job_id: str, operation: str) -> None:
        job = await self._store.get(job_id)
        await self._store.add_event(
            job_id,
            "data_platform_query_failed",
            job.phase if job is not None else "unknown",
            self._clock.now(),
            {"operation": operation},
        )
