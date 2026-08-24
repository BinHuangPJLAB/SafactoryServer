#!/usr/bin/env python3
"""Run one configured CyberGym agent controller through a common interface."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

try:
    from .openhands_prepare import stage_adapter
except ImportError:
    from openhands_prepare import stage_adapter


GATEWAY_ENV_KEYS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_BASE_URL",
    "LLM_BASE_URL",
)

API_KEY_ENV_KEYS = (
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
)


def load_controller(path: Path, agent_type: str) -> ModuleType:
    module_name = f"safactory_cybergym_{agent_type}_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load CyberGym {agent_type} controller: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def option(settings: dict[str, Any], name: str, default: Any = None) -> Any:
    options = settings.get("agent_options")
    if isinstance(options, dict) and name in options:
        return options[name]
    return default


def bool_option(settings: dict[str, Any], name: str, default: bool) -> bool:
    value = option(settings, name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def int_option(settings: dict[str, Any], name: str, default: int) -> int:
    value = option(settings, name, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"CyberGym agent option {name!r} must be an integer") from exc


def float_option(settings: dict[str, Any], name: str, default: float) -> float:
    value = option(settings, name, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"CyberGym agent option {name!r} must be a number") from exc


def optional_bool(value: Any) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Expected a boolean value, got {value!r}")


def path_or_none(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text) if text else None


def configure_gateway(settings: dict[str, Any]) -> None:
    gateway_url = str(settings["gateway_url"])
    gateway_key = str(settings["gateway_api_key"])
    for key in GATEWAY_ENV_KEYS:
        os.environ[key] = gateway_url
    for key in API_KEY_ENV_KEYS:
        os.environ[key] = gateway_key


def legacy_openai_model(settings: dict[str, Any]) -> str:
    model = str(settings["model_ref"])
    if model.startswith("openai/"):
        return model.split("/", 1)[1]
    return model


def patch_docker_environment(module: ModuleType, values: dict[str, str]) -> None:
    original_from_env: Callable[..., Any] = module.docker.from_env

    class ContainersProxy:
        def __init__(self, collection: Any):
            self._collection = collection

        def run(self, *args: Any, **kwargs: Any) -> Any:
            environment = dict(kwargs.get("environment") or {})
            environment.update(values)
            kwargs["environment"] = environment
            return self._collection.run(*args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._collection, name)

    class ClientProxy:
        def __init__(self, client: Any):
            self._client = client
            self.containers = ContainersProxy(client.containers)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._client, name)

    def from_env(*args: Any, **kwargs: Any) -> ClientProxy:
        return ClientProxy(original_from_env(*args, **kwargs))

    module.docker.from_env = from_env


def task_args(module: ModuleType, settings: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {
        "task_id": str(settings["task_id"]),
        "data_dir": Path(str(settings["data_dir"])),
        "server": str(settings["agent_server_url"]),
        "difficulty": module.TaskDifficulty(str(settings["difficulty"])),
    }
    if settings["agent_type"] == "opencode":
        mask_map_path = path_or_none(option(settings, "mask_map_path"))
        kwargs["mask_map_path"] = mask_map_path
    return module.TaskArgs(**kwargs)


def run_openhands(
    settings: dict[str, Any],
    *,
    runner_tmp: Path,
    runtime_host: str,
    timeout: int,
) -> str | None:
    staged_runner, _config_path = stage_adapter(
        source_runner=Path(str(settings["agent_runner"])),
        destination=runner_tmp / "adapter",
        runtime_image=str(settings["agent_image"]),
        runtime_host=runtime_host,
    )
    module = load_controller(staged_runner, "openhands")
    llm_args = module.LLMArgs(
        model=str(settings["model_ref"]),
        api_key=str(settings["gateway_api_key"]),
        base_url=str(settings["gateway_url"]),
        native_tool_calling=optional_bool(settings.get("native_tool_calling")),
        top_p=float(settings["top_p"]),
        temperature=float(settings["temperature"]),
        max_output_tokens=int(settings["max_output_tokens"]),
        seed=option(settings, "seed"),
    )
    args = module.OpenhandsArgs(
        log_dir=Path(str(settings["logs_dir"])),
        tmp_dir=Path(str(settings["tmp_dir"])),
        llm=llm_args,
        max_iter=int(settings["max_iter"]),
        repo=Path(str(settings["agent_repo"])),
        silent=bool(settings["silent"]),
        remove_tmp=bool_option(settings, "remove_tmp", True),
        timeout=timeout,
        debug=bool_option(settings, "debug", False),
    )
    return module.run_with_configs(args, task_args(module, settings))


def run_opencode(settings: dict[str, Any], *, timeout: int) -> str | None:
    module = load_controller(Path(str(settings["agent_runner"])), "opencode")
    args = module.OpenCodeArgs(
        model=str(settings["model_ref"]),
        log_dir=Path(str(settings["logs_dir"])),
        tmp_dir=Path(str(settings["tmp_dir"])),
        max_iter=int(settings["max_iter"]),
        remove_tmp=bool_option(settings, "remove_tmp", True),
        timeout=timeout,
        container_name=option(settings, "container_name"),
        image_name=str(settings["agent_image"]),
        base_url=str(settings["gateway_url"]),
    )
    return module.run_with_configs(args, task_args(module, settings))


def run_codex(settings: dict[str, Any], *, timeout: int) -> str | None:
    module = load_controller(Path(str(settings["agent_runner"])), "codex")
    patch_docker_environment(
        module,
        {name: str(settings["gateway_url"]) for name in GATEWAY_ENV_KEYS},
    )
    original: Callable[..., Any] = module.run_codex

    def run_with_gateway(*args: Any, **kwargs: Any) -> Any:
        kwargs["llm_api_key"] = str(settings["gateway_api_key"])
        kwargs["llm_base_url"] = str(settings["gateway_url"])
        return original(*args, **kwargs)

    module.run_codex = run_with_gateway
    model = str(settings["model_ref"])
    if not bool_option(settings, "preserve_model_provider", False):
        model = legacy_openai_model(settings)
    args = module.CodexArgs(
        model=model,
        log_dir=Path(str(settings["logs_dir"])),
        tmp_dir=Path(str(settings["tmp_dir"])),
        max_iter=int(settings["max_iter"]),
        remove_tmp=bool_option(settings, "remove_tmp", True),
        timeout=timeout,
        container_name=option(settings, "container_name"),
        image_name=str(settings["agent_image"]),
    )
    return module.run_with_configs(args, task_args(module, settings))


def add_gateway_envs(module: ModuleType, *extra_names: str) -> None:
    names = list(getattr(module, "ENVS", []))
    for name in (*API_KEY_ENV_KEYS, *GATEWAY_ENV_KEYS, *extra_names):
        if name not in names:
            names.append(name)
    module.ENVS = names


def run_cybench(settings: dict[str, Any], *, timeout: int) -> str | None:
    module = load_controller(Path(str(settings["agent_runner"])), "cybench")
    add_gateway_envs(module)
    args = module.CybenchArgs(
        log_dir=Path(str(settings["logs_dir"])),
        tmp_dir=Path(str(settings["tmp_dir"])),
        model=legacy_openai_model(settings),
        container_name=option(settings, "container_name"),
        max_iter=int(settings["max_iter"]),
        repo=Path(str(settings["agent_repo"])),
        image=str(settings["agent_image"]),
        max_input_tokens=int_option(settings, "max_input_tokens", 6000),
        max_output_tokens=int_option(settings, "max_output_tokens", 2000),
        remove_tmp=bool_option(settings, "remove_tmp", True),
        timeout=timeout,
    )
    return module.run_with_configs(args, task_args(module, settings))


def run_enigma(settings: dict[str, Any], *, timeout: int) -> str | None:
    module = load_controller(Path(str(settings["agent_runner"])), "enigma")
    add_gateway_envs(module, "PATH", "HOME", "PYTHONPATH")
    module.ENIGMA_IMAGE = str(settings["agent_image"])
    args = module.EnigmaArgs(
        model=legacy_openai_model(settings),
        log_dir=Path(str(settings["logs_dir"])),
        tmp_dir=Path(str(settings["tmp_dir"])),
        repo=Path(str(settings["agent_repo"])),
        enigma_python=Path(str(settings["agent_python"])),
        cost_limit=float_option(settings, "cost_limit", 2.0),
        silent=bool(settings["silent"]),
        remove_tmp=bool_option(settings, "remove_tmp", True),
        timeout=timeout,
        container_name=option(settings, "container_name"),
    )
    return module.run_with_configs(args, task_args(module, settings))


def dispatch(
    settings: dict[str, Any],
    *,
    runner_tmp: Path,
    runtime_host: str,
    timeout: int,
) -> str:
    configure_gateway(settings)
    agent_type = str(settings["agent_type"])
    runners: dict[str, Callable[..., str | None]] = {
        "codex": run_codex,
        "cybench": run_cybench,
        "enigma": run_enigma,
        "opencode": run_opencode,
    }
    if agent_type == "openhands":
        agent_id = run_openhands(
            settings,
            runner_tmp=runner_tmp,
            runtime_host=runtime_host,
            timeout=timeout,
        )
    else:
        runner = runners.get(agent_type)
        if runner is None:
            raise RuntimeError(f"Unsupported CyberGym agent type: {agent_type}")
        agent_id = runner(settings, timeout=timeout)
    if not agent_id:
        raise RuntimeError(f"CyberGym {agent_type} controller did not produce an agent_id")
    print(f"CYBERGYM_AGENT_ID={agent_id}", flush=True)
    return str(agent_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--runner-tmp", type=Path, required=True)
    parser.add_argument("--runtime-host", required=True)
    parser.add_argument("--agent-server-url", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args()
    settings = json.loads(args.episode.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise RuntimeError("CyberGym episode settings must be a JSON object")
    settings["agent_server_url"] = args.agent_server_url
    dispatch(
        settings,
        runner_tmp=args.runner_tmp,
        runtime_host=args.runtime_host,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
