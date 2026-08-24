from pathlib import Path

import pytest

from server.auth import AuthConfigLoadError, BearerAuthenticator, load_auth_config


def _write_config(path: Path, users: str) -> Path:
    path.write_text(
        f'''schema_version: "1.0"
users:
{users}
''',
        encoding="utf-8",
    )
    return path


def test_load_auth_config_and_authenticate(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path / "auth.yaml",
        "  - username: alice\n    api_key: alice-secret\n",
    )

    config = load_auth_config(path)
    authenticator = BearerAuthenticator.from_config(config)

    assert authenticator.authenticate(["Bearer alice-secret"]) == "alice"
    assert authenticator.authenticate(["bearer alice-secret"]) == "alice"
    assert authenticator.authenticate(["Bearer unknown"]) is None
    assert "alice-secret" not in repr(config)


@pytest.mark.parametrize(
    "users",
    [
        (
            "  - username: duplicate\n"
            "    api_key: first-key\n"
            "  - username: duplicate\n"
            "    api_key: second-key\n"
        ),
        (
            "  - username: first\n"
            "    api_key: duplicate-key\n"
            "  - username: second\n"
            "    api_key: duplicate-key\n"
        ),
        "  - username: invalid user\n    api_key: a-key\n",
        "  - username: valid-user\n    api_key: 'contains whitespace'\n",
        "  - username: valid-user\n    api_key: 密钥\n",
    ],
)
def test_invalid_auth_config_fails_closed(tmp_path: Path, users: str) -> None:
    path = _write_config(tmp_path / "auth.yaml", users)

    with pytest.raises(AuthConfigLoadError):
        load_auth_config(path)


def test_missing_auth_config_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(AuthConfigLoadError):
        load_auth_config(tmp_path / "missing.yaml")
