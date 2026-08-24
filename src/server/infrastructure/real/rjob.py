from __future__ import annotations

import asyncio
import contextlib
import http.client
import inspect
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class RJobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MountSpec:
    source: str
    target: str
    read_only: bool


@dataclass(frozen=True, slots=True)
class RJobSpec:
    name: str
    idempotency_key: str
    namespace: str
    charged_group: str
    image: str
    image_pull_policy: str
    role: str
    labels: dict[str, str]
    environment: dict[str, str]
    command: tuple[str, ...] = ()
    working_dir: str = "/app"
    mounts: tuple[MountSpec, ...] = ()
    resources: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 3600
    daemon: bool = False
    private_machine: str | None = "Group"
    host_network: bool | None = None
    auto_delete_duration: str | None = "12h"
    max_running_duration: str | None = None


@dataclass(frozen=True, slots=True)
class RJobSnapshot:
    rjob_id: str
    state: RJobState
    ready: bool = False
    address: str | None = None
    port: int | None = None
    exit_code: int | None = None
    failure_summary: str | None = None
    workload_summary: dict[str, int] = field(default_factory=dict)


class RJobClient(Protocol):
    async def preflight(self) -> None: ...

    async def create(self, spec: RJobSpec) -> str: ...

    async def get(self, rjob_id: str) -> RJobSnapshot: ...

    async def stop(self, rjob_id: str) -> None: ...


class GatewayHealthChecker(Protocol):
    async def ready(self, gateway_url: str, health_path: str) -> bool: ...


class RJobDependencyError(RuntimeError):
    """The RJob control plane could not complete an operation."""


def gateway_command(config_target: str) -> tuple[str, ...]:
    """Build the same IP-emitting Gateway bootstrap used by create_test_rjob.py."""
    bootstrap = (
        "import ipaddress\n"
        "import os\n"
        "import socket\n"
        "import sys\n"
        "addresses = []\n"
        "for info in socket.getaddrinfo(socket.gethostname(), None):\n"
        "    candidate = info[4][0]\n"
        "    try:\n"
        "        address = ipaddress.ip_address(candidate)\n"
        "    except ValueError:\n"
        "        continue\n"
        "    if (address.is_loopback or address.is_link_local "
        "or address.is_multicast or address.is_unspecified):\n"
        "        continue\n"
        "    if str(address) not in addresses:\n"
        "        addresses.append(str(address))\n"
        "if not addresses:\n"
        "    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "    try:\n"
        "        probe.connect(('10.68.0.1', 443))\n"
        "        addresses.append(probe.getsockname()[0])\n"
        "    finally:\n"
        "        probe.close()\n"
        "if not addresses:\n"
        "    raise RuntimeError('could not resolve a non-loopback RJob IP')\n"
        "print('SAFACTORY_RJOB_IP=' + addresses[0], flush=True)\n"
        f"os.execv(sys.executable, [sys.executable, '-m', 'gateway', "
        f"'--config', {config_target!r}])\n"
    )
    return ("python", "-c", bootstrap)


