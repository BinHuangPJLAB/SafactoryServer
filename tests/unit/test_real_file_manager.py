from pathlib import Path

import yaml
from real_helpers import write_real_configs

from server.infrastructure.real.configuration import RealCatalog, load_initialization_config
from server.infrastructure.real.file_manager import SharedFileManager


def test_publishes_immutable_grouped_job_files(tmp_path: Path) -> None:
    models, ranges = write_real_configs(tmp_path)
    manager = SharedFileManager(tmp_path / "shared", RealCatalog(models, ranges))

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


def test_uses_cluster_sources_and_copies_environment_runtime_files(
    tmp_path: Path,
) -> None:
    models, ranges = write_real_configs(tmp_path)
    (tmp_path / "runner.py").write_text("print('runner')\n", encoding="utf-8")
    manager = SharedFileManager(
        tmp_path / "generated-env",
        RealCatalog(models, ranges),
        rjob_root="gpfs://shared/generated-env",
        results_root=tmp_path / "results",
        results_rjob_root="gpfs://shared/results",
    )

    binding = manager.bind("job_real_002", "range_real_001")

    assert binding.input_source == "gpfs://shared/generated-env/job_real_002"
    assert binding.result_source == "gpfs://shared/results/job_real_002"
    assert Path(binding.input_local_path, "groups/browser/runner.py").is_file()


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
        "SAFACTORY_GATEWAY_CONFIG_SOURCE_DIR": "gpfs://shared/gateway",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = load_initialization_config(root / "examples/real/initialization.yaml")
    assert config.catalog is not None
    catalog = RealCatalog(
        config.catalog.models_path,
        config.catalog.ranges_path,
        config.catalog.environment_root,
    )
    manager = SharedFileManager(
        tmp_path / "env",
        catalog,
        rjob_root="gpfs://shared/env",
        results_root=tmp_path / "results",
        results_rjob_root="gpfs://shared/results",
    )

    binding = manager.bind("job_smoke", "range_harbor_smoke_001")

    runner = Path(binding.input_local_path, "groups/harbor/runner.py")
    assert runner.is_file()
    assert binding.agent_start_config_path == (
        "/mnt/safactory-job/groups/harbor/start.rjob.yaml"
    )
