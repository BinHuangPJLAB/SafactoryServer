from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from server.infrastructure.real.configuration import RangeConfig, RealCatalog

INPUT_TARGET = "/mnt/safactory-job"
RESULT_TARGET = "/app/results"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class FileBindingError(RuntimeError):
    """The immutable range input set cannot be resolved or published."""


@dataclass(frozen=True, slots=True)
class EnvironmentBinding:
    env_name: str
    env_image: str
    env_num: int
    dataset_records: int
    dataset_checksum: str
    start_config_checksum: str


@dataclass(frozen=True, slots=True)
class JobFileBinding:
    job_id: str
    range_id: str
    input_local_path: str
    input_source: str
    input_target: str
    result_local_path: str
    result_source: str
    result_target: str
    agent_config_path: str
    agent_start_config_path: str
    agent_config_checksum: str
    groups: tuple[EnvironmentBinding, ...]
    total_episodes: int
    gateway_config_source: str = ""
    gateway_config_path: str = "/app/runtime-config/gateway.yaml"
    gateway_config_checksum: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> JobFileBinding:
        raw = json.loads(value)
        raw["groups"] = tuple(EnvironmentBinding(**item) for item in raw["groups"])
        raw.setdefault("input_local_path", raw["input_source"])
        raw.setdefault("result_local_path", raw["result_source"])
        raw.setdefault(
            "gateway_config_source", _join_source(raw["input_source"], "gateway")
        )
        raw.setdefault("gateway_config_path", "/app/runtime-config/gateway.yaml")
        raw.setdefault("gateway_config_checksum", "")
        if "agent_start_config_path" not in raw:
            if len(raw["groups"]) != 1:
                raise FileBindingError(
                    "legacy multi-environment binding has no launcher start config"
                )
            env_name = raw["groups"][0].env_name
            raw["agent_start_config_path"] = (
                f"{raw['input_target']}/groups/{env_name}/start.rjob.yaml"
            )
        return cls(**raw)


