from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import re
import shlex
from datetime import datetime

from server.config import Settings
from server.domain.entities import JobStatus
from server.infrastructure.clock import Clock
from server.infrastructure.real.configuration import (
    GatewayModelConfig,
    InitializationConfig,
    RealCatalog,
    TrustedConfigError,
)
from server.infrastructure.real.control_store import (
    ControlJob,
    JobStateConflict,
    SQLiteControlStore,
    parse_timestamp,
)
from server.infrastructure.real.file_manager import (
    FileBindingError,
    JobFileBinding,
    SharedFileManager,
)
from server.infrastructure.real.rjob import (
    GatewayHealthChecker,
    MountSpec,
    RJobClient,
    RJobDependencyError,
    RJobSnapshot,
    RJobSpec,
    RJobState,
    gateway_command,
)

LOGGER = logging.getLogger("server.orchestrator")
DETAIL_LOGGER = logging.getLogger("server.orchestrator.detail")
_VERBOSE_EVENTS = {
    "rjob_reconciliation_started",
    "rjob_reconciliation_completed",
}


class RealJobOrchestrator:
    """Durable reconciliation loop for the two top-level RJobs."""

    def __init__(
        self,
        *,
        settings: Settings,
        initialization: InitializationConfig,
        catalog: RealCatalog,
        store: SQLiteControlStore,
        files: SharedFileManager,
        rjobs: RJobClient,
        health: GatewayHealthChecker,
        clock: Clock,
    ) -> None:
        self._settings = settings
        self._initialization = initialization
        self._catalog = catalog
        self._store = store
        self._files = files
        self._rjobs = rjobs
        self._health = health
        self._clock = clock
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def wake(self) -> None:
        self._wake.set()

    async def preflight(self) -> None:
        database_environment = _json_object(
            self._settings.database_environment_json, "database environment"
        )
        _json_object(self._settings.gateway_config_json, "Gateway configuration")
        _json_object(self._settings.gateway_resources_json, "gateway resources")
        _json_object(self._settings.gateway_requests_json, "gateway requests")
        _json_object(self._settings.safactory_resources_json, "controller resources")
        _json_object(self._settings.safactory_requests_json, "controller requests")
        _json_object(self._settings.episode_rjob_defaults_json, "episode defaults")
        _json_string_list(
            self._settings.safactory_launcher_args_json, "launcher arguments"
        )
        if (
            self._settings.rjob_backend == "brainpp"
            and self._settings.safactory_storage_type == "cloud"
        ):
            _validate_cloud_environment(database_environment)
        await self._catalog.preflight()
        self._files.preflight()
        await self._store.initialize()
        await self._rjobs.preflight()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="real-job-orchestrator")
        self.wake()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def reconcile_once(self) -> None:
        for job in await self._store.list_active():
            try:
                await self._reconcile(job)
            except (RJobDependencyError, TrustedConfigError) as exc:
                LOGGER.warning(
                    "job_id=%s phase=%s attempt=%s dependency_error=%s",
                    job.job_id,
                    job.phase,
                    job.orchestrator_attempt,
                    type(exc).__name__,
                )
                latest = await self._store.get(job.job_id)
                if latest is not None and (
                    (
                        latest.safactory_rjob_id is not None
                        and self._controller_timed_out(latest)
                    )
                    or (
                        latest.safactory_rjob_id is None
                        and self._timed_out(
                            latest, self._settings.gateway_ready_timeout_seconds
                        )
                    )
                ):
                    await self._fail(job.job_id, "DEPENDENCY_UNAVAILABLE")
            except JobStateConflict:
                LOGGER.info(
                    "job_id=%s event=execution_stopped_for_close",
                    job.job_id,
                )
            except Exception:
                LOGGER.exception(
                    "job_id=%s phase=%s unexpected_reconciliation_error",
                    job.job_id,
                    job.phase,
                )
                await self._fail(job.job_id, "INTERNAL_ORCHESTRATION_ERROR")

        for job in await self._store.list_cleanup_pending():
            await self._cleanup(job)

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            await self.reconcile_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._settings.orchestrator_poll_seconds
                )

    async def _reconcile(self, job: ControlJob) -> None:
        job = await self._store.update(
            job.job_id,
            now=self._clock.now(),
            orchestrator_attempt=job.orchestrator_attempt + 1,
        )
        if job.status == JobStatus.CLOSING.value:
            return
        await self._event(job, "rjob_reconciliation_started")

        if job.binding_json is None:
            job = await self._bind_files(job)
            if job.terminal or job.status == JobStatus.CLOSING.value:
                return

        binding = JobFileBinding.from_json(job.binding_json)
        latest = await self._store.get(job.job_id)
        if latest is None:  # pragma: no cover - jobs are not deleted
            return
        job = latest
        if job.status == JobStatus.CLOSING.value:
            return
        if job.gateway_rjob_id is None:
            job = await self._submit_gateway(job, binding)
            if (
                job.terminal
                or job.status == JobStatus.CLOSING.value
                or job.gateway_rjob_id is None
            ):
                return

        gateway = await self._rjobs.get(job.gateway_rjob_id)
        job = await self._record_gateway_status(job, gateway)
        if job.status == JobStatus.CLOSING.value:
            return
        if gateway.state in {RJobState.FAILED, RJobState.CANCELLED, RJobState.SUCCEEDED}:
            await self._fail(job.job_id, "GATEWAY_RJOB_FAILED")
            return

        if not self._gateway_has_route(gateway):
            if job.safactory_rjob_id is not None:
                await self._fail(job.job_id, "GATEWAY_LOST_DURING_RUN")
                return
            if self._timed_out(job, self._settings.gateway_ready_timeout_seconds):
                await self._fail(job.job_id, "GATEWAY_READY_TIMEOUT")
            return

        gateway_url = _gateway_url(
            self._settings.gateway_scheme, gateway.address, gateway.port
        )
        gateway_ready = await self._health.ready(
            gateway_url, self._settings.gateway_health_path
        )
        latest = await self._store.get(job.job_id)
        if latest is None:  # pragma: no cover - jobs are not deleted
            return
        job = latest
        if job.status == JobStatus.CLOSING.value:
            return
        if not gateway_ready:
            if job.safactory_rjob_id is not None:
                await self._fail(job.job_id, "GATEWAY_LOST_DURING_RUN")
                return
            job = await self._store.update(
                job.job_id,
                now=self._clock.now(),
                phase="checking_gateway_health",
            )
            if self._timed_out(job, self._settings.gateway_ready_timeout_seconds):
                await self._fail(job.job_id, "GATEWAY_HEALTH_TIMEOUT")
            return

        if job.gateway_url is not None and job.gateway_url != gateway_url:
            await self._fail(job.job_id, "GATEWAY_ADDRESS_CHANGED")
            return

        if job.gateway_url is None:
            job = await self._store.update(
                job.job_id,
                now=self._clock.now(),
                gateway_address=gateway.address,
                gateway_port=gateway.port,
                gateway_url=gateway_url,
                gateway_ready_at=_timestamp(self._clock.now()),
                phase="submitting_safactory_rjob",
            )
            if job.status == JobStatus.CLOSING.value:
                return
            await self._event(job, "gateway_rjob_ready")

        latest = await self._store.get(job.job_id)
        if latest is None:  # pragma: no cover - jobs are not deleted
            return
        job = latest
        if job.status == JobStatus.CLOSING.value:
            return
        if job.safactory_rjob_id is None:
            job = await self._submit_safactory(job, binding)
            if job.status == JobStatus.CLOSING.value:
                return

        # Gateway availability remains a hard invariant for the controller lifetime.
        if gateway.state != RJobState.RUNNING or not gateway.ready:
            await self._fail(job.job_id, "GATEWAY_LOST_DURING_RUN")
            return

        controller = await self._rjobs.get(job.safactory_rjob_id)
        job = await self._record_controller_status(job, controller)
        if job.status == JobStatus.CLOSING.value:
            return
        if controller.state == RJobState.SUCCEEDED:
            if not self._results_complete(binding, controller):
                await self._fail(job.job_id, "EPISODE_RESULTS_INCOMPLETE")
                return
            now = self._clock.now()
            job = await self._store.update(
                job.job_id,
                now=now,
                status=JobStatus.SUCCEEDED.value,
                phase="completed",
                completed_at=_timestamp(now),
                cleanup_requested=1,
            )
            await self._event(job, "job_succeeded")
            return
        if controller.state in {RJobState.FAILED, RJobState.CANCELLED}:
            await self._fail(job.job_id, "SAFACTORY_RJOB_FAILED")
            return
        if self._controller_timed_out(job):
            await self._fail(job.job_id, "SAFACTORY_RJOB_TIMEOUT")
            return
        await self._event(job, "rjob_reconciliation_completed")

    async def _bind_files(self, job: ControlJob) -> ControlJob:
        job = await self._store.update(
            job.job_id,
            now=self._clock.now(),
            status=JobStatus.PREPARING.value,
            phase="resolving_job_files",
        )
        try:
            binding = await asyncio.to_thread(
                self._files.bind, job.job_id, job.range_id
            )
        except FileBindingError as exc:
            await self._fail(job.job_id, "FILE_BINDING_FAILED")
            failed = await self._store.get(job.job_id)
            if failed is None:  # pragma: no cover - guarded by the durable insert
                raise RuntimeError("Job disappeared during file binding") from exc
            return failed
        job = await self._store.update(
            job.job_id,
            now=self._clock.now(),
            binding_json=binding.to_json(),
            episode_total=binding.total_episodes,
            phase="submitting_gateway_rjob",
        )
        await self._event(
            job,
            "job_files_bound",
            {"environment_groups": len(binding.groups), "episodes": binding.total_episodes},
        )
        return job

    async def _submit_gateway(
        self, job: ControlJob, binding: JobFileBinding
    ) -> ControlJob:
        try:
            gateway_model = GatewayModelConfig.model_validate_json(
                job.model_gateway_json
            )
        except ValueError as exc:
            raise TrustedConfigError("stored model snapshot is invalid") from exc
        database_environment = _string_environment(
            _json_object(
                self._settings.database_environment_json, "database environment"
            )
        )
        environment = {
            **gateway_model.environment,
            **database_environment,
            "PYTHONPATH": self._settings.gateway_workdir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "NO_PROXY": self._settings.runtime_no_proxy,
            "no_proxy": self._settings.runtime_no_proxy,
            "SAFACTORY_JOB_ID": job.job_id,
            "SAFACTORY_MODEL_ID": job.model_id,
            "SAFACTORY_MODEL_ROUTE": json.dumps(
                gateway_model.route, separators=(",", ":")
            ),
            "GATEWAY_LISTEN_PORT": str(self._settings.gateway_port),
        }
        spec = RJobSpec(
            name=_safe_rjob_name(
                f"{self._settings.gateway_name_prefix}-{job.job_id}"
            ),
            idempotency_key=f"{job.job_id}:gateway",
            namespace=self._settings.rjob_namespace,
            charged_group=self._settings.charged_group,
            image=job.gateway_image,
            image_pull_policy=self._initialization.image_pull_policy,
            role="gateway",
            labels={
                "safactory.brainpp.cn/job-id": job.job_id,
                "safactory.brainpp.cn/component": "gateway",
            },
            environment=environment,
            command=gateway_command(binding.gateway_config_path),
            working_dir=self._settings.gateway_workdir,
            mounts=(
                MountSpec(
                    binding.gateway_config_source,
                    self._settings.gateway_config_mount_dir,
                    True,
                ),
            ),
            resources=_json_object(
                self._settings.gateway_resources_json, "gateway resources"
            ),
            requests=_json_object(
                self._settings.gateway_requests_json, "gateway requests"
            ),
            timeout_seconds=self._settings.safactory_timeout_seconds,
            daemon=True,
            restart_policy=self._settings.rjob_restart_policy,
            private_machine=self._settings.rjob_private_machine,
            host_network=self._settings.rjob_host_network,
            auto_delete_duration=self._settings.rjob_auto_delete_duration,
        )
        rjob_id = await self._rjobs.create(spec)
        job = await self._store.update(
            job.job_id,
            now=self._clock.now(),
            gateway_rjob_id=rjob_id,
            gateway_status=RJobState.PENDING.value,
            phase="waiting_gateway_address",
        )
        await self._event(job, "gateway_rjob_submitted", {"rjob_id": rjob_id})
        return job

    async def _submit_safactory(
        self, job: ControlJob, binding: JobFileBinding
    ) -> ControlJob:
        if not job.gateway_url:
            raise RuntimeError("controller submission requires a verified Gateway URL")
        gateway_sessions_url = _append_url_path(
            job.gateway_url, self._settings.gateway_sessions_path
        )
        try:
            gateway_model = GatewayModelConfig.model_validate_json(
                job.model_gateway_json
            )
        except ValueError as exc:
            raise TrustedConfigError("stored model snapshot is invalid") from exc
        launcher_command = _launcher_command(
            settings=self._settings,
            binding=binding,
            job_id=job.job_id,
            gateway_url=gateway_sessions_url,
            llm_model=gateway_model.llm_model or job.model_id,
        )
        LOGGER.info(
            "request_id=%s job_id=%s component=controller "
            "event=rjob_launch_command command=%s",
            job.request_id,
            job.job_id,
            shlex.join(launcher_command),
        )
        database_environment = _string_environment(
            _json_object(
                self._settings.database_environment_json, "database environment"
            )
        )
        rjob_environment = {
            "RJOB_CLUSTER_ENTRY": self._settings.rjob_cluster_entry or "",
            "RJOB_NAMESPACE": self._settings.rjob_namespace,
            "RJOB_ACCESS_KEY": self._settings.rjob_access_key or "",
            "RJOB_SECRET_KEY": self._settings.rjob_secret_key or "",
            "RJOB_CHARGED_GROUP": self._settings.charged_group,
            "RJOB_GATEWAY_BASE_URL": gateway_sessions_url,
        }
        spec = RJobSpec(
            name=_safe_rjob_name(
                f"{self._settings.safactory_name_prefix}-{job.job_id}"
            ),
            idempotency_key=f"{job.job_id}:controller",
            namespace=self._settings.rjob_namespace,
            charged_group=self._settings.charged_group,
            image=job.safactory_image,
            image_pull_policy=self._initialization.image_pull_policy,
            role="safactory-controller",
            labels={
                "safactory.brainpp.cn/job-id": job.job_id,
                "safactory.brainpp.cn/component": "launcher",
            },
            environment={
                **database_environment,
                **rjob_environment,
                "PYTHONPATH": self._settings.safactory_workdir,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "NO_PROXY": self._settings.runtime_no_proxy,
                "no_proxy": self._settings.runtime_no_proxy,
                "SAFACTORY_JOB_ID": job.job_id,
                "SAFACTORY_MODEL_ID": job.model_id,
                "SAFACTORY_GATEWAY_URL": gateway_sessions_url,
                "SAFACTORY_AGENT_CONFIG": binding.agent_config_path,
                "SAFACTORY_INPUT_ROOT": binding.input_target,
                "SAFACTORY_RESULTS_ROOT": binding.result_target,
                "SAFACTORY_RJOB_NAMESPACE": self._settings.rjob_namespace,
                "SAFACTORY_CHARGED_GROUP": self._settings.charged_group,
                "SAFACTORY_RJOB_ENDPOINT": self._settings.rjob_endpoint or "",
                "SAFACTORY_RJOB_CREDENTIAL_REF": self._settings.rjob_credential_ref,
                "SAFACTORY_DATA_PLATFORM_CONFIG_REF": (
                    self._settings.data_platform_config_ref
                ),
                "SAFACTORY_EPISODE_RJOB_DEFAULTS": self._settings.episode_rjob_defaults_json,
            },
            command=launcher_command,
            working_dir=self._settings.safactory_workdir,
            mounts=(
                MountSpec(binding.input_source, binding.input_target, True),
                MountSpec(binding.result_source, binding.result_target, False),
            ),
            resources=_json_object(
                self._settings.safactory_resources_json, "controller resources"
            ),
            requests=_json_object(
                self._settings.safactory_requests_json, "controller requests"
            ),
            timeout_seconds=self._settings.safactory_timeout_seconds,
            restart_policy=self._settings.rjob_restart_policy,
            private_machine=self._settings.rjob_private_machine,
            host_network=self._settings.rjob_host_network,
            auto_delete_duration=self._settings.rjob_auto_delete_duration,
            max_running_duration=self._settings.rjob_max_running_duration,
        )
        rjob_id = await self._rjobs.create(spec)
        now = self._clock.now()
        try:
            job = await self._store.update(
                job.job_id,
                now=now,
                safactory_rjob_id=rjob_id,
                safactory_status=RJobState.PENDING.value,
                status=JobStatus.RUNNING.value,
                phase="running_safactory",
                started_at=job.started_at or _timestamp(now),
            )
        except JobStateConflict:
            # Close may win while the external create call is in flight. Persist the
            # returned RJob ID so the close reconciler can still remove it.
            job = await self._store.update(
                job.job_id,
                now=now,
                safactory_rjob_id=rjob_id,
                safactory_status=RJobState.PENDING.value,
                started_at=job.started_at or _timestamp(now),
            )
        await self._event(job, "safactory_rjob_submitted", {"rjob_id": rjob_id})
        return job

    async def _record_gateway_status(
        self, job: ControlJob, snapshot: RJobSnapshot
    ) -> ControlJob:
        self._log_rjob_snapshot(job, "gateway", job.gateway_status, snapshot)
        return await self._store.update(
            job.job_id,
            now=self._clock.now(),
            gateway_status=snapshot.state.value,
            gateway_exit_code=snapshot.exit_code,
            gateway_failure_summary=snapshot.failure_summary,
        )

    async def _record_controller_status(
        self, job: ControlJob, snapshot: RJobSnapshot
    ) -> ControlJob:
        self._log_rjob_snapshot(job, "controller", job.safactory_status, snapshot)
        summary = snapshot.workload_summary
        return await self._store.update(
            job.job_id,
            now=self._clock.now(),
            safactory_status=snapshot.state.value,
            safactory_exit_code=snapshot.exit_code,
            safactory_failure_summary=snapshot.failure_summary,
            episode_total=summary.get("total", job.episode_total),
            episode_running=summary.get("running", job.episode_running),
            episode_succeeded=summary.get("succeeded", job.episode_succeeded),
            episode_failed=summary.get("failed", job.episode_failed),
            episode_collected=summary.get("collected", job.episode_collected),
        )

    async def _fail(self, job_id: str, reason: str) -> None:
        job = await self._store.get(job_id)
        if job is None or job.status in {
            JobStatus.SUCCEEDED.value,
            JobStatus.CLOSING.value,
            JobStatus.CLOSED.value,
        }:
            return
        if job.status != JobStatus.FAILED.value:
            now = self._clock.now()
            try:
                job = await self._store.update(
                    job_id,
                    now=now,
                    status=JobStatus.FAILED.value,
                    phase="cleaning_rjobs",
                    status_reason=reason,
                    completed_at=_timestamp(now),
                    cleanup_requested=1,
                )
            except JobStateConflict:
                return
            await self._event(job, "job_failed", {"reason": reason})
        await self._cleanup(job)

    async def _cleanup(self, job: ControlJob) -> None:
        latest = await self._store.get(job.job_id)
        if latest is None:  # pragma: no cover - jobs are not deleted
            return
        job = latest
        closing = job.status == JobStatus.CLOSING.value

        if self._settings.keep_rjobs and not closing:
            await self._store.update(
                job.job_id,
                now=self._clock.now(),
                cleanup_requested=0,
            )
            await self._event(
                job,
                "rjob_cleanup_skipped",
                {
                    "reason": "SAFACTORY_KEEP_RJOBS",
                    "gateway_rjob_id": job.gateway_rjob_id,
                    "safactory_rjob_id": job.safactory_rjob_id,
                },
            )
            return

        # The controller owns episode RJobs; stopping it first triggers its idempotent
        # nested cleanup. The Gateway remains available until that request completes.
        try:
            if job.safactory_rjob_id:
                await self._event(job, "episode_cleanup_requested")
                await self._rjobs.stop(job.safactory_rjob_id)
                await self._event(job, "safactory_rjob_deleted")
            if job.gateway_rjob_id:
                await self._rjobs.stop(job.gateway_rjob_id)
                await self._event(job, "gateway_rjob_deleted")
        except RJobDependencyError:
            LOGGER.warning("job_id=%s cleanup_retry_pending", job.job_id)
            return
        now = self._clock.now()
        changes: dict[str, object] = {"cleanup_requested": 0}
        if closing:
            changes.update(
                status=JobStatus.CLOSED.value,
                phase="completed",
                completed_at=_timestamp(now),
                status_reason=None,
            )
        job = await self._store.update(job.job_id, now=now, **changes)
        await self._event(job, "cleanup_completed")
        if closing:
            await self._event(job, "job_closed")

    async def _event(
        self,
        job: ControlJob,
        event_type: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        logger = DETAIL_LOGGER if event_type in _VERBOSE_EVENTS else LOGGER
        logger_method = logger.debug if event_type in _VERBOSE_EVENTS else logger.info
        logger_method(
            "request_id=%s job_id=%s phase=%s attempt=%s event=%s "
            "gateway_rjob_id=%s safactory_rjob_id=%s details=%s",
            job.request_id,
            job.job_id,
            job.phase,
            job.orchestrator_attempt,
            event_type,
            job.gateway_rjob_id or "-",
            job.safactory_rjob_id or "-",
            json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        )
        await self._store.add_event(
            job.job_id, event_type, job.phase, self._clock.now(), payload
        )

    @staticmethod
    def _log_rjob_snapshot(
        job: ControlJob,
        component: str,
        previous_state: str | None,
        snapshot: RJobSnapshot,
    ) -> None:
        DETAIL_LOGGER.debug(
            "request_id=%s job_id=%s component=%s event=rjob_snapshot "
            "state=%s ready=%s address=%s port=%s exit_code=%s "
            "failure_summary=%s workload_summary=%s",
            job.request_id,
            job.job_id,
            component,
            snapshot.state.value,
            snapshot.ready,
            snapshot.address or "-",
            snapshot.port or "-",
            snapshot.exit_code if snapshot.exit_code is not None else "-",
            snapshot.failure_summary or "-",
            json.dumps(snapshot.workload_summary, sort_keys=True),
        )
        if previous_state != snapshot.state.value:
            LOGGER.info(
                "request_id=%s job_id=%s component=%s event=rjob_state_changed "
                "previous_state=%s platform_state=%s exit_code=%s "
                "failure_summary=%s",
                job.request_id,
                job.job_id,
                component,
                previous_state or "-",
                snapshot.state.value,
                snapshot.exit_code if snapshot.exit_code is not None else "-",
                snapshot.failure_summary or "-",
            )

    @staticmethod
    def _gateway_has_route(snapshot: RJobSnapshot) -> bool:
        if snapshot.state != RJobState.RUNNING or not snapshot.ready:
            return False
        if not snapshot.address or not snapshot.port:
            return False
        hostname = snapshot.address.strip().lower().rstrip(".")
        if hostname in {"localhost", "0.0.0.0"}:
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.split(".")
            return (
                len(hostname) <= 253
                and all(
                    label
                    and len(label) <= 63
                    and label[0].isalnum()
                    and label[-1].isalnum()
                    and all(character.isalnum() or character == "-" for character in label)
                    for label in labels
                )
            )
        return not (address.is_loopback or address.is_unspecified)

    @staticmethod
    def _results_complete(binding: JobFileBinding, snapshot: RJobSnapshot) -> bool:
        summary = snapshot.workload_summary
        # The direct brainpp API exposes only the outer launcher terminal state.
        # launcher.py itself exits successfully only after collecting its episode
        # outputs, matching create_safactory_rjob.py's proven completion contract.
        if not summary:
            return True
        expected = binding.total_episodes
        return (
            summary.get("total") == expected
            and summary.get("succeeded") == expected
            and summary.get("collected") == expected
            and summary.get("failed", 0) == 0
            and summary.get("running", 0) == 0
        )

    def _timed_out(self, job: ControlJob, timeout_seconds: int) -> bool:
        return (self._clock.now() - parse_timestamp(job.created_at)).total_seconds() > (
            timeout_seconds
        )

    def _controller_timed_out(self, job: ControlJob) -> bool:
        if job.started_at is None:
            return False
        return (self._clock.now() - parse_timestamp(job.started_at)).total_seconds() > (
            self._settings.safactory_timeout_seconds
        )


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_object(value: str, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise TrustedConfigError(f"invalid {label} JSON") from exc
    if not isinstance(parsed, dict):
        raise TrustedConfigError(f"{label} must be a JSON object")
    return parsed


def _json_string_list(value: str, label: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise TrustedConfigError(f"invalid {label} JSON") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise TrustedConfigError(f"{label} must be a JSON string array")
    return tuple(parsed)


def _string_environment(value: dict[str, object]) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise TrustedConfigError("database environment names must be non-empty strings")
        if not isinstance(item, (str, int, float, bool)):
            raise TrustedConfigError(
                f"database environment value must be scalar: {key}"
            )
        environment[key] = str(item)
    return environment


def _validate_cloud_environment(value: dict[str, object]) -> None:
    required = {
        "WT_SDK_PROFILE",
        "WT_SDK_DB_URI",
        "WT_SDK_ENV_CONFIG_DB_URI",
        "WT_SDK_S3_ENDPOINT",
        "WT_SDK_S3_ALLOW_HTTP",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_EC2_METADATA_DISABLED",
    }
    missing = sorted(name for name in required if not str(value.get(name) or "").strip())
    if missing:
        raise TrustedConfigError(
            "cloud database environment is incomplete: " + ", ".join(missing)
        )
    placeholders = sorted(
        name
        for name, item in value.items()
        if "YOUR_" in str(item).upper()
        or str(item).upper().startswith("REPLACE_ME")
    )
    if placeholders:
        raise TrustedConfigError(
            "cloud database environment contains placeholders: "
            + ", ".join(placeholders)
        )
    if str(value["WT_SDK_PROFILE"]).lower() not in {"test", "production"}:
        raise TrustedConfigError("WT_SDK_PROFILE must be test or production")
    for name in ("WT_SDK_DB_URI", "WT_SDK_ENV_CONFIG_DB_URI"):
        if not str(value[name]).startswith("s3://"):
            raise TrustedConfigError(f"{name} must use an s3:// URI")
    if not str(value["WT_SDK_S3_ENDPOINT"]).startswith(("http://", "https://")):
        raise TrustedConfigError("WT_SDK_S3_ENDPOINT must be an http(s) URL")
    for name in ("WT_SDK_S3_ALLOW_HTTP", "AWS_EC2_METADATA_DISABLED"):
        if str(value[name]).lower() not in {"true", "false", "1", "0", "yes", "no"}:
            raise TrustedConfigError(f"{name} must be a boolean value")


def _launcher_command(
    *,
    settings: Settings,
    binding: JobFileBinding,
    job_id: str,
    gateway_url: str,
    llm_model: str,
) -> tuple[str, ...]:
    launcher_rjob_config = (
        binding.launcher_rjob_config_path
        or settings.safactory_launcher_rjob_config
    )
    command = [
        settings.safactory_python_bin,
        "launcher.py",
        "--mode",
        "rjob",
        "--rjob-config",
        launcher_rjob_config,
        "--agent-config",
        binding.agent_config_path,
        "--agent-start-config",
        binding.agent_start_config_path,
        "--gateway-base-url",
        gateway_url,
        "--llm-model",
        llm_model,
        "--storage-type",
        settings.safactory_storage_type,
        "--job-id",
        job_id,
        "--pool-size",
        str(settings.safactory_pool_size),
        "--multiplier",
        str(settings.safactory_multiplier),
        "--max-workers",
        str(settings.safactory_max_workers),
        "--max-steps",
        str(settings.safactory_max_steps),
        "--agent-start-timeout-s",
        str(settings.safactory_agent_start_timeout_seconds),
    ]
    if settings.safactory_enable_evaluation:
        command.append("--enable-evaluation")
    command.extend(
        _json_string_list(
            settings.safactory_launcher_args_json, "launcher arguments"
        )
    )
    return tuple(command)


def _safe_rjob_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9.-]+", "-", value.lower()).strip("-.")
    return (normalized or "safactory")[:49].rstrip("-.")


def _append_url_path(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _gateway_url(scheme: str, address: str, port: int) -> str:
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError:
        host = address
    else:
        host = f"[{address}]" if parsed_address.version == 6 else address
    return f"{scheme}://{host}:{port}"