class BrainPPRJobClient:
    """Direct ``brainpp.rjob`` adapter based on the two proven CLI scripts."""

    def __init__(
        self,
        *,
        cluster_entry: str,
        namespace: str,
        access_key: str | None,
        secret_key: str | None,
        verifyssl: bool,
        retries: int,
        no_packaging: bool,
        gateway_port: int,
    ) -> None:
        self._symbols = _brainpp_symbols()
        client_kwargs = {
            "cluster_entry": cluster_entry,
            "namespace": namespace,
            "access_key": access_key,
            "secret_key": secret_key,
            "verifyssl": verifyssl,
            "retries": retries,
        }
        self._client = self._symbols["RJobClient"](
            **{key: value for key, value in client_kwargs.items() if value is not None}
        )
        self._no_packaging = no_packaging
        self._gateway_port = gateway_port

    async def preflight(self) -> None:
        try:
            await asyncio.to_thread(self._client.list, ["safactory-preflight-probe"])
        except Exception as exc:
            raise RJobDependencyError("brainpp RJob preflight failed") from exc

    async def create(self, spec: RJobSpec) -> str:
        try:
            existing = await asyncio.to_thread(self._client.list, [spec.name])
            if existing:
                return spec.name
            job = self._build_job(spec)
            submit = self._client.submit
            kwargs: dict[str, Any] = {
                "no_packaging": self._no_packaging,
                "dry_run": False,
                "predict_only": False,
                "top": False,
                "name_normalized": True,
            }
            try:
                parameters = inspect.signature(submit).parameters
            except (TypeError, ValueError):
                parameters = {}
            if parameters and not any(
                item.kind == inspect.Parameter.VAR_KEYWORD
                for item in parameters.values()
            ):
                kwargs = {key: value for key, value in kwargs.items() if key in parameters}
            submitted = await asyncio.to_thread(submit, job, **kwargs)
            return str(submitted or spec.name).strip()
        except RJobDependencyError:
            raise
        except Exception as exc:
            raise RJobDependencyError("brainpp RJob submission failed") from exc

    async def get(self, rjob_id: str) -> RJobSnapshot:
        try:
            jobs = await asyncio.to_thread(self._client.list, [rjob_id])
            if not jobs:
                return RJobSnapshot(rjob_id, RJobState.UNKNOWN)
            job = jobs[0]
            state = _brainpp_state(_member(job, "status"))
            addresses = _extract_rjob_ips(_rjob_replica_statuses(jobs))
            if not addresses:
                addresses = _extract_rjob_ips(jobs)
            if not addresses and state == RJobState.RUNNING:
                logs = await self._logs(rjob_id)
                logged_address = _extract_logged_rjob_ip(logs)
                if logged_address:
                    addresses = [logged_address]
            address = addresses[0] if addresses else None
            return RJobSnapshot(
                rjob_id=rjob_id,
                state=state,
                ready=state == RJobState.RUNNING,
                address=address,
                port=self._gateway_port if address else None,
                exit_code=_extract_optional_int(job, {"exitcode", "exit_code"}),
                failure_summary=(
                    f"RJob status={_status_text(_member(job, 'status'))}"
                    if state in {RJobState.FAILED, RJobState.CANCELLED}
                    else None
                ),
            )
        except Exception as exc:
            raise RJobDependencyError("brainpp RJob status query failed") from exc

    async def stop(self, rjob_id: str) -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._client.stop, rjob_id)
        try:
            try:
                await asyncio.to_thread(self._client.delete, [rjob_id], async_=True)
            except TypeError:
                await asyncio.to_thread(self._client.delete, [rjob_id])
        except Exception as exc:
            raise RJobDependencyError("brainpp RJob cleanup failed") from exc

    async def _logs(self, rjob_id: str) -> str:
        try:
            raw = await asyncio.to_thread(self._client.logs_rjob, rjob_id)
        except Exception:
            return ""
        return _logs_text(raw)

    def _build_job(self, spec: RJobSpec) -> Any:
        symbols = self._symbols
        resources = _resources_struct(symbols, spec.resources)
        image_pull_policy = _coerce_enum(
            symbols.get("ImagePullPolicy"), spec.image_pull_policy
        )
        container_kwargs: dict[str, Any] = {
            "name": "main",
            "image": spec.image,
            "command": list(spec.command),
            "environments": spec.environment,
            "working_dir": spec.working_dir,
        }
        if resources is not None:
            container_kwargs["resources"] = resources
        if image_pull_policy is not None:
            container_kwargs["image_pull_policy"] = image_pull_policy
        container = _make_struct(symbols["Container"], **container_kwargs)
        template = _make_struct(
            symbols["Template"],
            containers=[container],
            environments=spec.environment,
            working_dir=spec.working_dir,
        )
        task_kwargs: dict[str, Any] = {
            "replicas": 1,
            "template": template,
            "daemon": spec.daemon,
        }
        restart_policy = _coerce_enum(symbols.get("RestartPolicy"), "Never")
        if restart_policy is not None:
            task_kwargs["restart_policy"] = restart_policy
        private_machine = _coerce_enum(
            symbols.get("PrivateMachine"), spec.private_machine
        )
        if private_machine is not None:
            task_kwargs["private_machine"] = private_machine
        if spec.mounts:
            task_kwargs["mount_config"] = [
                f"{mount.source}:{mount.target}" for mount in spec.mounts
            ]
        if spec.host_network is not None:
            task_kwargs["host_network"] = spec.host_network
        if spec.max_running_duration:
            task_kwargs["max_running_duration"] = spec.max_running_duration
        task = _make_struct(symbols["Task"], **task_kwargs)
        spec_kwargs: dict[str, Any] = {"tasks": {"main": task}}
        if spec.auto_delete_duration:
            spec_kwargs["auto_delete_duration"] = spec.auto_delete_duration
        if spec.host_network is not None:
            spec_kwargs["host_network"] = spec.host_network
        job_spec = _make_struct(symbols["Spec"], **spec_kwargs)
        metadata_kwargs: dict[str, Any] = {
            "name": spec.name,
            "labels": spec.labels,
            "annotations": spec.labels,
        }
        if spec.charged_group:
            metadata_kwargs["charged_group"] = spec.charged_group
        metadata = _make_struct(symbols["Metadata"], **metadata_kwargs)
        return _make_struct(symbols["Job"], metadata=metadata, spec=job_spec)


