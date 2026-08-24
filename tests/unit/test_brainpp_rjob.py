from __future__ import annotations

import asyncio

from server.infrastructure.real.rjob import (
    BrainPPRJobClient,
    MountSpec,
    RJobSpec,
    RJobState,
    gateway_command,
)


class Struct:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeSDKClient:
    def list(self, names):
        return [
            Struct(
                status=Struct(current=Struct(name="Running")),
                spec=Struct(
                    tasks={
                        "main": Struct(
                            replicaStatus=[Struct(podIP="100.103.153.109")]
                        )
                    }
                ),
            )
        ]


def _adapter() -> BrainPPRJobClient:
    adapter = BrainPPRJobClient.__new__(BrainPPRJobClient)
    adapter._symbols = {
        name: Struct
        for name in (
            "Job",
            "Metadata",
            "Spec",
            "Task",
            "Template",
            "Container",
            "Resources",
        )
    }
    adapter._client = FakeSDKClient()
    adapter._no_packaging = True
    adapter._gateway_port = 8000
    return adapter


def test_gateway_command_emits_ip_before_execing_gateway() -> None:
    command = gateway_command("/app/runtime-config/gateway.yaml")

    assert command[:2] == ("python", "-c")
    assert "SAFACTORY_RJOB_IP=" in command[2]
    assert "'-m', 'gateway'" in command[2]


def test_brainpp_adapter_reads_replica_ip_and_builds_sdk_mounts() -> None:
    adapter = _adapter()
    snapshot = asyncio.run(adapter.get("gateway-job"))
    spec = RJobSpec(
        name="gateway-job",
        idempotency_key="job:gateway",
        namespace="ns",
        charged_group="group",
        image="registry/image:safactory003",
        image_pull_policy="Always",
        role="gateway",
        labels={"component": "gateway"},
        environment={"PYTHONUNBUFFERED": "1"},
        command=gateway_command("/app/runtime-config/gateway.yaml"),
        mounts=(MountSpec("gpfs://shared/gateway", "/app/runtime-config", True),),
        daemon=True,
    )
    job = adapter._build_job(spec)

    assert snapshot.state == RJobState.RUNNING
    assert snapshot.address == "100.103.153.109"
    assert snapshot.port == 8000
    assert job.spec.tasks["main"].mount_config == [
        "gpfs://shared/gateway:/app/runtime-config"
    ]
    assert job.spec.tasks["main"].daemon is True
