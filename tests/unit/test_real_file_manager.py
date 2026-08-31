from pathlib import Path

import pytest
import yaml
from real_helpers import REAL_GATEWAY_ROUTES, write_real_configs

from server.infrastructure.real.configuration import RealCatalog, load_initialization_config
from server.infrastructure.real.file_manager import FileBindingError, SharedFileManager


def test_publishes_immutable_grouped_job_files(tmp_path: Path) -> None:
    ranges = write_real_configs(tmp_path)
    manager = SharedFileManager(
        tmp_path / "shared", RealCatalog(ranges, REAL_GATEWAY_ROUTES)
    )

    binding = manager.bind("job_real_001", "range_real_001")
    repeated = manager.bind("job_real_001", "range_real_001")

    assert repeated == binding
    assert binding.total_episodes == 4
    assert binding.groups[0].dataset_records == 2
    assert Path(binding.result_source).is_dir()
    rendered = yaml.safe_load(Path(binding.input_source, "config.yaml").read_text())
    assert rendered["environments"][0]["dataset"] == (
        "/mnt/safactory-job/groups/browser/dataset.jsonl"
    )
    start = yaml.safe_load(
        Path(binding.input_source, "groups/browser/start.rjob.yaml").read_text()
    )
    assert start["rjob"]["mount_config"] == [
        f"{binding.result_source}:/app/results"
    ]
    assert yaml.safe_load(
        Path(binding.input_source, "gateway/gateway.yaml").read_text()
    ) == {}
    assert binding.gateway_config_source.endswith("/job_real_001/gateway")


def test_uses_cluster_sources_and_copies_environment_runtime_files(
    tmp_path: Path,
) -> None:
    ranges = write_real_configs(tmp_path)
    (tmp_path / "runner.py").write_text("print('runner')\n", encoding="utf-8")
    manager = SharedFileManager(
        tmp_path / "generated-env",
        RealCatalog(ranges, REAL_GATEWAY_ROUTES),
        rjob_root="gpfs://shared/generated-env",
        results_root=tmp_path / "results",
        results_rjob_root="gpfs://shared/results",
    )

    binding = manager.bind("job_real_002", "range_real_001")

    assert binding.input_source == "gpfs://shared/generated-env/job_real_002"
    assert binding.result_source == "gpfs://shared/results/job_real_002"
    assert Path(binding.input_local_path, "groups/browser/runner.py").is_file()


def test_accepts_runner_provided_by_trusted_runtime_without_source(
    tmp_path: Path,
) -> None:
    ranges = write_real_configs(tmp_path)
    start_path = tmp_path / "browser.start.yaml"
    start = yaml.safe_load(start_path.read_text(encoding="utf-8"))
    start["container"]["runner_entrypoint"] = {
        "command": "/bin/bash /opt/trusted-runner/runner.sh",
    }
    start["rjob"]["mount_config"].insert(0, "trusted-runner:/opt/trusted-runner")
    start_path.write_text(yaml.safe_dump(start, sort_keys=False), encoding="utf-8")
    manager = SharedFileManager(
        tmp_path / "shared", RealCatalog(ranges, REAL_GATEWAY_ROUTES)
    )

    binding = manager.bind("job_mounted_runner", "range_real_001")

    rendered = yaml.safe_load(
        Path(
            binding.input_local_path,
            "groups/browser/start.rjob.yaml",
        ).read_text(encoding="utf-8")
    )
    assert rendered["container"]["runner_entrypoint"] == {
        "command": "/bin/bash /opt/trusted-runner/runner.sh",
    }


def test_rejects_runner_entrypoint_without_command(tmp_path: Path) -> None:
    ranges = write_real_configs(tmp_path)
    start_path = tmp_path / "browser.start.yaml"
    start = yaml.safe_load(start_path.read_text(encoding="utf-8"))
    start["container"]["runner_entrypoint"] = {"source": "/trusted/runner.py"}
    start_path.write_text(yaml.safe_dump(start, sort_keys=False), encoding="utf-8")
    manager = SharedFileManager(
        tmp_path / "shared", RealCatalog(ranges, REAL_GATEWAY_ROUTES)
    )

    with pytest.raises(FileBindingError, match="runner_entrypoint.command is missing"):
        manager.bind("job_missing_runner_command", "range_real_001")


