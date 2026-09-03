from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from conftest import FakeClock
from real_helpers import (
    REAL_GATEWAY_ROUTES,
    FakeRJobClient,
    ReadyHealthChecker,
    write_initialization_config,
    write_real_configs,
)

from server.config import Settings
from server.infrastructure.real.configuration import (
    RealCatalog,
    load_initialization_config,
)
from server.infrastructure.real.control_store import SQLiteControlStore, new_control_job
from server.infrastructure.real.file_manager import (
    EnvironmentBinding,
    JobFileBinding,
    SharedFileManager,
)
from server.infrastructure.real.orchestrator import RealJobOrchestrator, _launcher_command
from server.infrastructure.real.rjob import RJobDependencyError, RJobSnapshot, RJobState


def test_orders_recovers_and_cleans_top_level_rjobs(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="server.orchestrator")
    asyncio.run(_exercise_orchestrator(tmp_path))
    launch_messages = [
        record.getMessage()
        for record in caplog.records
        if "event=rjob_launch_command" in record.getMessage()
    ]
    assert len(launch_messages) == 1
    assert "command=python launcher.py --mode rjob" in launch_messages[0]
    assert (
        "--gateway-base-url http://gateway.jobs.svc:8080/v1/sessions"
        in launch_messages[0]
    )
    assert "--llm-model kimi-k3" in launch_messages[0]


def test_keep_rjobs_skips_top_level_cleanup(tmp_path: Path) -> None:
    asyncio.run(_exercise_orchestrator(tmp_path, keep_rjobs=True))


def test_gateway_failure_never_submits_controller(tmp_path: Path) -> None:
    asyncio.run(_exercise_gateway_failure(tmp_path))


def test_keep_rjobs_skips_cleanup_after_failure(tmp_path: Path) -> None:
    asyncio.run(_exercise_gateway_failure(tmp_path, keep_rjobs=True))


def test_close_queued_job_is_idempotent_and_recoverable(tmp_path: Path) -> None:
    asyncio.run(_exercise_close_queued_job(tmp_path))


def test_close_running_job_cleans_controller_before_gateway(tmp_path: Path) -> None:
    asyncio.run(_exercise_close_running_job(tmp_path))


def test_explicit_close_overrides_keep_rjobs(tmp_path: Path) -> None:
    asyncio.run(_exercise_close_running_job(tmp_path, keep_rjobs=True))


def test_close_retries_transient_cleanup_failure(tmp_path: Path) -> None:
    asyncio.run(_exercise_close_running_job(tmp_path, fail_first_cleanup=True))


def test_launcher_uses_range_specific_rjob_config() -> None:
    binding = JobFileBinding(
        job_id="job_harbor",
        range_id="range_harbor",
        input_local_path="/shared/job_harbor",
        input_source="gpfs://shared/job_harbor",
        input_target="/app/env",
        result_local_path="/results/job_harbor",
        result_source="gpfs://results/job_harbor",
        result_target="/app/results",
        agent_config_path="/app/env/config.yaml",
        agent_start_config_path="/app/env/groups/harbor/start.rjob.yaml",
        agent_config_checksum="agent-checksum",
        groups=(
            EnvironmentBinding(
                env_name="harbor",
                env_image="registry/harbor:latest",
                env_num=1,
                dataset_records=1,
                dataset_checksum="dataset-checksum",
                start_config_checksum="start-checksum",
            ),
        ),
        total_episodes=1,
        launcher_rjob_config_path="/app/env/launcher.rjob.yaml",
        launcher_rjob_config_checksum="launcher-checksum",
    )

    command = _launcher_command(
        settings=Settings(safactory_launcher_rjob_config="global.rjob.yaml"),
        binding=binding,
        job_id=binding.job_id,
        gateway_url="http://gateway.example/v1/sessions",
        llm_model="kimi-k3",
    )

    config_index = command.index("--rjob-config")
    assert command[config_index + 1] == "/app/env/launcher.rjob.yaml"


