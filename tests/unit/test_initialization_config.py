from pathlib import Path

import pytest

from server.config import DEFAULT_INITIALIZATION_CONFIG_PATH, Settings
from server.infrastructure.real.configuration import (
    TrustedConfigError,
    apply_initialization_config,
    load_initialization_config,
)


def test_bundled_initialization_uses_explicit_image_placeholders() -> None:
    config = load_initialization_config(DEFAULT_INITIALIZATION_CONFIG_PATH)

    assert config.placeholder is True
    assert config.gateway_base_image == (
        "mock.local/safactory-gateway:phase2-placeholder"
    )
    assert config.safactory_base_image == (
        "mock.local/safactory-controller:phase2-placeholder"
    )


def test_initialization_requires_both_images(tmp_path: Path) -> None:
    path = tmp_path / "initialization.yaml"
    path.write_text(
        'schema_version: "1.0"\ngateway_base_image: only-one-image\n',
        encoding="utf-8",
    )

    with pytest.raises(TrustedConfigError):
        load_initialization_config(path)


def test_complete_runtime_yaml_configures_database_rjob_and_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in {
        "RJOB_AK": "secret-access-key-value",
        "RJOB_SK": "secret-signing-key-value",
        "DB_URI": "s3://db",
    }.items():
        monkeypatch.setenv(name, value)
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway/gateway.yaml").write_text("storage_type: cloud\n")
    (tmp_path / "models.yaml").write_text("schema_version: '1.0'\nmodels: []\n")
    (tmp_path / "ranges.yaml").write_text("schema_version: '1.0'\nranges: []\n")
    (tmp_path / "env").mkdir()
    config_path = tmp_path / "initialization.yaml"
    config_path.write_text(
        """
schema_version: "1.0"
gateway_base_image: registry/image:safactory003
safactory_base_image: registry/image:safactory003
database:
  control_db_path: runtime/control.db
  data_platform_factory: sdk:create_client
  environment:
    WT_SDK_DB_URI: ${DB_URI}
rjob:
  backend: brainpp
  cluster_entry: https://rjob.example
  namespace: ns
  charged_group: group
  access_key: ${RJOB_AK}
  secret_key: ${RJOB_SK}
storage:
  environment:
    local_path: runtime/env
    rjob_source: gpfs://shared/env
    mount_path: /app/env
  results:
    local_path: runtime/results
    rjob_source: gpfs://shared/results
    mount_path: /app/results
catalog:
  models_path: models.yaml
  ranges_path: ranges.yaml
  environment_root: env
gateway:
  config_local_dir: gateway
  config_source_dir: gpfs://shared/gateway
safactory: {}
""".strip(),
        encoding="utf-8",
    )

    config = load_initialization_config(config_path)
    settings = apply_initialization_config(Settings(mode="real"), config)

    assert settings.rjob_backend == "brainpp"
    assert settings.rjob_cluster_entry == "https://rjob.example"
    assert settings.rjob_access_key == "secret-access-key-value"
    assert settings.control_db_path == tmp_path / "runtime/control.db"
    assert settings.shared_storage_rjob_source == "gpfs://shared/env"
    assert settings.results_rjob_source == "gpfs://shared/results"
    assert "secret-access-key-value" not in repr(config)
    assert "secret-signing-key-value" not in repr(config)
