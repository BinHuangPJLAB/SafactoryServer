from fastapi import APIRouter

from server.api.routes import jobs, models, ranges, sessions

api_router = APIRouter(prefix="/v1")
api_router.include_router(models.router)
api_router.include_router(ranges.router)
api_router.include_router(jobs.router)
api_router.include_router(sessions.router)