async def _exercise_orchestrator(
    tmp_path: Path, *, keep_rjobs: bool = False
) -> None:
    ranges = write_real_configs(tmp_path)
    initialization = load_initialization_config(write_initialization_config(tmp_path))
    settings = Settings(
        auth_config_path=tmp_path / "unused-auth.yaml",
        initialization_config_path=tmp_path / "initialization.yaml",
        range_config_path=ranges,
        control_db_path=tmp_path / "control.db",
        shared_storage_root=tmp_path / "shared",
        rjob_endpoint="https://rjob.invalid",
        keep_rjobs=keep_rjobs,
    )
    clock = FakeClock(current=datetime(2026, 8, 22, tzinfo=UTC))
    catalog = RealCatalog(ranges, REAL_GATEWAY_ROUTES)
    store = SQLiteControlStore(settings.control_db_path)
    files = SharedFileManager(settings.shared_storage_root, catalog)
    rjobs = FakeRJobClient(
        snapshots={
            "rjob_gateway": RJobSnapshot(
                "rjob_gateway",
                RJobState.RUNNING,
                ready=True,
                address="gateway.jobs.svc",
                port=8080,
            ),
            "rjob_controller": RJobSnapshot(
                "rjob_controller", RJobState.RUNNING
            ),
        }
    )
    health = ReadyHealthChecker()
    orchestrator = RealJobOrchestrator(
        settings=settings,
        initialization=initialization,
        catalog=catalog,
        store=store,
        files=files,
        rjobs=rjobs,
        health=health,
        clock=clock,
    )
    await orchestrator.preflight()
    job = new_control_job(
        job_id="job_real_001",
        request_id="req_test",
        owner_username="test-user",
        model_id="kimi-k3",
        model_checksum=catalog.model_checksum(),
        model_gateway_json=(
            catalog.resolve_model("kimi-k3").gateway.model_dump_json()
        ),
        range_id="range_real_001",
        gateway_image=initialization.gateway_base_image,
        safactory_image=initialization.safactory_base_image,
        now=clock.now(),
    )
    await store.add(job)

    await orchestrator.reconcile_once()

    running = await store.get(job.job_id)
    assert running is not None
    assert running.status == "running"
    assert [spec.role for spec in rjobs.created] == ["gateway", "safactory-controller"]
    assert rjobs.created[1].environment["SAFACTORY_GATEWAY_URL"] == (
        "http://gateway.jobs.svc:8080/v1/sessions"
    )
    assert rjobs.created[0].command[:2] == ("python", "-c")
    assert rjobs.created[0].environment["SAFACTORY_MODEL_ID"] == "kimi-k3"
    assert json.loads(rjobs.created[0].environment["SAFACTORY_MODEL_ROUTE"]) == (
        REAL_GATEWAY_ROUTES["kimi-k3"]
    )
    assert rjobs.created[0].mounts[0].source.endswith(
        "/job_real_001/gateway"
    )
    assert rjobs.created[0].mounts[0].target == "/app/runtime-config"
    assert rjobs.created[0].mounts[0].read_only is True
    assert "/app/runtime-config/gateway.yaml" in rjobs.created[0].command[2]
    assert rjobs.created[1].command[:4] == (
        "python",
        "launcher.py",
        "--mode",
        "rjob",
    )
    rjob_config_index = rjobs.created[1].command.index("--rjob-config")
    assert rjobs.created[1].command[rjob_config_index + 1] == "config.yaml"
    assert "--agent-start-config" in rjobs.created[1].command
    llm_model_index = rjobs.created[1].command.index("--llm-model")
    assert rjobs.created[1].command[llm_model_index + 1] == "kimi-k3"
    assert rjobs.created[1].mounts[0].read_only is True
    assert health.urls == ["http://gateway.jobs.svc:8080/readyz"]

    restarted = RealJobOrchestrator(
        settings=settings,
        initialization=initialization,
        catalog=catalog,
        store=store,
        files=files,
        rjobs=rjobs,
        health=health,
        clock=clock,
    )
    await restarted.reconcile_once()
    assert len(rjobs.created) == 2

    rjobs.snapshots["rjob_controller"] = RJobSnapshot(
        "rjob_controller",
        RJobState.SUCCEEDED,
        workload_summary={
            "total": 4,
            "running": 0,
            "succeeded": 4,
            "failed": 0,
            "collected": 4,
        },
    )
    await restarted.reconcile_once()

    completed = await store.get(job.job_id)
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.cleanup_requested == 0
    unchanged, close_transitioned = await store.request_close(
        job.job_id, now=clock.now()
    )
    assert unchanged.status == "succeeded"
    assert close_transitioned is False
    assert rjobs.stopped == (
        [] if keep_rjobs else ["rjob_controller", "rjob_gateway"]
    )
    events = await store.events(job.job_id)
    event_types = {item["event_type"] for item in events}
    assert "job_succeeded" in event_types
    if keep_rjobs:
        assert "rjob_cleanup_skipped" in event_types
        skipped = next(
            item for item in events if item["event_type"] == "rjob_cleanup_skipped"
        )
        assert skipped["payload"] == {
            "reason": "SAFACTORY_KEEP_RJOBS",
            "gateway_rjob_id": "rjob_gateway",
            "safactory_rjob_id": "rjob_controller",
        }


