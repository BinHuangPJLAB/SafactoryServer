from typing import Annotated

from fastapi import APIRouter, Depends, Response

from server.api.dependencies import get_job_service
from server.api.schemas.ranges import RangeItem
from server.application.service import JobService

router = APIRouter()


@router.get("/ranges", response_model=list[RangeItem])
async def list_ranges(
    response: Response,
    service: Annotated[JobService, Depends(get_job_service)],
) -> list[RangeItem]:
    ranges = await service.list_ranges()
    response.headers["Cache-Control"] = "no-store"
    return [RangeItem.model_validate(item) for item in ranges]
