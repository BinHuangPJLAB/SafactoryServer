from typing import Annotated

from fastapi import APIRouter, Depends

from server.api.dependencies import get_job_service
from server.api.schemas.models import ModelItem, ModelsResponse
from server.application.service import JobService

router = APIRouter()


@router.get("/models", response_model=ModelsResponse)
async def list_models(
    service: Annotated[JobService, Depends(get_job_service)],
) -> ModelsResponse:
    models = await service.list_models()
    return ModelsResponse(items=[ModelItem.model_validate(model) for model in models])