async def _exercise_gateway_failure(
    tmp_path: Path, *, keep_rjobs: bool = False
) -> None:
    ranges = write_real_configs(tmp_path)
    initialization = load_initialization_config(write_initialization_config(tmp_path))
    settings = Settings(
        auth_config_path=tmp_path / "unused-auth.yaml",
        initialization_config_path=tmp_path / "initialization.yaml",
        range_config_path=ranges,
        control_db_path=tmp_path / "control.db",
        shared_storage_root=tmp_path / "shared",
        rjob_endpoint="https://rjob.invalid",
        keep_rjobs=keep_rjobs,
    )
    clock = FakeClock(current=datetime(2026, 8, 22, tzinfo=UTC))
    catalog = RealCatalog(ranges, REAL_GATEWAY_ROUTES)
    store = SQLiteControlStore(settings.control_db_path)
    rjobs = FakeRJobClient(
        snapshots={
            "rjob_gateway": RJobSnapshot(
                "rjob_gateway", RJobState.RUNNING, ready=False
            )
        }
    )
    orchestrator = RealJobOrchestrator(
        settings=settings,
        initialization=initialization,
        catalog=catalog,
        store=store,
        files=SharedFileManager(settings.shared_storage_root, catalog),
        rjobs=rjobs,
        health=ReadyHealthChecker(),
        clock=clock,
    )
    await orchestrator.preflight()
    await store.add(
        new_control_job(
            job_id="job_gateway_failure",
            request_id="req_test",
            owner_username="test-user",
            model_id="kimi-k3",
            model_checksum=catalog.model_checksum(),
            model_gateway_json=(
                catalog.resolve_model("kimi-k3").gateway.model_dump_json()
            ),
            range_id="range_real_001",
            gateway_image=initialization.gateway_base_image,
            safactory_image=initialization.safactory_base_image,
            now=clock.now(),
        )
    )

    await orchestrator.reconcile_once()
    assert [spec.role for spec in rjobs.created] == ["gateway"]

    rjobs.snapshots["rjob_gateway"] = RJobSnapshot(
        "rjob_gateway", RJobState.FAILED, exit_code=1
    )
    await orchestrator.reconcile_once()
    failed = await store.get("job_gateway_failure")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.status_reason == "GATEWAY_RJOB_FAILED"
    assert failed.safactory_rjob_id is None
    assert rjobs.stopped == ([] if keep_rjobs else ["rjob_gateway"])
    if keep_rjobs:
        assert failed.cleanup_requested == 0
        events = await store.events(failed.job_id)
        assert "rjob_cleanup_skipped" in {
            item["event_type"] for item in events
        }


async def _exercise_close_queued_job(tmp_path: Path) -> None:
    ranges = write_real_configs(tmp_path)
    initialization = load_initialization_config(write_initialization_config(tmp_path))
    settings = Settings(
        auth_config_path=tmp_path / "unused-auth.yaml",
        initialization_config_path=tmp_path / "initialization.yaml",
        range_config_path=ranges,
        control_db_path=tmp_path / "control.db",
        shared_storage_root=tmp_path / "shared",
        rjob_endpoint="https://rjob.invalid",
    )
    clock = FakeClock(current=datetime(2026, 9, 3, tzinfo=UTC))
    catalog = RealCatalog(ranges, REAL_GATEWAY_ROUTES)
    store = SQLiteControlStore(settings.control_db_path)
    rjobs = FakeRJobClient()
    orchestrator = RealJobOrchestrator(
        settings=settings,
        initialization=initialization,
        catalog=catalog,
        store=store,
        files=SharedFileManager(settings.shared_storage_root, catalog),
        rjobs=rjobs,
        health=ReadyHealthChecker(),
        clock=clock,
    )
    await orchestrator.preflight()
    job = new_control_job(
        job_id="job_close_queued",
        request_id="req_test",
        owner_username="test-user",
        model_id="kimi-k3",
        model_checksum=catalog.model_checksum(),
        model_gateway_json=catalog.resolve_model("kimi-k3").gateway.model_dump_json(),
        range_id="range_real_001",
        gateway_image=initialization.gateway_base_image,
        safactory_image=initialization.safactory_base_image,
        now=clock.now(),
    )
    await store.add(job)

    closing, transitioned = await store.request_close(job.job_id, now=clock.now())
    repeated, transitioned_again = await store.request_close(job.job_id, now=clock.now())

    assert closing.status == "closing"
    assert closing.cleanup_requested == 1
    assert transitioned is True
    assert repeated == closing
    assert transitioned_again is False

    await orchestrator.reconcile_once()

    closed = await store.get(job.job_id)
    assert closed is not None
    assert closed.status == "closed"
    assert closed.terminal is True
    assert closed.cleanup_requested == 0
    assert closed.completed_at is not None
    assert rjobs.created == []
    assert rjobs.stopped == []
    assert "job_closed" in {
        event["event_type"] for event in await store.events(job.job_id)
    }


