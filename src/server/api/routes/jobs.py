from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response, status

from server.api.dependencies import get_job_service
from server.api.schemas.common import ErrorBody, RequiredId
from server.api.schemas.jobs import (
    CloseJobResponse,
    CreatedJobResponse,
    CreateJobRequest,
    JobSessionsResponse,
)
from server.application.service import JobService
from server.domain.entities import JobStatus

router = APIRouter()


@router.post("/jobs", response_model=CreatedJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: CreateJobRequest,
    response: Response,
    service: Annotated[JobService, Depends(get_job_service)],
) -> CreatedJobResponse:
    job = await service.create_job(payload.model_id, payload.range_id)
    response.headers["Location"] = (
        f"/v1/jobs/sessions?job_id={quote(job.job_id, safe='')}"
    )
    return CreatedJobResponse.model_validate(job)


@router.post(
    "/jobs/close",
    response_model=CloseJobResponse,
    responses={status.HTTP_202_ACCEPTED: {"model": CloseJobResponse}},
)
async def close_job(
    job_id: RequiredId,
    response: Response,
    service: Annotated[JobService, Depends(get_job_service)],
) -> CloseJobResponse:
    result = await service.close_job(job_id)
    if result.job_status == JobStatus.CLOSING:
        response.status_code = status.HTTP_202_ACCEPTED
        response.headers["Location"] = (
            f"/v1/jobs/sessions?job_id={quote(result.job_id, safe='')}"
        )
        if result.retry_after_seconds is not None:
            response.headers["Retry-After"] = str(result.retry_after_seconds)
    return CloseJobResponse.model_validate(result)


@router.get("/jobs/sessions", response_model=JobSessionsResponse)
async def list_job_sessions(
    job_id: RequiredId,
    response: Response,
    service: Annotated[JobService, Depends(get_job_service)],
) -> JobSessionsResponse:
    result = await service.list_sessions(job_id)
    if result.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(result.retry_after_seconds)
    return JobSessionsResponse(
        job_id=result.job_id,
        job_status=result.job_status,
        session_ids=list(result.session_ids),
        error=None if result.error is None else ErrorBody.model_validate(result.error),
    )