def test_bundled_managed_environment_can_be_bound(
    tmp_path: Path, monkeypatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    values = {
        "RJOB_ACCESS_KEY": "ak",
        "RJOB_SECRET_KEY": "sk",
        "WT_SDK_DB_URI": "s3://db",
        "WT_SDK_ENV_CONFIG_DB_URI": "s3://env",
        "WT_SDK_S3_ENDPOINT": "https://s3.example",
        "AWS_ACCESS_KEY_ID": "ak",
        "AWS_SECRET_ACCESS_KEY": "sk",
        "SAFACTORY_ENV_RJOB_SOURCE": "gpfs://shared/env",
        "SAFACTORY_RESULTS_RJOB_SOURCE": "gpfs://shared/results",
        "GATEWAY_MODEL_BASE_URL": "https://model.example/v1",
        "GATEWAY_MODEL_API_KEY": "model-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = load_initialization_config(root / "examples/real/initialization.yaml")
    assert config.catalog is not None
    catalog = RealCatalog(
        config.catalog.ranges_path,
        config.gateway.config["llm_routes"],
        config.catalog.environment_root,
    )
    manager = SharedFileManager(
        tmp_path / "env",
        catalog,
        rjob_root="gpfs://shared/env",
        results_root=tmp_path / "results",
        results_rjob_root="gpfs://shared/results",
        gateway_config=config.gateway.config,
        gateway_config_mount_dir=config.gateway.config_mount_dir,
        gateway_config_filename=config.gateway.config_filename,
    )

    binding = manager.bind("job_smoke", "range_cyberrange_smoke_001")
    full_binding = manager.bind("job_full", "range_cyberrange_full_001")
    harbor_binding = manager.bind(
        "job_harbor", "range_harbor_vulhub_claude_kimi_all_001"
    )

    runner = Path(binding.input_local_path, "groups/cyberrange/runner.sh")
    assert runner.is_file()
    assert binding.agent_start_config_path == (
        "/mnt/safactory-job/groups/cyberrange/start.rjob.yaml"
    )
    assert binding.total_episodes == 1
    assert full_binding.total_episodes == 4
    smoke_range = catalog.resolve_range("range_cyberrange_smoke_001")
    full_range = catalog.resolve_range("range_cyberrange_full_001")
    assert smoke_range is not None
    assert full_range is not None
    assert smoke_range.groups[0].result_artifact == "runtime-test-result.json"
    assert full_range.groups[0].result_artifact == "runtime-test-result.json"
    assert harbor_binding.total_episodes == 474
    assert harbor_binding.launcher_rjob_config_path == (
        f"{harbor_binding.input_target}/launcher.rjob.yaml"
    )
    assert harbor_binding.launcher_rjob_config_checksum
    harbor_launcher = yaml.safe_load(
        Path(harbor_binding.input_local_path, "launcher.rjob.yaml").read_text()
    )
    assert harbor_launcher["rjob"]["name_prefix"] == "harbor-vulhub"
    assert harbor_launcher["rjob"]["submit_concurrency"] == 40
    harbor_start = yaml.safe_load(
        Path(
            harbor_binding.input_local_path,
            "groups/harbor/start.rjob.yaml",
        ).read_text()
    )
    assert harbor_start["rjob"]["privileged"] is True
    assert harbor_start["rjob"]["resources"]["custom_resources"] == [
        "brainpp.cn/fuse=1"
    ]
    assert harbor_start["rjob"]["mount_config"][-1] == (
        f"{harbor_binding.result_source}:/app/results"
    )
    rendered_start = yaml.safe_load(
        Path(
            binding.input_local_path,
            "groups/cyberrange/start.rjob.yaml",
        ).read_text()
    )
    runner_entrypoint = rendered_start["container"]["runner_entrypoint"]
    assert runner_entrypoint == {
        "command": "/bin/bash /opt/safactory-cyberrange/runner.sh < /dev/null",
    }
    mounts = rendered_start["rjob"]["mount_config"]
    assert any(
        "yxwang:/mnt/shared-storage-user/evoagi-share" in mount for mount in mounts
    )
    assert any("/opt/safactory-cyberrange" in mount for mount in mounts)
    assert mounts[-1] == f"{binding.result_source}:/app/results"
    gateway = yaml.safe_load(
        Path(binding.input_local_path, "gateway/gateway.yaml").read_text()
    )
    assert gateway["llm_routes"]["kimi-k3"]["api_key"] == "model-secret"
