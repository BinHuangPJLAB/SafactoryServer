from __future__ import annotations

import asyncio
import logging

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


class TerminalSDKClient:
    def list(self, names):
        return [
            Struct(
                status=Struct(
                    current=Struct(name="Succeeded"),
                    secret_key="must-not-be-logged",
                ),
                spec=Struct(
                    tasks={
                        "main": Struct(
                            replicaStatus=[
                                Struct(status="Succeeded", exitCode=0)
                            ]
                        )
                    }
                ),
            )
        ]

    def logs_rjob(self, name):
        return "controller completed all episodes successfully"


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
        resources={"cpu": 2, "memory_in_mb": 4096},
        requests={"cpu": 1, "memory_in_mb": 2048},
        daemon=True,
        restart_policy="Never",
    )
    job = adapter._build_job(spec)

    assert snapshot.state == RJobState.RUNNING
    assert snapshot.address == "100.103.153.109"
    assert snapshot.port == 8000
    assert job.spec.tasks["main"].mount_config == [
        "gpfs://shared/gateway:/app/runtime-config"
    ]
    assert job.spec.tasks["main"].daemon is True
    assert job.spec.tasks["main"].template.containers[0].resources.cpu == 2
    assert job.spec.tasks["main"].template.containers[0].requests.cpu == 1
    assert job.spec.tasks["main"].restart_policy == "Never"


def test_brainpp_adapter_records_terminal_status_and_logs(caplog) -> None:
    adapter = _adapter()
    adapter._client = TerminalSDKClient()
    caplog.set_level(logging.DEBUG, logger="server.rjob.detail")

    snapshot = asyncio.run(adapter.get("controller-job"))

    assert snapshot.state == RJobState.SUCCEEDED
    assert "terminal_state=succeeded" in caplog.text
    assert "controller completed all episodes successfully" in caplog.text
    assert '"secret_key": "<redacted>"' in caplog.text
    assert "must-not-be-logged" not in caplog.text