class SharedFileManager:
    """Validates and atomically publishes a Job-specific immutable input binding."""

    def __init__(
        self,
        root: Path,
        catalog: RealCatalog,
        *,
        rjob_root: str | None = None,
        results_root: Path | None = None,
        results_rjob_root: str | None = None,
        input_target: str = INPUT_TARGET,
        result_target: str = RESULT_TARGET,
        gateway_config: dict[str, Any] | None = None,
        gateway_config_mount_dir: str = "/app/runtime-config",
        gateway_config_filename: str = "gateway.yaml",
    ) -> None:
        self._root = root
        self._rjob_root = str(rjob_root or root)
        self._results_root = results_root or root / "results"
        self._results_rjob_root = str(results_rjob_root or self._results_root)
        self._input_target = input_target.rstrip("/") or "/"
        self._result_target = result_target.rstrip("/") or "/"
        if not Path(gateway_config_mount_dir).is_absolute():
            raise ValueError("gateway_config_mount_dir must be an absolute path")
        filename = Path(gateway_config_filename)
        if filename.is_absolute() or ".." in filename.parts:
            raise ValueError("gateway_config_filename must stay inside its mount")
        self._gateway_config = dict(gateway_config or {})
        self._gateway_config_mount_dir = gateway_config_mount_dir.rstrip("/") or "/"
        self._gateway_config_filename = gateway_config_filename
        self._catalog = catalog

    def preflight(self) -> None:
        try:
            for root in (self._root, self._results_root):
                root.mkdir(parents=True, exist_ok=True)
                probe = root / ".safactory-write-probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
        except OSError as exc:
            raise FileBindingError("shared storage is not writable") from exc

    def bind(self, job_id: str, range_id: str) -> JobFileBinding:
        if not SAFE_NAME.fullmatch(job_id):
            raise FileBindingError("job_id is not safe for storage")
        range_config = self._catalog.resolve_range(range_id)
        if range_config is None or not range_config.available:
            raise FileBindingError("range is unavailable")

        final_input = self._root / job_id
        binding_path = final_input / ".binding.json"
        if binding_path.exists():
            binding = JobFileBinding.from_json(binding_path.read_text(encoding="utf-8"))
            if binding.job_id != job_id or binding.range_id != range_id:
                raise FileBindingError("existing immutable binding does not match job")
            return binding
        if final_input.exists():
            raise FileBindingError("partial input binding already exists")

        final_input.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".input-", dir=final_input.parent))
        try:
            binding = self._render_binding(staging, final_input, job_id, range_config)
            (staging / ".binding.json").write_text(binding.to_json(), encoding="utf-8")
            os.replace(staging, final_input)
        except FileBindingError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise FileBindingError("failed to publish immutable job files") from exc
        return binding

    def _render_binding(
        self,
        staging: Path,
        final_input: Path,
        job_id: str,
        range_config: RangeConfig,
    ) -> JobFileBinding:
        agent_source = self._catalog.resolve_source(range_config.agent_config)
        agent_document = _read_yaml(agent_source)
        environments = agent_document.get("environments")
        if not isinstance(environments, list) or not environments:
            raise FileBindingError("agent config must contain non-empty environments")

        environment_by_name: dict[str, dict[str, Any]] = {}
        for environment in environments:
            if not isinstance(environment, dict):
                raise FileBindingError("each environment must be an object")
            name = environment.get("env_name")
            if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
                raise FileBindingError("env_name is missing or unsafe")
            if name in environment_by_name:
                raise FileBindingError("env_name values must be unique")
            environment_by_name[name] = environment

        declared_names = {item.env_name for item in range_config.groups}
        if set(environment_by_name) != declared_names:
            raise FileBindingError("range groups do not match agent environments")

        if len(range_config.groups) != 1:
            raise FileBindingError(
                "the Safactory launcher accepts one agent start config per Job; "
                "configure exactly one environment group"
            )

        result_local_path = self._results_root / job_id
        result_local_path.mkdir(parents=True, exist_ok=True)
        result_source = _join_source(self._results_rjob_root, job_id)
        group_bindings: list[EnvironmentBinding] = []
        total_episodes = 0
        for group in range_config.groups:
            environment = environment_by_name[group.env_name]
            env_image = environment.get("env_image")
            env_num = environment.get("env_num")
            env_params = environment.get("env_params")
            if not isinstance(env_image, str) or not env_image.strip():
                raise FileBindingError(f"env_image is invalid for {group.env_name}")
            if not isinstance(env_num, int) or isinstance(env_num, bool) or env_num < 1:
                raise FileBindingError(f"env_num is invalid for {group.env_name}")
            if not isinstance(env_params, dict):
                raise FileBindingError(f"env_params is invalid for {group.env_name}")

            dataset_source = self._catalog.resolve_source(group.dataset)
            dataset_content, record_count = _read_dataset(dataset_source)
            start_source = self._catalog.resolve_source(group.start_config)
            start_document = _read_yaml(start_source)
            _validate_start_config(
                start_document, group.env_name, result_target=self._result_target
            )
            _validate_runner_source(start_document, start_source.parent)

            group_dir = staging / "groups" / group.env_name
            _copy_environment_files(start_source.parent, group_dir)
            dataset_target = group_dir / "dataset.jsonl"
            dataset_target.write_bytes(dataset_content)

            rendered_start = dict(start_document)
            rendered_rjob = dict(rendered_start["rjob"])
            rendered_rjob["mount_config"] = [
                mount
                for mount in rendered_rjob["mount_config"]
                if _mount_target(mount) != self._result_target
            ]
            rendered_rjob["mount_config"].append(
                f"{result_source}:{self._result_target}"
            )
            rendered_start["rjob"] = rendered_rjob
            start_content = _dump_yaml(rendered_start)
            (group_dir / "start.rjob.yaml").write_bytes(start_content)

            environment["dataset"] = (
                f"{self._input_target}/groups/{group.env_name}/dataset.jsonl"
            )
            rendered_params = dict(env_params)
            rendered_params["safactory_results_root"] = self._result_target
            environment["env_params"] = rendered_params
            total_episodes += record_count * env_num
            group_bindings.append(
                EnvironmentBinding(
                    env_name=group.env_name,
                    env_image=env_image,
                    env_num=env_num,
                    dataset_records=record_count,
                    dataset_checksum=_sha256(dataset_content),
                    start_config_checksum=_sha256(start_content),
                )
            )

        agent_content = _dump_yaml(agent_document)
        (staging / "config.yaml").write_bytes(agent_content)
        gateway_content = _dump_yaml(self._gateway_config)
        gateway_directory = staging / "gateway"
        gateway_directory.mkdir()
        (gateway_directory / self._gateway_config_filename).write_bytes(
            gateway_content
        )
        input_source = _join_source(self._rjob_root, job_id)
        only_group = range_config.groups[0]
        return JobFileBinding(
            job_id=job_id,
            range_id=range_config.range_id,
            input_local_path=str(final_input),
            input_source=input_source,
            input_target=self._input_target,
            result_local_path=str(result_local_path),
            result_source=result_source,
            result_target=self._result_target,
            agent_config_path=f"{self._input_target}/config.yaml",
            agent_start_config_path=(
                f"{self._input_target}/groups/{only_group.env_name}/start.rjob.yaml"
            ),
            agent_config_checksum=_sha256(agent_content),
            groups=tuple(group_bindings),
            total_episodes=total_episodes,
            gateway_config_source=_join_source(input_source, "gateway"),
            gateway_config_path=str(
                Path(self._gateway_config_mount_dir) / self._gateway_config_filename
            ),
            gateway_config_checksum=_sha256(gateway_content),
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise FileBindingError("a trusted YAML input is unavailable or invalid") from exc
    if not isinstance(document, dict):
        raise FileBindingError("trusted YAML root must be an object")
    return document


def _read_dataset(path: Path) -> tuple[bytes, int]:
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("dataset must contain at least one record")
        for line in lines:
            if not isinstance(json.loads(line), dict):
                raise ValueError("dataset records must be objects")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise FileBindingError("dataset.jsonl is unavailable or invalid") from exc
    return content, len(lines)


def _validate_start_config(
    document: dict[str, Any], env_name: str, *, result_target: str = RESULT_TARGET
) -> None:
    if document.get("agent_name") != env_name:
        raise FileBindingError("agent_name does not match env_name")
    container = document.get("container")
    rjob = document.get("rjob")
    if not isinstance(container, dict) or not isinstance(
        container.get("runner_entrypoint"), dict
    ):
        raise FileBindingError("runner_entrypoint is missing")
    if not isinstance(rjob, dict):
        raise FileBindingError("rjob configuration is missing")
    mounts = rjob.get("mount_config")
    if not isinstance(mounts, list) or not mounts:
        raise FileBindingError("result mount_config is missing")
    if not any(_mount_target(item) == result_target for item in mounts):
        raise FileBindingError(f"result mount target must be {result_target}")


def _validate_runner_source(document: dict[str, Any], source_dir: Path) -> None:
    source = document["container"]["runner_entrypoint"].get("source")
    if not isinstance(source, str) or not source.strip():
        raise FileBindingError("runner_entrypoint.source is missing")
    configured = Path(source)
    if not configured.is_absolute() and not (source_dir / configured).is_file():
        raise FileBindingError(f"relative runner source is unavailable: {source}")


def _copy_environment_files(source: Path, target: Path) -> None:
    """Copy one managed environment while excluding runtime output and caches."""
    ignored = {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "datasets",
        "runtime",
        "results",
    }
    target.mkdir(parents=True)
    resolved_target = target.resolve()
    for item in source.iterdir():
        if item.name in ignored:
            continue
        resolved_item = item.resolve()
        if resolved_target == resolved_item or resolved_target.is_relative_to(resolved_item):
            continue
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, ignore=shutil.ignore_patterns(*ignored))
        elif item.is_file():
            shutil.copy2(item, destination)


def _join_source(root: str, child: str) -> str:
    return f"{root.rstrip('/')}/{child.lstrip('/')}"


def _mount_target(value: Any) -> str | None:
    if isinstance(value, str) and ":" in value:
        return value.rsplit(":", maxsplit=1)[1]
    if isinstance(value, dict):
        target = value.get("target")
        return target if isinstance(target, str) else None
    return None


def _dump_yaml(value: dict[str, Any]) -> bytes:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
