from typing import Annotated

from pydantic import Field, StringConstraints

from server.api.schemas.common import ApiModel, OptionalFailure, UtcTimestamp
from server.domain.entities import JobStatus

NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CreateJobRequest(ApiModel):
    model_id: NonBlankString
    range_id: NonBlankString


class CreatedJobResponse(ApiModel):
    job_id: str
    status: JobStatus
    model_id: str
    range_id: str
    created_at: UtcTimestamp


class CloseJobResponse(ApiModel):
    job_id: str
    job_status: JobStatus
    updated_at: UtcTimestamp


class JobSessionsResponse(ApiModel):
    job_id: str
    job_status: JobStatus
    session_ids: list[str]
    error: OptionalFailure = Field(default=None, exclude_if=lambda value: value is None)
