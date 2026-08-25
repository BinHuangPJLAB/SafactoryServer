from pathlib import Path

import pytest

from server.config import DEFAULT_INITIALIZATION_CONFIG_PATH, Settings
from server.infrastructure.real.configuration import (
    TrustedConfigError,
    apply_initialization_config,
    load_initialization_config,
)


def test_bundled_initialization_is_a_complete_real_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "RJOB_ACCESS_KEY": "ak",
        "RJOB_SECRET_KEY": "sk",
        "WT_SDK_DB_URI": "s3://db",
        "WT_SDK_ENV_CONFIG_DB_URI": "s3://env",
        "WT_SDK_S3_ENDPOINT": "https://s3.example",
        "AWS_ACCESS_KEY_ID": "aws-ak",
        "AWS_SECRET_ACCESS_KEY": "aws-sk",
        "SAFACTORY_ENV_RJOB_SOURCE": "gpfs://shared/env",
        "SAFACTORY_RESULTS_RJOB_SOURCE": "gpfs://shared/results",
        "GATEWAY_MODEL_BASE_URL": "https://model.example/v1",
        "GATEWAY_MODEL_API_KEY": "model-secret",
    }.items():
        monkeypatch.setenv(name, value)
    config = load_initialization_config(DEFAULT_INITIALIZATION_CONFIG_PATH)

    assert config.gateway_base_image.endswith("server:safactory003")
    assert config.safactory_base_image.endswith("server:safactory003")
    assert config.gateway is not None
    assert config.gateway.config["llm_routes"]["kimi-k3"]["api_key"] == (
        "model-secret"
    )
    assert "model-secret" not in repr(config)
    assert config.database is not None
    assert config.database.environment["AWS_REGION"] == "cn-shanghai"
    assert config.database.environment["AWS_SESSION_TOKEN"] == ""


def test_initialization_requires_both_images(tmp_path: Path) -> None:
    path = tmp_path / "initialization.yaml"
    path.write_text(
        'schema_version: "1.0"\ngateway_base_image: only-one-image\n',
        encoding="utf-8",
    )

    with pytest.raises(TrustedConfigError):
        load_initialization_config(path)


def test_initialization_requires_runtime_sections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "initialization.yaml"
    path.write_text(
        """
schema_version: "1.0"
gateway_base_image: registry/gateway:real
safactory_base_image: registry/safactory:real
""".strip(),
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
  ranges_path: ranges.yaml
  environment_root: env
gateway:
  config:
    listen_port: 8000
    base_session_path: /v1/sessions
    storage_type: cloud
    llm_routes:
      test:
        base_url: https://model.example/v1
safactory: {}
""".strip(),
        encoding="utf-8",
    )

    config = load_initialization_config(config_path)
    settings = apply_initialization_config(Settings(), config)

    assert settings.rjob_backend == "brainpp"
    assert settings.rjob_cluster_entry == "https://rjob.example"
    assert settings.rjob_access_key == "secret-access-key-value"
    assert settings.control_db_path == tmp_path / "runtime/control.db"
    assert settings.shared_storage_rjob_source == "gpfs://shared/env"
    assert settings.results_rjob_source == "gpfs://shared/results"
    assert '"listen_port":8000' in settings.gateway_config_json
    assert "secret-access-key-value" not in repr(config)
    assert "secret-signing-key-value" not in repr(config)
