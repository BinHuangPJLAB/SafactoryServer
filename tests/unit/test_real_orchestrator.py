from __future__ import annotations

import asyncio
import json
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
from server.infrastructure.real.file_manager import SharedFileManager
from server.infrastructure.real.orchestrator import RealJobOrchestrator
from server.infrastructure.real.rjob import RJobSnapshot, RJobState


def test_orders_recovers_and_cleans_top_level_rjobs(tmp_path: Path) -> None:
    asyncio.run(_exercise_orchestrator(tmp_path))


def test_gateway_failure_never_submits_controller(tmp_path: Path) -> None:
    asyncio.run(_exercise_gateway_failure(tmp_path))


async def _exercise_orchestrator(tmp_path: Path) -> None:
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
    assert rjobs.stopped == ["rjob_controller", "rjob_gateway"]
    events = await store.events(job.job_id)
    assert "job_succeeded" in {item["event_type"] for item in events}


async def _exercise_gateway_failure(tmp_path: Path) -> None:
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
    assert rjobs.stopped == ["rjob_gateway"]
