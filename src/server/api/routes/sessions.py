from typing import Annotated

from fastapi import APIRouter, Depends, Response

from server.api.dependencies import get_job_service
from server.api.schemas.common import RequiredId
from server.api.schemas.sessions import (
    SessionMilestonesResponse,
    SessionResultResponse,
    SessionStepsResponse,
    StepItem,
    StepTrajectoryResponse,
)
from server.application.service import JobService

router = APIRouter()


@router.get("/sessions/milestones", response_model=SessionMilestonesResponse)
async def get_session_milestones(
    job_id: RequiredId,
    session_id: RequiredId,
    response: Response,
    service: Annotated[JobService, Depends(get_job_service)],
) -> SessionMilestonesResponse:
    result = await service.get_milestones(job_id, session_id)
    response.headers["Cache-Control"] = "no-store"
    if result.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(result.retry_after_seconds)
    return SessionMilestonesResponse.model_validate(result)


@router.get("/sessions/result", response_model=SessionResultResponse)
async def get_session_result(
    job_id: RequiredId,
    session_id: RequiredId,
    response: Response,
    service: Annotated[JobService, Depends(get_job_service)],
) -> SessionResultResponse:
    result = await service.get_result(job_id, session_id)
    if result.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(result.retry_after_seconds)
    return SessionResultResponse.model_validate(result)


@router.get("/sessions/steps", response_model=SessionStepsResponse)
async def get_session_steps(
    job_id: RequiredId,
    session_id: RequiredId,
    service: Annotated[JobService, Depends(get_job_service)],
) -> SessionStepsResponse:
    result = await service.get_steps(job_id, session_id)
    return SessionStepsResponse(
        session_id=result.session_id,
        step_count=result.step_count,
        sealed=result.sealed,
        steps=[StepItem.model_validate(step) for step in result.steps],
    )


@router.get("/sessions/steps/trajectory", response_model=StepTrajectoryResponse)
async def get_step_trajectory(
    job_id: RequiredId,
    session_id: RequiredId,
    step_id: RequiredId,
    service: Annotated[JobService, Depends(get_job_service)],
) -> StepTrajectoryResponse:
    result = await service.get_trajectory(job_id, session_id, step_id)
    return StepTrajectoryResponse.model_validate(result)
