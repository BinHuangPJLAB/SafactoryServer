from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_AUTH_CONFIG_PATH = Path(__file__).parent / "auth" / "trusted_api_keys.yaml"
DEFAULT_INITIALIZATION_CONFIG_PATH = (
    Path(__file__).parent / "configs" / "initialization.yaml"
)
DEFAULT_RANGE_CONFIG_PATH = Path("/etc/safactory/ranges.yaml")
DEFAULT_CONTROL_DB_PATH = Path("/var/lib/safactory/control.db")
DEFAULT_SHARED_STORAGE_ROOT = Path("/mnt/safactory")
DEFAULT_RESULTS_ROOT = Path("/mnt/safactory/results")
DEFAULT_RUNTIME_NO_PROXY = (
    "localhost,127.0.0.1,::1,10.0.0.0/8,100.96.0.0/12,"
    "172.16.0.0/12,192.168.0.0/16,.svc,.svc.cluster.local,"
    ".cluster.local,.pjlab.local,.pjlab.org.cn,.pjh-service.org.cn,"
    "kubernetes.default.svc"
)


@dataclass(frozen=True, slots=True)
class Settings:
    auth_config_path: Path = DEFAULT_AUTH_CONFIG_PATH
    host: str = "0.0.0.0"
    port: int = 8000
    retry_after_seconds: int = 1
    log_level: str = "INFO"
    initialization_config_path: Path = DEFAULT_INITIALIZATION_CONFIG_PATH
    range_config_path: Path = DEFAULT_RANGE_CONFIG_PATH
    control_db_path: Path = DEFAULT_CONTROL_DB_PATH
    shared_storage_root: Path = DEFAULT_SHARED_STORAGE_ROOT
    shared_storage_rjob_source: str = "/mnt/safactory"
    results_root: Path = DEFAULT_RESULTS_ROOT
    results_rjob_source: str = "/mnt/safactory/results"
    environment_mount_dir: str = "/app/env"
    results_mount_dir: str = "/app/results"
    rjob_backend: str = "http"
    rjob_endpoint: str | None = None
    rjob_token: str | None = field(default=None, repr=False)
    rjob_cluster_entry: str | None = None
    rjob_access_key: str | None = field(default=None, repr=False)
    rjob_secret_key: str | None = field(default=None, repr=False)
    rjob_verifyssl: bool = True
    rjob_retries: int = 3
    rjob_no_packaging: bool = True
    rjob_restart_policy: str = "Never"
    rjob_namespace: str = "default"
    charged_group: str = "default"
    rjob_private_machine: str = "Group"
    rjob_host_network: bool | None = None
    rjob_auto_delete_duration: str = "12h"
    rjob_max_running_duration: str = "14h"
    rjob_credential_ref: str = ""
    data_platform_config_ref: str = ""
    database_environment_json: str = field(default="{}", repr=False)
    gateway_config_json: str = field(default="{}", repr=False)
    gateway_resources_json: str = "{}"
    gateway_requests_json: str = "{}"
    safactory_resources_json: str = "{}"
    safactory_requests_json: str = "{}"
    episode_rjob_defaults_json: str = "{}"
    gateway_config_mount_dir: str = "/app/runtime-config"
    gateway_config_filename: str = "gateway.yaml"
    gateway_name_prefix: str = "safactory-gateway"
    gateway_workdir: str = "/app"
    gateway_port: int = 8000
    gateway_scheme: str = "http"
    gateway_health_path: str = "/readyz"
    gateway_sessions_path: str = "/v1/sessions"
    runtime_no_proxy: str = DEFAULT_RUNTIME_NO_PROXY
    safactory_name_prefix: str = "safactory"
    safactory_workdir: str = "/app"
    safactory_python_bin: str = "python"
    safactory_launcher_rjob_config: str = "config.yaml"
    safactory_storage_type: str = "cloud"
    safactory_pool_size: int = 10
    safactory_multiplier: float = 1.0
    safactory_max_workers: int = 10
    safactory_max_steps: int = 150
    safactory_agent_start_timeout_seconds: float = 9000.0
    safactory_enable_evaluation: bool = True
    safactory_launcher_args_json: str = "[]"
    gateway_health_timeout_seconds: float = 3.0
    gateway_ready_timeout_seconds: int = 300
    safactory_timeout_seconds: int = 7200
    orchestrator_poll_seconds: float = 2.0
    rjob_request_timeout_seconds: float = 10.0
    data_platform_factory: str = "wt_data_platform_sdk:create_client"

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        if self.retry_after_seconds < 1:
            raise ValueError("retry_after_seconds must be positive.")
        if not 1 <= self.gateway_port <= 65535:
            raise ValueError("gateway_port must be between 1 and 65535.")
        if self.gateway_scheme not in {"http", "https"}:
            raise ValueError("gateway_scheme must be either 'http' or 'https'.")
        if self.rjob_backend not in {"http", "brainpp"}:
            raise ValueError("rjob_backend must be either 'http' or 'brainpp'.")
        if self.rjob_retries < 0:
            raise ValueError("rjob_retries must not be negative.")
        if not self.rjob_restart_policy.strip():
            raise ValueError("rjob_restart_policy must not be empty.")
        for name, value in (
            ("environment_mount_dir", self.environment_mount_dir),
            ("results_mount_dir", self.results_mount_dir),
            ("gateway_config_mount_dir", self.gateway_config_mount_dir),
            ("gateway_workdir", self.gateway_workdir),
            ("safactory_workdir", self.safactory_workdir),
        ):
            if not Path(value).is_absolute():
                raise ValueError(f"{name} must be an absolute container path.")
        gateway_filename = Path(self.gateway_config_filename)
        if gateway_filename.is_absolute() or ".." in gateway_filename.parts:
            raise ValueError(
                "gateway_config_filename must stay inside gateway_config_mount_dir."
            )
        if not self.gateway_name_prefix.strip() or not self.safactory_name_prefix.strip():
            raise ValueError("RJob name prefixes must not be empty.")
        if self.safactory_storage_type not in {"cloud", "sqlite"}:
            raise ValueError("safactory_storage_type must be 'cloud' or 'sqlite'.")
        if (
            self.safactory_pool_size <= 0
            or self.safactory_max_workers <= 0
            or self.safactory_max_steps <= 0
            or self.safactory_multiplier <= 0
            or self.safactory_agent_start_timeout_seconds <= 0
        ):
            raise ValueError("Safactory launcher numeric settings must be positive.")
        if self.orchestrator_poll_seconds <= 0:
            raise ValueError("orchestrator_poll_seconds must be positive.")
        if self.gateway_health_timeout_seconds <= 0:
            raise ValueError("gateway_health_timeout_seconds must be positive.")
        if self.gateway_ready_timeout_seconds <= 0 or self.safactory_timeout_seconds <= 0:
            raise ValueError("RJob timeouts must be positive.")

    @classmethod
    def from_env(cls) -> Settings:
        auth_config_path = Path(
            os.getenv("SAFACTORY_AUTH_CONFIG_PATH", str(DEFAULT_AUTH_CONFIG_PATH))
        ).expanduser()
        return cls(
            auth_config_path=auth_config_path,
            host=os.getenv("SAFACTORY_HOST", "0.0.0.0"),
            port=int(os.getenv("SAFACTORY_PORT", "8000")),
            retry_after_seconds=int(os.getenv("SAFACTORY_RETRY_AFTER_SECONDS", "1")),
            log_level=os.getenv("SAFACTORY_LOG_LEVEL", "INFO").upper(),
            initialization_config_path=Path(
                os.getenv(
                    "SAFACTORY_INITIALIZATION_CONFIG_PATH",
                    str(DEFAULT_INITIALIZATION_CONFIG_PATH),
                )
            ).expanduser(),
            range_config_path=Path(
                os.getenv("SAFACTORY_RANGE_CONFIG_PATH", str(DEFAULT_RANGE_CONFIG_PATH))
            ).expanduser(),
            control_db_path=Path(
                os.getenv("SAFACTORY_CONTROL_DB_PATH", str(DEFAULT_CONTROL_DB_PATH))
            ).expanduser(),
            shared_storage_root=Path(
                os.getenv(
                    "SAFACTORY_SHARED_STORAGE_ROOT", str(DEFAULT_SHARED_STORAGE_ROOT)
                )
            ).expanduser(),
            shared_storage_rjob_source=os.getenv(
                "SAFACTORY_SHARED_STORAGE_RJOB_SOURCE", "/mnt/safactory"
            ),
            results_root=Path(
                os.getenv("SAFACTORY_RESULTS_ROOT", str(DEFAULT_RESULTS_ROOT))
            ).expanduser(),
            results_rjob_source=os.getenv(
                "SAFACTORY_RESULTS_RJOB_SOURCE", "/mnt/safactory/results"
            ),
            environment_mount_dir=os.getenv(
                "SAFACTORY_ENVIRONMENT_MOUNT_DIR", "/app/env"
            ),
            results_mount_dir=os.getenv("SAFACTORY_RESULTS_MOUNT_DIR", "/app/results"),
            rjob_backend=os.getenv("SAFACTORY_RJOB_BACKEND", "http").lower(),
            rjob_endpoint=_optional_env("SAFACTORY_RJOB_ENDPOINT"),
            rjob_token=_optional_env("SAFACTORY_RJOB_TOKEN"),
            rjob_cluster_entry=_optional_env("RJOB_CLUSTER_ENTRY"),
            rjob_access_key=_optional_env("RJOB_ACCESS_KEY"),
            rjob_secret_key=_optional_env("RJOB_SECRET_KEY"),
            rjob_verifyssl=_boolean_env("SAFACTORY_RJOB_VERIFYSSL", True),
            rjob_retries=int(os.getenv("SAFACTORY_RJOB_RETRIES", "3")),
            rjob_no_packaging=_boolean_env("SAFACTORY_RJOB_NO_PACKAGING", True),
            rjob_restart_policy=os.getenv(
                "SAFACTORY_RJOB_RESTART_POLICY", "Never"
            ),
            rjob_namespace=os.getenv("SAFACTORY_RJOB_NAMESPACE", "default"),
            charged_group=os.getenv("SAFACTORY_CHARGED_GROUP", "default"),
            rjob_private_machine=os.getenv("SAFACTORY_RJOB_PRIVATE_MACHINE", "Group"),
            rjob_host_network=_optional_boolean_env("SAFACTORY_RJOB_HOST_NETWORK"),
            rjob_auto_delete_duration=os.getenv(
                "SAFACTORY_RJOB_AUTO_DELETE_DURATION", "12h"
            ),
            rjob_max_running_duration=os.getenv(
                "SAFACTORY_RJOB_MAX_RUNNING_DURATION", "14h"
            ),
            rjob_credential_ref=os.getenv("SAFACTORY_RJOB_CREDENTIAL_REF", ""),
            data_platform_config_ref=os.getenv(
                "SAFACTORY_DATA_PLATFORM_CONFIG_REF", ""
            ),
            database_environment_json=os.getenv(
                "SAFACTORY_DATABASE_ENVIRONMENT_JSON", "{}"
            ),
            gateway_config_json=os.getenv("SAFACTORY_GATEWAY_CONFIG_JSON", "{}"),
            gateway_resources_json=os.getenv("SAFACTORY_GATEWAY_RESOURCES_JSON", "{}"),
            gateway_requests_json=os.getenv("SAFACTORY_GATEWAY_REQUESTS_JSON", "{}"),
            safactory_resources_json=os.getenv(
                "SAFACTORY_CONTROLLER_RESOURCES_JSON", "{}"
            ),
            safactory_requests_json=os.getenv(
                "SAFACTORY_CONTROLLER_REQUESTS_JSON", "{}"
            ),
            episode_rjob_defaults_json=os.getenv(
                "SAFACTORY_EPISODE_RJOB_DEFAULTS_JSON", "{}"
            ),
            gateway_port=int(os.getenv("SAFACTORY_GATEWAY_PORT", "8000")),
            gateway_scheme=os.getenv("SAFACTORY_GATEWAY_SCHEME", "http").lower(),
            gateway_health_path=os.getenv("SAFACTORY_GATEWAY_HEALTH_PATH", "/readyz"),
            gateway_sessions_path=os.getenv(
                "SAFACTORY_GATEWAY_SESSIONS_PATH", "/v1/sessions"
            ),
            gateway_config_mount_dir=os.getenv(
                "SAFACTORY_GATEWAY_CONFIG_MOUNT_DIR", "/app/runtime-config"
            ),
            gateway_config_filename=os.getenv(
                "SAFACTORY_GATEWAY_CONFIG_FILENAME", "gateway.yaml"
            ),
            gateway_name_prefix=os.getenv(
                "SAFACTORY_GATEWAY_NAME_PREFIX", "safactory-gateway"
            ),
            gateway_workdir=os.getenv("SAFACTORY_GATEWAY_WORKDIR", "/app"),
            runtime_no_proxy=os.getenv(
                "SAFACTORY_RUNTIME_NO_PROXY", DEFAULT_RUNTIME_NO_PROXY
            ),
            safactory_name_prefix=os.getenv(
                "SAFACTORY_CONTROLLER_NAME_PREFIX", "safactory"
            ),
            safactory_workdir=os.getenv("SAFACTORY_CONTROLLER_WORKDIR", "/app"),
            safactory_python_bin=os.getenv("SAFACTORY_PYTHON_BIN", "python"),
            safactory_launcher_rjob_config=os.getenv(
                "SAFACTORY_LAUNCHER_RJOB_CONFIG", "config.yaml"
            ),
            safactory_storage_type=os.getenv(
                "SAFACTORY_STORAGE_TYPE", "cloud"
            ).lower(),
            safactory_pool_size=int(os.getenv("SAFACTORY_POOL_SIZE", "10")),
            safactory_multiplier=float(os.getenv("SAFACTORY_MULTIPLIER", "1.0")),
            safactory_max_workers=int(os.getenv("SAFACTORY_MAX_WORKERS", "10")),
            safactory_max_steps=int(os.getenv("SAFACTORY_MAX_STEPS", "150")),
            safactory_agent_start_timeout_seconds=float(
                os.getenv("SAFACTORY_AGENT_START_TIMEOUT_SECONDS", "9000")
            ),
            safactory_enable_evaluation=_boolean_env(
                "SAFACTORY_ENABLE_EVALUATION", True
            ),
            safactory_launcher_args_json=os.getenv(
                "SAFACTORY_LAUNCHER_ARGS_JSON", "[]"
            ),
            gateway_health_timeout_seconds=float(
                os.getenv("SAFACTORY_GATEWAY_HEALTH_TIMEOUT_SECONDS", "3")
            ),
            gateway_ready_timeout_seconds=int(
                os.getenv("SAFACTORY_GATEWAY_READY_TIMEOUT_SECONDS", "300")
            ),
            safactory_timeout_seconds=int(
                os.getenv("SAFACTORY_CONTROLLER_TIMEOUT_SECONDS", "7200")
            ),
            orchestrator_poll_seconds=float(
                os.getenv("SAFACTORY_ORCHESTRATOR_POLL_SECONDS", "2")
            ),
            rjob_request_timeout_seconds=float(
                os.getenv("SAFACTORY_RJOB_REQUEST_TIMEOUT_SECONDS", "10")
            ),
            data_platform_factory=os.getenv(
                "SAFACTORY_DATA_PLATFORM_FACTORY",
                "wt_data_platform_sdk:create_client",
            ),
        )


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _boolean_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")


def _optional_boolean_env(name: str) -> bool | None:
    return None if os.getenv(name) is None else _boolean_env(name, False)