async def _exercise_close_running_job(
    tmp_path: Path,
    *,
    keep_rjobs: bool = False,
    fail_first_cleanup: bool = False,
) -> None:
    ranges = write_real_configs(tmp_path)
    initialization = load_initialization_config(write_initialization_config(tmp_path))
    settings = Settings(
        auth_config_path=tmp_path / "unused-auth.yaml",
        initialization_config_path=tmp_path / "initialization.yaml",
        range_config_path=ranges,
        control_db_path=tmp_path / "control.db",
        shared_storage_root=tmp_path / "shared",
        rjob_endpoint="https://rjob.invalid",
        keep_rjobs=keep_rjobs,
    )
    clock = FakeClock(current=datetime(2026, 9, 3, tzinfo=UTC))
    catalog = RealCatalog(ranges, REAL_GATEWAY_ROUTES)
    store = SQLiteControlStore(settings.control_db_path)
    rjobs = FakeRJobClient(
        snapshots={
            "rjob_gateway": RJobSnapshot(
                "rjob_gateway",
                RJobState.RUNNING,
                ready=True,
                address="gateway.jobs.svc",
                port=8080,
            ),
            "rjob_controller": RJobSnapshot(
                "rjob_controller", RJobState.RUNNING
            ),
        }
    )
    orchestrator = RealJobOrchestrator(
        settings=settings,
        initialization=initialization,
        catalog=catalog,
        store=store,
        files=SharedFileManager(settings.shared_storage_root, catalog),
        rjobs=rjobs,
        health=ReadyHealthChecker(),
        clock=clock,
    )
    await orchestrator.preflight()
    job = new_control_job(
        job_id="job_close_running",
        request_id="req_test",
        owner_username="test-user",
        model_id="kimi-k3",
        model_checksum=catalog.model_checksum(),
        model_gateway_json=catalog.resolve_model("kimi-k3").gateway.model_dump_json(),
        range_id="range_real_001",
        gateway_image=initialization.gateway_base_image,
        safactory_image=initialization.safactory_base_image,
        now=clock.now(),
    )
    await store.add(job)
    await orchestrator.reconcile_once()
    running = await store.get(job.job_id)
    assert running is not None
    assert running.status == "running"

    await store.request_close(job.job_id, now=clock.now())
    original_stop = rjobs.stop
    if fail_first_cleanup:
        cleanup_failed = False

        async def fail_once(rjob_id: str) -> None:
            nonlocal cleanup_failed
            if not cleanup_failed:
                cleanup_failed = True
                raise RJobDependencyError("temporary cleanup failure")
            await original_stop(rjob_id)

        rjobs.stop = fail_once
    restarted = RealJobOrchestrator(
        settings=settings,
        initialization=initialization,
        catalog=catalog,
        store=store,
        files=SharedFileManager(settings.shared_storage_root, catalog),
        rjobs=rjobs,
        health=ReadyHealthChecker(),
        clock=clock,
    )
    await restarted.reconcile_once()

    if fail_first_cleanup:
        still_closing = await store.get(job.job_id)
        assert still_closing is not None
        assert still_closing.status == "closing"
        assert still_closing.cleanup_requested == 1
        rjobs.stop = original_stop
        await restarted.reconcile_once()

    closed = await store.get(job.job_id)
    assert closed is not None
    assert closed.status == "closed"
    assert closed.cleanup_requested == 0
    assert rjobs.stopped == ["rjob_controller", "rjob_gateway"]
