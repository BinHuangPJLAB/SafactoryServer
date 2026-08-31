from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import FakeClock
from real_helpers import REAL_GATEWAY_ROUTES, FakeDataPlatform, write_real_configs

from server.domain.errors import DomainError, ErrorCode
from server.infrastructure.identifiers import RandomIdentifierFactory
from server.infrastructure.real.configuration import RealCatalog
from server.infrastructure.real.control_store import SQLiteControlStore, new_control_job
from server.infrastructure.real.file_manager import SharedFileManager
from server.infrastructure.real.runtime_repository import RealRuntimeRepository


def test_missing_milestone_snapshot_has_stable_running_and_terminal_states(
    tmp_path: Path,
) -> None:
    asyncio.run(_exercise_missing_snapshot_states(tmp_path))


async def _exercise_missing_snapshot_states(tmp_path: Path) -> None:
    clock = FakeClock()
    catalog = RealCatalog(write_real_configs(tmp_path), REAL_GATEWAY_ROUTES)
    store = SQLiteControlStore(tmp_path / "control.db")
    await store.initialize()
    binding = SharedFileManager(
        tmp_path / "shared",
        catalog,
        results_root=tmp_path / "results",
    ).bind("job_milestones", "range_real_001")
    model = catalog.resolve_model("kimi-k3")
    assert model is not None
    job = new_control_job(
        job_id="job_milestones",
        request_id="req_test",
        owner_username="test-user",
        model_id="kimi-k3",
        model_checksum=catalog.model_checksum(),
        model_gateway_json=model.gateway.model_dump_json(),
        range_id="range_real_001",
        gateway_image="gateway",
        safactory_image="safactory",
        now=clock.now(),
    )
    await store.add(job)
    await store.update(
        job.job_id,
        now=clock.now(),
        binding_json=binding.to_json(),
        status="running",
    )
    runtime = RealRuntimeRepository(
        catalog=catalog,
        store=store,
        data=FakeDataPlatform(),
        clock=clock,
        identifiers=RandomIdentifierFactory(),
        gateway_image="gateway",
        safactory_image="safactory",
        retry_after_seconds=3,
        wake_orchestrator=lambda: None,
    )

    pending = await runtime.get_milestones(
        job.job_id,
        "session_real_001",
    )
    assert pending.milestone_status == "pending"
    assert pending.snapshot is None
    assert pending.retry_after_seconds == 3

    await store.update(job.job_id, now=clock.now(), status="succeeded")
    try:
        await runtime.get_milestones(job.job_id, "session_real_001")
    except DomainError as exc:
        assert exc.code == ErrorCode.MILESTONES_NOT_FOUND
    else:
        raise AssertionError("terminal Job without milestones must fail")
