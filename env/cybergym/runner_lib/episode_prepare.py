#!/usr/bin/env python3
"""Resolve one Safactory request into stable CyberGym episode settings."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import socket
import time
from pathlib import Path
from typing import Any


SUPPORTED_AGENT_TYPES = {"codex", "cybench", "enigma", "opencode", "openhands"}

AGENT_DEFAULTS: dict[str, dict[str, str]] = {
    "codex": {
        "image": "cybergym/codex:latest",
        "image_archive": "codex.tar",
    },
    "cybench": {
        "image": "cybergym/cybench:latest",
        "image_archive": "cybench.tar",
        "repo_dir": "cybench-repo",
    },
    "enigma": {
        "image": "sweagent/enigma:latest",
        "image_archive": "enigma.tar",
        "repo_dir": "enigma-repo",
    },
    "opencode": {
        "image": "opencode:001",
        "image_archive": "opencode.tar",
    },
    "openhands": {
        "image": "docker.all-hands.dev/all-hands-ai/runtime:0.33-nikolaik",
        "image_archive": "openhands.tar",
        "repo_dir": "openhands-repo",
    },
}


def configured_path(
    dataset: dict[str, Any],
    env_params: dict[str, Any],
    key: str,
    env_key: str,
) -> Path:
    value = first_text(dataset.get(key), env_params.get(key), os.environ.get(env_key))
    if not value:
        raise RuntimeError(
            f"Missing required CyberGym path setting: {key} "
            f"(set env_params.{key} or {env_key})"
        )
    return Path(value).expanduser()


def results_dir(
    request: dict[str, Any],
    env_params: dict[str, Any],
    dataset: dict[str, Any],
) -> Path:
    root = configured_path(dataset, env_params, "results_root", "CYBERGYM_RESULTS_ROOT")
    return root / safe_part(request.get("job_id")) / safe_part(request.get("session_id"))


def model_ref(
    request: dict[str, Any],
    env_params: dict[str, Any],
    dataset: dict[str, Any],
) -> str:
    explicit = first_text(
        dataset.get("model_ref"),
        env_params.get("model_ref"),
        os.environ.get("CYBERGYM_MODEL_REF"),
    )
    value = explicit or first_text(
        dataset.get("route_model"),
        env_params.get("route_model"),
        os.environ.get("SAFACTORY_ROUTE_MODEL"),
        request.get("model"),
    )
    if not value:
        raise RuntimeError("CyberGym runner could not resolve an agent model")
    if value.startswith("safactory/"):
        value = value.split("/", 1)[1]
    return value if "/" in value else f"openai/{value}"


def max_iter(
    request: dict[str, Any],
    env_params: dict[str, Any],
    dataset: dict[str, Any],
) -> int:
    configured = positive_int(
        dataset.get("max_iter"),
        env_params.get("max_iter"),
        default=positive_int(request.get("max_steps"), default=50),
    )
    request_limit = positive_int(request.get("max_steps"), default=configured)
    return max(1, min(configured, request_limit))


def agent_mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a mapping")
    return value


def agent_value(
    dataset: dict[str, Any],
    env_params: dict[str, Any],
    key: str,
    env_key: str,
    *legacy_keys: str,
    default: Any = "",
) -> Any:
    """Resolve agent settings with dataset > start env > agent config precedence."""
    dataset_agent = agent_mapping(dataset.get("agent"), "env_params.dataset.agent")
    params_agent = agent_mapping(env_params.get("agent"), "env_params.agent")
    flat_key = "agent_type" if key == "type" else f"agent_{key}"
    candidates = [dataset_agent.get(key), dataset.get(flat_key), os.environ.get(env_key)]
    candidates.extend((params_agent.get(key), env_params.get(flat_key)))
    for legacy_key in legacy_keys:
        if not legacy_key:
            continue
        candidates.extend((dataset.get(legacy_key), env_params.get(legacy_key)))
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def agent_options(dataset: dict[str, Any], env_params: dict[str, Any]) -> dict[str, Any]:
    params_agent = agent_mapping(env_params.get("agent"), "env_params.agent")
    dataset_agent = agent_mapping(dataset.get("agent"), "env_params.dataset.agent")
    merged: dict[str, Any] = {}
    for value, name in (
        (env_params.get("agent_options"), "env_params.agent_options"),
        (params_agent.get("options"), "env_params.agent.options"),
    ):
        merged.update(agent_mapping(value, name))

    raw_env = str(os.environ.get("CYBERGYM_AGENT_OPTIONS_JSON") or "").strip()
    if raw_env:
        try:
            parsed = json.loads(raw_env)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CYBERGYM_AGENT_OPTIONS_JSON must contain valid JSON") from exc
        merged.update(agent_mapping(parsed, "CYBERGYM_AGENT_OPTIONS_JSON"))

    for value, name in (
        (dataset.get("agent_options"), "env_params.dataset.agent_options"),
        (dataset_agent.get("options"), "env_params.dataset.agent.options"),
    ):
        merged.update(agent_mapping(value, name))
    return merged


def resolve_agent(
    cybergym_root: Path,
    dataset: dict[str, Any],
    env_params: dict[str, Any],
) -> dict[str, Any]:
    agent_type = str(
        agent_value(
            dataset,
            env_params,
            "type",
            "CYBERGYM_AGENT_TYPE",
            default="openhands",
        )
    ).strip().lower()
    if agent_type not in SUPPORTED_AGENT_TYPES:
        raise RuntimeError(
            f"Unsupported CyberGym agent_type {agent_type!r}; "
            f"expected one of {sorted(SUPPORTED_AGENT_TYPES)}"
        )

    defaults = AGENT_DEFAULTS[agent_type]
    agent_root = cybergym_root / "examples" / "agents" / agent_type
    runner = Path(
        str(
            agent_value(
                dataset,
                env_params,
                "runner",
                "CYBERGYM_AGENT_RUNNER",
                "openhands_runner" if agent_type == "openhands" else "",
                default=agent_root / "run.py",
            )
        )
    ).expanduser()

    repo_default = ""
    if defaults.get("repo_dir"):
        repo_default = str(agent_root / defaults["repo_dir"])
    repo_legacy = "openhands_repo" if agent_type == "openhands" else ""
    repo_value = agent_value(
        dataset,
        env_params,
        "repo",
        "CYBERGYM_AGENT_REPO",
        repo_legacy,
        default=repo_default,
    )
    repo = Path(str(repo_value)).expanduser() if str(repo_value or "").strip() else None

    python_default = ""
    if agent_type == "enigma" and repo is not None:
        python_default = str(repo / "venv" / "bin" / "python")
    python_value = agent_value(
        dataset,
        env_params,
        "python",
        "CYBERGYM_AGENT_PYTHON",
        default=python_default,
    )
    agent_python = (
        Path(str(python_value)).expanduser()
        if str(python_value or "").strip()
        else None
    )

    image = required_text(
        agent_value(
            dataset,
            env_params,
            "image",
            "CYBERGYM_AGENT_IMAGE",
            default=defaults["image"],
        ),
        "agent.image",
    )
    image_archive = required_text(
        agent_value(
            dataset,
            env_params,
            "image_archive",
            "CYBERGYM_AGENT_IMAGE_ARCHIVE",
            default=defaults["image_archive"],
        ),
        "agent.image_archive",
    )
    model = first_text(
        agent_value(
            dataset,
            env_params,
            "model",
            "CYBERGYM_AGENT_MODEL",
            default="",
        )
    )
    return {
        "type": agent_type,
        "runner": runner,
        "repo": repo,
        "python": agent_python,
        "image": image,
        "image_archive": image_archive,
        "model": model,
        "options": agent_options(dataset, env_params),
    }


def prepare(request_path: Path, output_path: Path, env_path: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise RuntimeError("SimulationStartRequest must be a JSON object")

    session_id = required_text(request.get("session_id"), "session_id")
    env_params = request.get("env_params") if isinstance(request.get("env_params"), dict) else {}
    dataset = env_params.get("dataset") if isinstance(env_params.get("dataset"), dict) else {}
    task_id = required_text(dataset.get("task_id"), "env_params.dataset.task_id")
    difficulty = first_text(dataset.get("difficulty"), env_params.get("difficulty"), "level1")

    cybergym_root = configured_path(dataset, env_params, "cybergym_root", "CYBERGYM_ROOT")
    data_dir = configured_path(dataset, env_params, "data_dir", "CYBERGYM_DATA_DIR")
    image_archive_dir = configured_path(
        dataset,
        env_params,
        "image_archive_dir",
        "CYBERGYM_IMAGE_ARCHIVE_DIR",
    )
    agent = resolve_agent(cybergym_root, dataset, env_params)
    result_dir = results_dir(request, env_params, dataset)
    server_dir = result_dir / "server"
    logs_dir = result_dir / "logs"
    tmp_dir = result_dir / "tmp"
    for directory in (result_dir, server_dir, logs_dir, tmp_dir):
        directory.mkdir(parents=True, exist_ok=True)

    validate_runtime_paths(
        cybergym_root=cybergym_root,
        data_dir=data_dir,
        image_archive_dir=image_archive_dir,
        agent=agent,
    )

    gateway_url = first_text(os.environ.get("SAFACTORY_GATEWAY_SESSION_URL_CONTAINER"))
    if not gateway_url:
        base = required_text(request.get("gateway_base_url"), "gateway_base_url").rstrip("/")
        gateway_url = f"{base}/{session_id}"

    verify_timeout_s = positive_int(
        dataset.get("verify_timeout_s"),
        env_params.get("verify_timeout_s"),
        default=600,
    )
    agent_timeout_s = positive_int(
        dataset.get("timeout_s"),
        env_params.get("timeout_s"),
        default=1800,
    )
    outer_timeout_s = positive_int(request.get("agent_start_timeout_s"), default=3600)
    server_port = free_port()
    started_at_epoch = time.time()

    settings: dict[str, Any] = {
        "session_id": session_id,
        "job_id": first_text(request.get("job_id")),
        "task_id": task_id,
        "difficulty": difficulty,
        "cybergym_root": str(cybergym_root),
        "data_dir": str(data_dir),
        "image_archive_dir": str(image_archive_dir),
        "results_dir": str(result_dir),
        "server_dir": str(server_dir),
        "logs_dir": str(logs_dir),
        "tmp_dir": str(tmp_dir),
        "db_path": str(server_dir / "poc.db"),
        "agent_type": agent["type"],
        "agent_runner": str(agent["runner"]),
        "agent_repo": str(agent["repo"] or ""),
        "agent_python": str(agent["python"] or ""),
        "agent_image": agent["image"],
        "agent_image_archive": agent["image_archive"],
        "agent_options": agent["options"],
        "mask_map_path": first_text(agent["options"].get("mask_map_path")),
        "model_ref": agent["model"] or model_ref(request, env_params, dataset),
        "gateway_url": gateway_url.rstrip("/"),
        "gateway_api_key": first_text(
            os.environ.get("SAFACTORY_GATEWAY_API_KEY"),
            "safactory",
        ),
        "cybergym_api_key": first_text(
            dataset.get("cybergym_api_key"),
            env_params.get("cybergym_api_key"),
            os.environ.get("CYBERGYM_API_KEY"),
            secrets.token_urlsafe(32),
        ),
        "max_iter": max_iter(request, env_params, dataset),
        "temperature": first_text(
            dataset.get("temperature"),
            env_params.get("temperature"),
            request.get("temperature"),
            "0.0",
        ),
        "top_p": first_text(dataset.get("top_p"), env_params.get("top_p"), "1.0"),
        "max_output_tokens": first_text(
            dataset.get("max_output_tokens"),
            env_params.get("max_output_tokens"),
            "4096",
        ),
        "silent": bool_value(
            dataset.get("silent"),
            env_params.get("silent"),
            default=True,
        ),
        "native_tool_calling": optional_bool(
            dataset.get("native_tool_calling"),
            env_params.get("native_tool_calling"),
        ),
        "configured_agent_timeout_s": agent_timeout_s,
        "configured_verify_timeout_s": verify_timeout_s,
        "image_load_timeout_s": positive_int(
            dataset.get("image_load_timeout_s"),
            env_params.get("image_load_timeout_s"),
            default=1800,
        ),
        "deadline_epoch": int(started_at_epoch + outer_timeout_s),
        "started_at_epoch": started_at_epoch,
        "docker_bin": first_text(os.environ.get("DOCKER_BIN"), "docker"),
        "python_bin": first_text(os.environ.get("PYTHON_BIN"), "python3.12"),
        "configured_runtime_host": first_text(
            dataset.get("openhands_runtime_host"),
            env_params.get("openhands_runtime_host"),
            os.environ.get("CYBERGYM_OPENHANDS_RUNTIME_HOST"),
        ),
        "configured_controller_host": first_text(
            dataset.get("agent_server_host"),
            env_params.get("agent_server_host"),
            os.environ.get("CYBERGYM_AGENT_SERVER_HOST"),
        ),
        "server_port": server_port,
        "server_url": f"http://127.0.0.1:{server_port}",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_env(env_path, settings)


def validate_runtime_paths(
    *,
    cybergym_root: Path,
    data_dir: Path,
    image_archive_dir: Path,
    agent: dict[str, Any],
) -> None:
    agent_type = str(agent["type"])
    runner = Path(agent["runner"])
    repo = Path(agent["repo"]) if agent.get("repo") else None
    agent_python = Path(agent["python"]) if agent.get("python") else None
    checks: list[tuple[bool, str]] = [
        (cybergym_root.is_dir(), f"CyberGym root does not exist: {cybergym_root}"),
        (data_dir.is_dir(), f"CyberGym data directory does not exist: {data_dir}"),
        (
            image_archive_dir.is_dir(),
            f"CyberGym image archive directory does not exist: {image_archive_dir}",
        ),
        (runner.is_file(), f"CyberGym {agent_type} runner does not exist: {runner}"),
    ]
    if agent_type == "openhands":
        checks.extend(
            [
                (
                    (runner.parent / "template" / "config.toml").is_file(),
                    f"OpenHands template does not exist next to {runner}",
                ),
                (repo is not None and repo.is_dir(), f"OpenHands repository does not exist: {repo}"),
                (
                    repo is not None and (repo / "Makefile").is_file(),
                    f"OpenHands repository is not populated or built: {repo}",
                ),
            ]
        )
    elif agent_type == "cybench":
        checks.extend(
            [
                (repo is not None and repo.is_dir(), f"Cybench repository does not exist: {repo}"),
                (
                    repo is not None and (repo / "run_task.py").is_file(),
                    f"Cybench run_task.py does not exist: {repo}",
                ),
                (
                    repo is not None and (repo / "agent").is_dir(),
                    f"Cybench agent directory does not exist: {repo}",
                ),
            ]
        )
    elif agent_type == "enigma":
        checks.extend(
            [
                (repo is not None and (repo / "run.py").is_file(), f"Enigma repository is incomplete: {repo}"),
                (
                    agent_python is not None and agent_python.is_file(),
                    f"Enigma Python executable does not exist: {agent_python}",
                ),
            ]
        )
    for valid, message in checks:
        if not valid:
            raise RuntimeError(message)


def write_env(path: Path, settings: dict[str, Any]) -> None:
    mapping = {
        "EPISODE_SESSION_ID": settings["session_id"],
        "EPISODE_TASK_ID": settings["task_id"],
        "EPISODE_DIFFICULTY": settings["difficulty"],
        "EPISODE_CYBERGYM_ROOT": settings["cybergym_root"],
        "EPISODE_DATA_DIR": settings["data_dir"],
        "EPISODE_IMAGE_ARCHIVE_DIR": settings["image_archive_dir"],
        "EPISODE_RESULTS_DIR": settings["results_dir"],
        "EPISODE_SERVER_DIR": settings["server_dir"],
        "EPISODE_LOGS_DIR": settings["logs_dir"],
        "EPISODE_TMP_DIR": settings["tmp_dir"],
        "EPISODE_DB_PATH": settings["db_path"],
        "EPISODE_AGENT_TYPE": settings["agent_type"],
        "EPISODE_AGENT_RUNNER": settings["agent_runner"],
        "EPISODE_AGENT_REPO": settings["agent_repo"],
        "EPISODE_AGENT_PYTHON": settings["agent_python"],
        "EPISODE_AGENT_IMAGE": settings["agent_image"],
        "EPISODE_AGENT_IMAGE_ARCHIVE": settings["agent_image_archive"],
        "EPISODE_MASK_MAP_PATH": settings["mask_map_path"],
        "EPISODE_MODEL_REF": settings["model_ref"],
        "EPISODE_GATEWAY_URL": settings["gateway_url"],
        "EPISODE_GATEWAY_API_KEY": settings["gateway_api_key"],
        "EPISODE_CYBERGYM_API_KEY": settings["cybergym_api_key"],
        "EPISODE_MAX_ITER": settings["max_iter"],
        "EPISODE_TEMPERATURE": settings["temperature"],
        "EPISODE_TOP_P": settings["top_p"],
        "EPISODE_MAX_OUTPUT_TOKENS": settings["max_output_tokens"],
        "EPISODE_SILENT": str(settings["silent"]).lower(),
        "EPISODE_NATIVE_TOOL_CALLING": (
            ""
            if settings["native_tool_calling"] is None
            else str(settings["native_tool_calling"]).lower()
        ),
        "EPISODE_AGENT_TIMEOUT_S": settings["configured_agent_timeout_s"],
        "EPISODE_VERIFY_TIMEOUT_S": settings["configured_verify_timeout_s"],
        "EPISODE_IMAGE_LOAD_TIMEOUT_S": settings["image_load_timeout_s"],
        "EPISODE_DEADLINE_EPOCH": settings["deadline_epoch"],
        "EPISODE_DOCKER_BIN": settings["docker_bin"],
        "EPISODE_PYTHON_BIN": settings["python_bin"],
        "EPISODE_CONFIGURED_RUNTIME_HOST": settings["configured_runtime_host"],
        "EPISODE_CONFIGURED_CONTROLLER_HOST": settings["configured_controller_host"],
        "EPISODE_SERVER_PORT": settings["server_port"],
        "EPISODE_SERVER_URL": settings["server_url"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            f"{key}={shlex.quote(str(value))}\n"
            for key, value in mapping.items()
        ),
        encoding="utf-8",
    )


def required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"SimulationStartRequest missing {name}")
    return text


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def int_value(*values: Any) -> int:
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def positive_int(*values: Any, default: int) -> int:
    value = int_value(*values)
    return value if value > 0 else max(1, int(default))


def bool_value(*values: Any, default: bool) -> bool:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return bool(default)


def optional_bool(*values: Any) -> bool | None:
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return None


def safe_part(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "item"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-out", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.request, args.output, args.env_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
