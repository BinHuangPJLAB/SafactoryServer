from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

from server.api.error_handlers import install_error_handlers
from server.api.middleware import install_request_middleware
from server.api.router import api_router
from server.application.service import JobService
from server.auth import BearerAuthenticator, load_auth_config
from server.config import Settings
from server.infrastructure.clock import Clock, SystemClock
from server.infrastructure.identifiers import IdentifierFactory, RandomIdentifierFactory
from server.infrastructure.real.configuration import (
    RealCatalog,
    TrustedConfigError,
    apply_initialization_config,
    load_initialization_config,
)
from server.infrastructure.real.control_store import SQLiteControlStore
from server.infrastructure.real.data_platform import (
    DataPlatformRepository,
    load_sdk_repository,
)
from server.infrastructure.real.file_manager import SharedFileManager
from server.infrastructure.real.orchestrator import RealJobOrchestrator
from server.infrastructure.real.rjob import (
    BrainPPRJobClient,
    GatewayHealthChecker,
    HttpGatewayHealthChecker,
    HttpRJobClient,
    RJobClient,
)
from server.infrastructure.real.runtime_repository import RealRuntimeRepository
from server.observability.logging import configure_logging

LOGGER = logging.getLogger("server.startup")


@dataclass(frozen=True, slots=True)
class RealDependencies:
    """Optional injection points for external RJob and data-platform services."""

    rjobs: RJobClient | None = None
    data_platform: DataPlatformRepository | None = None
    gateway_health: GatewayHealthChecker | None = None


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    identifiers: IdentifierFactory | None = None,
    real_dependencies: RealDependencies | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    clock = clock or SystemClock()
    identifiers = identifiers or RandomIdentifierFactory()

    initialization = load_initialization_config(settings.initialization_config_path)
    settings = apply_initialization_config(settings, initialization)
    log_file = settings.log_file_path or settings.control_db_path.with_name(
        "safactory-server.log"
    )
    configure_logging(
        settings.log_level,
        log_file,
        file_level=settings.log_file_level,
        max_bytes=settings.log_file_max_bytes,
        backup_count=settings.log_file_backup_count,
    )
    LOGGER.info(
        "logging_status=ready stdout_level=%s file_level=%s file=%s",
        settings.log_level,
        settings.log_file_level,
        log_file,
    )

    auth_config = load_auth_config(settings.auth_config_path)
    authenticator = BearerAuthenticator.from_config(auth_config)
    LOGGER.info("auth_status=ready trusted_users=%s", len(auth_config.users))

    dependencies = real_dependencies or RealDependencies()
    catalog = RealCatalog(
        settings.range_config_path,
        initialization.gateway.config["llm_routes"],
        initialization.catalog.environment_root,
    )
    store = SQLiteControlStore(settings.control_db_path)
    files = SharedFileManager(
        settings.shared_storage_root,
        catalog,
        rjob_root=settings.shared_storage_rjob_source,
        results_root=settings.results_root,
        results_rjob_root=settings.results_rjob_source,
        input_target=settings.environment_mount_dir,
        result_target=settings.results_mount_dir,
        gateway_config=initialization.gateway.config,
        gateway_config_mount_dir=settings.gateway_config_mount_dir,
        gateway_config_filename=settings.gateway_config_filename,
    )
    rjobs = dependencies.rjobs or _load_rjob_client(settings)
    _install_database_environment(settings.database_environment_json)
    data_platform = dependencies.data_platform or load_sdk_repository(
        settings.data_platform_factory
    )
    gateway_health = dependencies.gateway_health or HttpGatewayHealthChecker(
        settings.gateway_health_timeout_seconds,
        settings.safactory_storage_type,
    )
    orchestrator = RealJobOrchestrator(
        settings=settings,
        initialization=initialization,
        catalog=catalog,
        store=store,
        files=files,
        rjobs=rjobs,
        health=gateway_health,
        clock=clock,
    )
    runtime = RealRuntimeRepository(
        catalog=catalog,
        store=store,
        data=data_platform,
        clock=clock,
        identifiers=identifiers,
        gateway_image=initialization.gateway_base_image,
        safactory_image=initialization.safactory_base_image,
        retry_after_seconds=settings.retry_after_seconds,
        wake_orchestrator=orchestrator.wake,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await orchestrator.preflight()
        await data_platform.preflight()
        await orchestrator.start()
        LOGGER.info("dependency_status=ready")
        try:
            yield
        finally:
            await orchestrator.stop()

    application = FastAPI(
        title="Safactory Job API",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.job_service = JobService(catalog, runtime)
    application.state.orchestrator = orchestrator
    application.state.control_store = store
    application.state.initialization_config = initialization
    install_error_handlers(application)
    install_request_middleware(application, identifiers, authenticator)
    application.include_router(api_router)
    return application


def _load_rjob_client(settings: Settings) -> RJobClient:
    if settings.rjob_backend == "brainpp":
        if not settings.rjob_cluster_entry:
            raise TrustedConfigError(
                "brainpp RJob backend requires rjob.cluster_entry"
            )
        return BrainPPRJobClient(
            cluster_entry=settings.rjob_cluster_entry,
            namespace=settings.rjob_namespace,
            access_key=settings.rjob_access_key,
            secret_key=settings.rjob_secret_key,
            verifyssl=settings.rjob_verifyssl,
            retries=settings.rjob_retries,
            no_packaging=settings.rjob_no_packaging,
            gateway_port=settings.gateway_port,
        )
    if not settings.rjob_endpoint:
        raise TrustedConfigError("http RJob backend requires rjob.endpoint")
    return HttpRJobClient(
        settings.rjob_endpoint,
        settings.rjob_token,
        settings.rjob_request_timeout_seconds,
    )


def _install_database_environment(raw: str) -> None:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TrustedConfigError("invalid database environment JSON") from exc
    if not isinstance(values, dict):
        raise TrustedConfigError("database environment must be a JSON object")
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TrustedConfigError(
                "database environment names and values must be strings"
            )
        os.environ[key] = value


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        access_log=False,
        log_config=None,
    )