def _brainpp_symbols() -> dict[str, Any]:
    try:
        import brainpp.rjob as rjob_mod
        from brainpp.rjob import RJobClient
    except ImportError as exc:
        raise RJobDependencyError(
            "brainpp.rjob is required for the direct RJob backend"
        ) from exc
    try:
        import brainpp.rjob.struct as struct_mod
    except ImportError:
        struct_mod = rjob_mod
    symbols: dict[str, Any] = {"RJobClient": RJobClient}
    names = (
        "Job",
        "Metadata",
        "Spec",
        "Task",
        "Template",
        "Container",
        "Resources",
        "RestartPolicy",
        "PrivateMachine",
        "ImagePullPolicy",
    )
    for name in names:
        value = getattr(rjob_mod, name, None) or getattr(struct_mod, name, None)
        if value is not None:
            symbols[name] = value
    required = {"Job", "Metadata", "Spec", "Task", "Template", "Container", "Resources"}
    missing = sorted(required - symbols.keys())
    if missing:
        raise RJobDependencyError(f"brainpp.rjob is missing structs: {missing}")
    return symbols


def _make_struct(cls: Any, **kwargs: Any) -> Any:
    try:
        signature = inspect.signature(cls)
    except (TypeError, ValueError):
        return cls(**kwargs)
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return cls(**kwargs)
    return cls(**{key: value for key, value in kwargs.items() if key in signature.parameters})


def _resources_struct(symbols: dict[str, Any], config: dict[str, Any]) -> Any | None:
    if not config:
        return None
    supported = {
        key: value
        for key, value in config.items()
        if key
        in {
            "cpu",
            "gpu",
            "memory_in_mb",
            "ephemeral_storage_in_mb",
            "custom_resources",
        }
        and value is not None
    }
    custom = supported.get("custom_resources")
    if isinstance(custom, dict):
        supported["custom_resources"] = [
            f"{name}={quantity}" for name, quantity in custom.items()
        ]
    return _make_struct(symbols["Resources"], **supported) if supported else None


def _coerce_enum(enum_cls: Any, value: Any) -> Any:
    text = str(value or "").strip()
    if not text or enum_cls is None:
        return text or None
    for candidate in (text, text.lower(), text.upper(), text.capitalize()):
        if hasattr(enum_cls, candidate):
            return getattr(enum_cls, candidate)
    try:
        for item in enum_cls:
            if str(getattr(item, "name", "")).lower() == text.lower() or str(
                getattr(item, "value", "")
            ).lower() == text.lower():
                return item
    except TypeError:
        pass
    return text


