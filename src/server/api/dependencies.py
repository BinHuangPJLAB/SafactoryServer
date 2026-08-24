from fastapi import Request

from server.application.service import JobService


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service

