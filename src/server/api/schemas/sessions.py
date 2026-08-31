from __future__ import annotations

from typing import Any

from pydantic import Field

from server.api.schemas.common import ApiModel, OptionalFailure, UtcTimestamp
from server.domain.entities import MilestoneStatus, ResultStatus


class SessionResultResponse(ApiModel):
    session_id: str
    result_status: ResultStatus
    score: float | None
    completed_at: UtcTimestamp | None
    result: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    error: OptionalFailure = Field(default=None, exclude_if=lambda value: value is None)


class SessionMilestonesResponse(ApiModel):
    job_id: str
    session_id: str
    milestone_status: MilestoneStatus
    snapshot: dict[str, Any] | None


class StepItem(ApiModel):
    step_id: str
    sequence_no: int = Field(ge=1)


class SessionStepsResponse(ApiModel):
    session_id: str
    step_count: int = Field(ge=0)
    sealed: bool
    steps: list[StepItem]


class Trajectory(ApiModel):
    model_input: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    model_output: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    action: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    observation: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class StepTrajectoryResponse(ApiModel):
    session_id: str
    step_id: str
    sequence_no: int = Field(ge=1)
    started_at: UtcTimestamp | None
    finished_at: UtcTimestamp | None
    trajectory: Trajectory
