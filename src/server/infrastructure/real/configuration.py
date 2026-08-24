from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from server.config import Settings
from server.domain.entities import Model, Range
from server.domain.errors import DomainError, ErrorCode


class TrustedConfigError(RuntimeError):
    """A trusted Phase 2 configuration file is unavailable or invalid."""


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatabaseConfig(ConfigModel):
    control_db_path: Path
    data_platform_factory: str = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict, repr=False)


class RJobConnectionConfig(ConfigModel):
    backend: Literal["brainpp", "http"] = "brainpp"
    cluster_entry: str | None = None
    endpoint: str | None = None
    token: str | None = Field(default=None, repr=False)
    namespace: str = Field(min_length=1)
    charged_group: str = Field(min_length=1)
    access_key: str | None = Field(default=None, repr=False)
    secret_key: str | None = Field(default=None, repr=False)
    verifyssl: bool = True
    retries: int = Field(default=3, ge=0)
    no_packaging: bool = True
    private_machine: str = Field(default="Group", min_length=1)
    host_network: bool | None = None
    auto_delete_duration: str = Field(default="12h", min_length=1)
    max_running_duration: str = Field(default="14h", min_length=1)

    @model_validator(mode="after")
    def required_endpoint(self) -> Self:
        if self.backend == "brainpp" and not self.cluster_entry:
            raise ValueError("brainpp RJob backend requires cluster_entry")
        if self.backend == "http" and not self.endpoint:
            raise ValueError("http RJob backend requires endpoint")
        return self


class ManagedLocationConfig(ConfigModel):
    local_path: Path
    rjob_source: str = Field(min_length=1)
    mount_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def absolute_mount(self) -> Self:
        if not Path(self.mount_path).is_absolute():
            raise ValueError("mount_path must be an absolute container path")
        return self


class StorageConfig(ConfigModel):
    environment: ManagedLocationConfig
    results: ManagedLocationConfig


class CatalogConfig(ConfigModel):
    models_path: Path
    ranges_path: Path
    environment_root: Path


