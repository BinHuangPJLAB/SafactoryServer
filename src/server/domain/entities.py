from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ResultStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MilestoneStatus(StrEnum):
    PENDING = "pending"
    AVAILABLE = "available"


@dataclass(frozen=True, slots=True)
class Model:
    model_id: str
    name: str
    available: bool


@dataclass(frozen=True, slots=True)
class Range:
    range_id: str
    description: str
    available: bool
    availability_retryable: bool


@dataclass(frozen=True, slots=True)
class CreatedJob:
    job_id: str
    status: JobStatus
    model_id: str
    range_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FailureInfo:
    code: str
    message: str
    details: dict[str, Any]
    retryable: bool


@dataclass(frozen=True, slots=True)
class JobSessions:
    job_id: str
    job_status: JobStatus
    session_ids: tuple[str, ...]
    retry_after_seconds: int | None = None
    error: FailureInfo | None = None


@dataclass(frozen=True, slots=True)
class SessionResult:
    session_id: str
    result_status: ResultStatus
    score: float | None
    completed_at: datetime | None
    retry_after_seconds: int | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SessionMilestones:
    job_id: str
    session_id: str
    milestone_status: MilestoneStatus
    snapshot: dict[str, Any] | None
    retry_after_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class StepIndex:
    step_id: str
    sequence_no: int


@dataclass(frozen=True, slots=True)
class SessionSteps:
    session_id: str
    step_count: int
    sealed: bool
    steps: tuple[StepIndex, ...]


@dataclass(frozen=True, slots=True)
class StepTrajectory:
    session_id: str
    step_id: str
    sequence_no: int
    started_at: datetime | None
    finished_at: datetime | None
    trajectory: dict[str, Any]