def _member(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _status_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    current = _member(value, "current")
    if current is not None:
        return str(_member(current, "name") or current)
    return str(_member(value, "name") or value or "Unknown")


def _brainpp_state(value: Any) -> RJobState:
    normalized = _status_text(value).strip().lower()
    if normalized == "running":
        return RJobState.RUNNING
    if normalized == "succeeded":
        return RJobState.SUCCEEDED
    if normalized == "failed":
        return RJobState.FAILED
    if normalized in {"stopped", "killed", "killing", "deleting"}:
        return RJobState.CANCELLED
    if normalized in {"created", "pending", "starting", "inqueue", "restarting"}:
        return RJobState.PENDING
    return RJobState.UNKNOWN


def _rjob_replica_statuses(jobs: Any) -> list[Any]:
    values = jobs if isinstance(jobs, (list, tuple, set)) else [jobs]
    statuses: list[Any] = []
    for job in values:
        tasks = _member(_member(job, "spec"), "tasks")
        task_values = tasks.values() if isinstance(tasks, dict) else tasks or ()
        for task in task_values:
            replicas = _member(task, "replicaStatus") or _member(task, "replica_status")
            if replicas is None:
                continue
            if isinstance(replicas, (list, tuple, set)):
                statuses.extend(replicas)
            else:
                statuses.append(replicas)
    return statuses


def _extract_rjob_ips(payload: Any) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    visited: set[int] = set()
    sequence = 0

    def add(text: str, path: tuple[str, ...]) -> None:
        nonlocal sequence
        for candidate in re.findall(
            r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])", text
        ):
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if (
                address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
            ):
                continue
            key = re.sub(r"[^a-z0-9]", "", path[-1].lower()) if path else ""
            status_path = any(
                "status" in re.sub(r"[^a-z0-9]", "", part.lower())
                or "network" in re.sub(r"[^a-z0-9]", "", part.lower())
                for part in path[:-1]
            )
            if key in {"podip", "containerip", "privateip", "ipaddress"}:
                priority = 100
            elif key == "ip":
                priority = 90
            elif key in {"hostip", "nodeip"}:
                priority = 80
            elif status_path and key in {"address", "endpoint", "host"}:
                priority = 50
            else:
                continue
            matches.append((priority, sequence, str(address)))
            sequence += 1

    def visit(value: Any, path: tuple[str, ...], depth: int) -> None:
        if value is None or depth > 12:
            return
        if isinstance(value, str):
            add(value, path)
            return
        if isinstance(value, (int, float, bool, bytes)):
            return
        identity = id(value)
        if identity in visited:
            return
        visited.add(identity)
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, (*path, str(key)), depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)), depth + 1)
            return
        for name in (
            "pod_ip",
            "podIP",
            "container_ip",
            "containerIP",
            "private_ip",
            "privateIP",
            "ip_address",
            "ipAddress",
            "ip",
            "host_ip",
            "hostIP",
            "node_ip",
            "nodeIP",
        ):
            try:
                attribute = getattr(value, name)
            except (AttributeError, TypeError, ValueError):
                continue
            visit(attribute, (*path, name), depth + 1)
        for method_name in ("model_dump", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                with contextlib.suppress(Exception):
                    visit(method(), path, depth + 1)
                return
        try:
            visit(
                {
                    key: item
                    for key, item in vars(value).items()
                    if not str(key).startswith("_")
                },
                path,
                depth + 1,
            )
        except TypeError:
            return

    visit(payload, (), 0)
    ordered: list[str] = []
    for _priority, _sequence, address in sorted(matches, key=lambda item: (-item[0], item[1])):
        if address not in ordered:
            ordered.append(address)
    return ordered


def _extract_logged_rjob_ip(logs: str) -> str | None:
    for candidate in re.findall(r"\bSAFACTORY_RJOB_IP=([0-9A-Fa-f:.]+)", logs):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            return str(address)
    return None


def _logs_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return "\n".join(_logs_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_logs_text(item) for item in value)
    return str(value or "")


def _extract_optional_int(value: Any, names: set[str], depth: int = 0) -> int | None:
    if value is None or depth > 10:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in names and isinstance(item, int) and not isinstance(item, bool):
                return item
        for item in value.values():
            found = _extract_optional_int(item, names, depth + 1)
            if found is not None:
                return found
        return None
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _extract_optional_int(method(), names, depth + 1)
            except Exception:
                return None
    try:
        return _extract_optional_int(vars(value), names, depth + 1)
    except TypeError:
        return None


class HttpRJobClient:
    """Small JSON adapter for the deployment RJob gateway.

    The deployment gateway exposes ``/health`` and ``/v1/rjobs``. Keeping this
    boundary explicit avoids coupling API handlers to a platform-specific SDK.
    """

    def __init__(self, endpoint: str, token: str | None, timeout_seconds: float) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds

    async def preflight(self) -> None:
        await self._request("GET", "/health")

    async def create(self, spec: RJobSpec) -> str:
        payload = asdict(spec)
        payload["mounts"] = [asdict(item) for item in spec.mounts]
        response = await self._request("POST", "/v1/rjobs", payload)
        rjob_id = response.get("rjob_id")
        if not isinstance(rjob_id, str) or not rjob_id:
            raise RJobDependencyError("RJob create response does not contain rjob_id")
        return rjob_id

    async def get(self, rjob_id: str) -> RJobSnapshot:
        response = await self._request(
            "GET", f"/v1/rjobs/{urllib.parse.quote(rjob_id, safe='')}"
        )
        try:
            state = RJobState(str(response.get("state", "unknown")).lower())
        except ValueError:
            state = RJobState.UNKNOWN
        summary = response.get("workload_summary") or {}
        if not isinstance(summary, dict):
            summary = {}
        raw_address = response.get("address")
        address = raw_address if isinstance(raw_address, str) and raw_address else None
        raw_port = response.get("port")
        port = (
            raw_port
            if isinstance(raw_port, int)
            and not isinstance(raw_port, bool)
            and 1 <= raw_port <= 65535
            else None
        )
        raw_exit_code = response.get("exit_code")
        exit_code = (
            raw_exit_code
            if isinstance(raw_exit_code, int) and not isinstance(raw_exit_code, bool)
            else None
        )
        raw_failure = response.get("failure_summary")
        failure_summary = raw_failure if isinstance(raw_failure, str) else None
        return RJobSnapshot(
            rjob_id=rjob_id,
            state=state,
            ready=response.get("ready") is True,
            address=address,
            port=port,
            exit_code=exit_code,
            failure_summary=failure_summary,
            workload_summary={
                key: int(value)
                for key, value in summary.items()
                if key
                in {"total", "running", "succeeded", "failed", "collected"}
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            },
        )

    async def stop(self, rjob_id: str) -> None:
        await self._request(
            "DELETE", f"/v1/rjobs/{urllib.parse.quote(rjob_id, safe='')}"
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._sync_request, method, path, payload)

    def _sync_request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(
            f"{self._endpoint}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                content = response.read()
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise RJobDependencyError("RJob control plane request failed") from exc
        if not content:
            return {}
        try:
            result = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RJobDependencyError("RJob control plane returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RJobDependencyError("RJob control plane returned an invalid response")
        return result


class HttpGatewayHealthChecker:
    def __init__(
        self, timeout_seconds: float, expected_storage_type: str | None = None
    ) -> None:
        self._timeout = timeout_seconds
        self._expected_storage_type = expected_storage_type

    async def ready(self, gateway_url: str, health_path: str) -> bool:
        target = f"{gateway_url.rstrip('/')}/{health_path.lstrip('/')}"
        return await asyncio.to_thread(self._sync_ready, target)

    def _sync_ready(self, target: str) -> bool:
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        connection_class = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            parsed.hostname,
            parsed.port,
            timeout=self._timeout,
        )
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            if not 200 <= response.status < 300:
                return False
            if not parsed.path.rstrip("/").endswith("/readyz"):
                return True
            payload = json.loads(body.decode("utf-8"))
            return (
                isinstance(payload, dict)
                and payload.get("status") == "ready"
                and (
                    self._expected_storage_type is None
                    or payload.get("storage_type") == self._expected_storage_type
                )
            )
        except (OSError, TimeoutError, http.client.HTTPException):
            return False
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        finally:
            connection.close()