class GatewayRuntimeConfig(ConfigModel):
    config_local_dir: Path
    config_source_dir: str = Field(min_length=1)
    config_mount_dir: str = Field(default="/app/runtime-config", min_length=1)
    config_filename: str = Field(default="gateway.yaml", min_length=1)
    workdir: str = Field(default="/app", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    scheme: Literal["http", "https"] = "http"
    health_path: str = Field(default="/readyz", min_length=1)
    sessions_path: str = Field(default="/v1/sessions", min_length=1)
    ready_timeout_seconds: int = Field(default=300, gt=0)
    health_timeout_seconds: float = Field(default=3.0, gt=0)
    resources: dict[str, Any] = Field(
        default_factory=lambda: {"cpu": 1, "gpu": 0, "memory_in_mb": 4096}
    )

    @model_validator(mode="after")
    def valid_paths(self) -> Self:
        for name, value in (
            ("config_mount_dir", self.config_mount_dir),
            ("workdir", self.workdir),
        ):
            if not Path(value).is_absolute():
                raise ValueError(f"{name} must be an absolute container path")
        filename = Path(self.config_filename)
        if filename.is_absolute() or ".." in filename.parts:
            raise ValueError("config_filename must stay inside config_mount_dir")
        return self


class SafactoryRuntimeConfig(ConfigModel):
    workdir: str = Field(default="/app", min_length=1)
    python_bin: str = Field(default="python", min_length=1)
    launcher_rjob_config: str = Field(default="config.yaml", min_length=1)
    storage_type: Literal["cloud", "sqlite"] = "cloud"
    pool_size: int = Field(default=10, gt=0)
    multiplier: float = Field(default=1.0, gt=0)
    max_workers: int = Field(default=10, gt=0)
    max_steps: int = Field(default=150, gt=0)
    agent_start_timeout_seconds: float = Field(default=9000.0, gt=0)
    enable_evaluation: bool = True
    timeout_seconds: int = Field(default=14 * 60 * 60, gt=0)
    resources: dict[str, Any] = Field(
        default_factory=lambda: {"cpu": 4, "gpu": 0, "memory_in_mb": 16384}
    )
    episode_rjob_defaults: dict[str, Any] = Field(default_factory=dict)
    launcher_args: tuple[str, ...] = ()

    @model_validator(mode="after")
    def absolute_workdir(self) -> Self:
        if not Path(self.workdir).is_absolute():
            raise ValueError("workdir must be an absolute container path")
        return self


class InitializationConfig(ConfigModel):
    schema_version: Literal["1.0"]
    gateway_base_image: str = Field(min_length=1, pattern=r"^\S+$")
    safactory_base_image: str = Field(min_length=1, pattern=r"^\S+$")
    image_pull_policy: Literal["Always", "IfNotPresent", "Never"] = "IfNotPresent"
    placeholder: bool = False
    database: DatabaseConfig | None = None
    rjob: RJobConnectionConfig | None = None
    storage: StorageConfig | None = None
    catalog: CatalogConfig | None = None
    gateway: GatewayRuntimeConfig | None = None
    safactory: SafactoryRuntimeConfig | None = None
    runtime_no_proxy: str | None = None

    @model_validator(mode="after")
    def complete_runtime_config(self) -> Self:
        values = {
            "database": self.database,
            "rjob": self.rjob,
            "storage": self.storage,
            "catalog": self.catalog,
            "gateway": self.gateway,
            "safactory": self.safactory,
        }
        configured = [name for name, value in values.items() if value is not None]
        if configured and len(configured) != len(values):
            missing = sorted(set(values) - set(configured))
            raise ValueError(
                "real runtime configuration is incomplete; missing: "
                + ", ".join(missing)
            )
        return self

    @property
    def runtime_configured(self) -> bool:
        return self.database is not None


class GatewayModelConfig(ConfigModel):
    route: dict[str, Any] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    llm_model: str | None = None


class ModelConfig(ConfigModel):
    model_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    available: bool = True
    gateway: GatewayModelConfig


class ModelDocument(ConfigModel):
    schema_version: Literal["1.0"]
    models: tuple[ModelConfig, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        ids = [item.model_id for item in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("model_id values must be unique")
        return self


class RangeGroupConfig(ConfigModel):
    env_name: str = Field(min_length=1)
    dataset: Path
    start_config: Path


class RangeConfig(ConfigModel):
    range_id: str = Field(min_length=1)
    available: bool = True
    availability_retryable: bool = False
    supported_models: tuple[str, ...] = Field(min_length=1)
    agent_config: Path
    groups: tuple[RangeGroupConfig, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_groups(self) -> Self:
        names = [item.env_name for item in self.groups]
        if len(names) != len(set(names)):
            raise ValueError("range group env_name values must be unique")
        return self


class RangeDocument(ConfigModel):
    schema_version: Literal["1.0"]
    ranges: tuple[RangeConfig, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        ids = [item.range_id for item in self.ranges]
        if len(ids) != len(set(ids)):
            raise ValueError("range_id values must be unique")
        return self


class RealCatalog:
    """Reloads trusted YAML for every operation so creation revalidates availability."""

    def __init__(
        self, model_path: Path, range_path: Path, environment_root: Path | None = None
    ) -> None:
        self.model_path = model_path
        self.range_path = range_path
        self.environment_root = environment_root or range_path.parent

    async def preflight(self) -> None:
        models = self._models()
        ranges = self._ranges()
        known_models = {item.model_id for item in models.models}
        unknown = {
            model_id
            for item in ranges.ranges
            for model_id in item.supported_models
            if model_id not in known_models
        }
        if unknown:
            raise TrustedConfigError(
                f"ranges reference unknown model IDs: {sorted(unknown)}"
            )

    async def list_models(self) -> tuple[Model, ...]:
        try:
            document = self._models()
        except TrustedConfigError as exc:
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        return tuple(Model(item.model_id, item.name, item.available) for item in document.models)

    async def get_model(self, model_id: str) -> Model | None:
        try:
            item = next(
                (item for item in self._models().models if item.model_id == model_id),
                None,
            )
        except TrustedConfigError as exc:
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        return None if item is None else Model(item.model_id, item.name, item.available)

    async def get_range(self, range_id: str) -> Range | None:
        try:
            item = self.resolve_range(range_id)
        except TrustedConfigError as exc:
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if item is None:
            return None
        return Range(
            range_id=item.range_id,
            available=item.available,
            availability_retryable=item.availability_retryable,
            supported_model_ids=frozenset(item.supported_models),
        )

    def resolve_model(self, model_id: str) -> ModelConfig | None:
        return next(
            (item for item in self._models().models if item.model_id == model_id), None
        )

    def resolve_range(self, range_id: str) -> RangeConfig | None:
        return next(
            (item for item in self._ranges().ranges if item.range_id == range_id), None
        )

    def model_checksum(self) -> str:
        return _checksum(self.model_path.read_bytes())

    def resolve_source(self, configured_path: Path) -> Path:
        if configured_path.is_absolute():
            return configured_path
        return (self.environment_root / configured_path).resolve()

    def _models(self) -> ModelDocument:
        return _load_yaml(self.model_path, ModelDocument)

    def _ranges(self) -> RangeDocument:
        return _load_yaml(self.range_path, RangeDocument)


def _load_yaml(path: Path, schema: type[BaseModel]):
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be an object")
        return schema.model_validate(raw)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise TrustedConfigError(f"invalid trusted configuration: {path}") from exc


def load_initialization_config(path: Path) -> InitializationConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be an object")
        expanded = _expand_environment(raw)
        _resolve_runtime_paths(expanded, path.parent)
        return InitializationConfig.model_validate(expanded)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise TrustedConfigError(f"invalid trusted configuration: {path}") from exc


def apply_initialization_config(
    settings: Settings, config: InitializationConfig
) -> Settings:
    """Overlay the complete real-runtime YAML onto process-level settings."""
    if not config.runtime_configured:
        return settings
    assert config.database is not None
    assert config.rjob is not None
    assert config.storage is not None
    assert config.catalog is not None
    assert config.gateway is not None
    assert config.safactory is not None
    return replace(
        settings,
        model_config_path=config.catalog.models_path,
        range_config_path=config.catalog.ranges_path,
        control_db_path=config.database.control_db_path,
        data_platform_factory=config.database.data_platform_factory,
        database_environment_json=canonical_json(config.database.environment),
        shared_storage_root=config.storage.environment.local_path,
        shared_storage_rjob_source=config.storage.environment.rjob_source,
        environment_mount_dir=config.storage.environment.mount_path,
        results_root=config.storage.results.local_path,
        results_rjob_source=config.storage.results.rjob_source,
        results_mount_dir=config.storage.results.mount_path,
        rjob_backend=config.rjob.backend,
        rjob_endpoint=config.rjob.endpoint,
        rjob_token=config.rjob.token,
        rjob_cluster_entry=config.rjob.cluster_entry,
        rjob_access_key=config.rjob.access_key,
        rjob_secret_key=config.rjob.secret_key,
        rjob_verifyssl=config.rjob.verifyssl,
        rjob_retries=config.rjob.retries,
        rjob_no_packaging=config.rjob.no_packaging,
        rjob_namespace=config.rjob.namespace,
        charged_group=config.rjob.charged_group,
        rjob_private_machine=config.rjob.private_machine,
        rjob_host_network=config.rjob.host_network,
        rjob_auto_delete_duration=config.rjob.auto_delete_duration,
        rjob_max_running_duration=config.rjob.max_running_duration,
        gateway_config_local_dir=config.gateway.config_local_dir,
        gateway_config_source_dir=config.gateway.config_source_dir,
        gateway_config_mount_dir=config.gateway.config_mount_dir,
        gateway_config_filename=config.gateway.config_filename,
        gateway_workdir=config.gateway.workdir,
        gateway_port=config.gateway.port,
        gateway_scheme=config.gateway.scheme,
        gateway_health_path=config.gateway.health_path,
        gateway_sessions_path=config.gateway.sessions_path,
        gateway_health_timeout_seconds=config.gateway.health_timeout_seconds,
        gateway_ready_timeout_seconds=config.gateway.ready_timeout_seconds,
        gateway_resources_json=canonical_json(config.gateway.resources),
        runtime_no_proxy=config.runtime_no_proxy or settings.runtime_no_proxy,
        safactory_workdir=config.safactory.workdir,
        safactory_python_bin=config.safactory.python_bin,
        safactory_launcher_rjob_config=config.safactory.launcher_rjob_config,
        safactory_storage_type=config.safactory.storage_type,
        safactory_pool_size=config.safactory.pool_size,
        safactory_multiplier=config.safactory.multiplier,
        safactory_max_workers=config.safactory.max_workers,
        safactory_max_steps=config.safactory.max_steps,
        safactory_agent_start_timeout_seconds=(
            config.safactory.agent_start_timeout_seconds
        ),
        safactory_enable_evaluation=config.safactory.enable_evaluation,
        safactory_timeout_seconds=config.safactory.timeout_seconds,
        safactory_resources_json=canonical_json(config.safactory.resources),
        episode_rjob_defaults_json=canonical_json(
            config.safactory.episode_rjob_defaults
        ),
        safactory_launcher_args_json=canonical_json(config.safactory.launcher_args),
    )


_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_environment(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if not isinstance(value, str):
        return value

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        result = os.getenv(name)
        if result is None or not result.strip():
            raise ValueError(f"required environment variable is unset: {name}")
        return result

    return _ENV_REFERENCE.sub(substitute, value)


def _resolve_runtime_paths(document: dict[str, Any], base: Path) -> None:
    paths = (
        ("database", "control_db_path"),
        ("catalog", "models_path"),
        ("catalog", "ranges_path"),
        ("catalog", "environment_root"),
        ("storage", "environment", "local_path"),
        ("storage", "results", "local_path"),
        ("gateway", "config_local_dir"),
    )
    for keys in paths:
        current: Any = document
        for key in keys[:-1]:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if not isinstance(current, dict) or keys[-1] not in current:
            continue
        raw = Path(str(current[keys[-1]])).expanduser()
        current[keys[-1]] = str(raw if raw.is_absolute() else (base / raw).resolve())


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
