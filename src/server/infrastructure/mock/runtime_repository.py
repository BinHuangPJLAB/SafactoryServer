from __future__ import annotations

from datetime import timedelta

from server.domain.entities import (
    CreatedJob,
    JobSessions,
    JobStatus,
    ResultStatus,
    SessionResult,
    SessionSteps,
    StepIndex,
    StepTrajectory,
)
from server.domain.errors import DomainError, ErrorCode
from server.infrastructure.clock import Clock
from server.infrastructure.identifiers import IdentifierFactory
from server.infrastructure.mock.fixture_schema import FixtureDocument, ScenarioFixture
from server.infrastructure.mock.job_repository import (
    InMemoryJobRepository,
    MaterializedSession,
    MaterializedStep,
    MockJob,
)


class MockRuntimeRepository:
    def __init__(
        self,
        document: FixtureDocument | None,
        jobs: InMemoryJobRepository,
        clock: Clock,
        identifiers: IdentifierFactory,
        retry_after_seconds: int,
    ) -> None:
        self._document = document
        self._jobs = jobs
        self._clock = clock
        self._identifiers = identifiers
        self._retry_after_seconds = retry_after_seconds

    async def create_job(self, model_id: str, range_id: str) -> CreatedJob:
        scenario = self._scenario_for_range(range_id)
        job_id = self._identifiers.new("job")
        created_at = self._clock.now()
        sessions = tuple(
            MaterializedSession(
                session_id=self._identifiers.new("session"),
                fixture=session_fixture,
                steps=tuple(
                    MaterializedStep(
                        step_id=self._identifiers.new("step"),
                        sequence_no=sequence_no,
                        fixture=step_fixture,
                    )
                    for sequence_no, step_fixture in enumerate(
                        session_fixture.steps, start=1
                    )
                ),
            )
            for session_fixture in scenario.sessions
        )
        job = MockJob(
            job_id=job_id,
            model_id=model_id,
            range_id=range_id,
            created_at=created_at,
            created_monotonic=self._clock.monotonic(),
            scenario=scenario,
            sessions=sessions,
        )
        await self._jobs.add(job)
        return CreatedJob(
            job_id=job_id,
            status=JobStatus.QUEUED,
            model_id=model_id,
            range_id=range_id,
            created_at=created_at,
        )

    async def list_sessions(self, job_id: str) -> JobSessions:
        job = await self._require_job(job_id)
        elapsed_ms = self._elapsed_ms(job)
        visible_sessions = tuple(
            session.session_id
            for session in job.sessions
            if elapsed_ms >= session.fixture.visible_after_ms
        )
        status = self._job_status(job.scenario, elapsed_ms)
        retry_after = None if status in {JobStatus.SUCCEEDED, JobStatus.FAILED} else (
            self._retry_after_seconds
        )
        return JobSessions(job.job_id, status, visible_sessions, retry_after)

    async def get_result(self, job_id: str, session_id: str) -> SessionResult:
        job = await self._require_job(job_id)
        elapsed_ms = self._elapsed_ms(job)
        session = self._require_visible_session(job, session_id, elapsed_ms)
        result = session.fixture.result
        if elapsed_ms < result.running_after_ms:
            return SessionResult(
                session_id, ResultStatus.PENDING, None, None, self._retry_after_seconds
            )
        if elapsed_ms < result.completed_after_ms:
            return SessionResult(
                session_id, ResultStatus.RUNNING, None, None, self._retry_after_seconds
            )
        completed_at = job.created_at + timedelta(milliseconds=result.completed_after_ms)
        return SessionResult(
            session_id, ResultStatus.SUCCEEDED, result.score, completed_at, None
        )

    async def get_steps(self, job_id: str, session_id: str) -> SessionSteps:
        job = await self._require_job(job_id)
        elapsed_ms = self._elapsed_ms(job)
        session = self._require_visible_session(job, session_id, elapsed_ms)
        visible_steps = tuple(
            StepIndex(step.step_id, step.sequence_no)
            for step in session.steps
            if elapsed_ms >= step.fixture.visible_after_ms
        )
        sealed = elapsed_ms >= session.fixture.result.completed_after_ms
        return SessionSteps(
            session_id=session.session_id,
            step_count=len(visible_steps),
            sealed=sealed,
            steps=visible_steps,
        )

    async def get_trajectory(
        self, job_id: str, session_id: str, step_id: str
    ) -> StepTrajectory:
        job = await self._require_job(job_id)
        elapsed_ms = self._elapsed_ms(job)
        session = self._require_visible_session(job, session_id, elapsed_ms)
        step = next(
            (
                candidate
                for candidate in session.steps
                if candidate.step_id == step_id
                and elapsed_ms >= candidate.fixture.visible_after_ms
            ),
            None,
        )
        if step is None:
            raise DomainError(
                ErrorCode.STEP_NOT_FOUND,
                {"session_id": session_id, "step_id": step_id},
            )

        finished_at = job.created_at + timedelta(milliseconds=step.fixture.visible_after_ms)
        started_at = finished_at - timedelta(milliseconds=step.fixture.duration_ms)
        return StepTrajectory(
            session_id=session.session_id,
            step_id=step.step_id,
            sequence_no=step.sequence_no,
            started_at=started_at,
            finished_at=finished_at,
            trajectory=step.fixture.trajectory.model_dump(exclude_none=True),
        )

    def _scenario_for_range(self, range_id: str) -> ScenarioFixture:
        if self._document is None:
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        range_fixture = next(
            (item for item in self._document.ranges if item.range_id == range_id), None
        )
        if range_fixture is None:
            raise DomainError(ErrorCode.RANGE_NOT_FOUND, {"range_id": range_id})
        return self._document.scenarios[range_fixture.scenario_id]

    async def _require_job(self, job_id: str) -> MockJob:
        job = await self._jobs.get(job_id)
        if job is None:
            raise DomainError(ErrorCode.JOB_NOT_FOUND, {"job_id": job_id})
        return job

    @staticmethod
    def _require_visible_session(
        job: MockJob, session_id: str, elapsed_ms: int
    ) -> MaterializedSession:
        session = next(
            (
                candidate
                for candidate in job.sessions
                if candidate.session_id == session_id
                and elapsed_ms >= candidate.fixture.visible_after_ms
            ),
            None,
        )
        if session is None:
            raise DomainError(
                ErrorCode.SESSION_NOT_FOUND,
                {"job_id": job.job_id, "session_id": session_id},
            )
        return session

    def _elapsed_ms(self, job: MockJob) -> int:
        elapsed_seconds = self._clock.monotonic() - job.created_monotonic
        return max(0, round(elapsed_seconds * 1000))

    @staticmethod
    def _job_status(scenario: ScenarioFixture, elapsed_ms: int) -> JobStatus:
        completed_after_ms = max(
            session.result.completed_after_ms for session in scenario.sessions
        )
        first_session_after_ms = min(session.visible_after_ms for session in scenario.sessions)
        if elapsed_ms >= completed_after_ms:
            return JobStatus.SUCCEEDED
        if elapsed_ms >= first_session_after_ms:
            return JobStatus.RUNNING
        if elapsed_ms >= scenario.preparing_after_ms:
            return JobStatus.PREPARING
        return JobStatus.QUEUED
