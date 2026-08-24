from __future__ import annotations

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


class AuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TrustedUser(AuthModel):
    username: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._@-]*$",
    )
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        api_key = value.get_secret_value()
        if not api_key or len(api_key) > 512 or any(char.isspace() for char in api_key):
            raise ValueError("api_key must be a non-empty Bearer token without whitespace.")
        if not api_key.isascii():
            raise ValueError("api_key must contain only ASCII characters.")
        return value


class AuthConfig(AuthModel):
    schema_version: Literal["1.0"]
    users: tuple[TrustedUser, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_credentials(self) -> Self:
        usernames = [user.username for user in self.users]
        api_keys = [user.api_key.get_secret_value() for user in self.users]
        if len(usernames) != len(set(usernames)):
            raise ValueError("username values must be unique.")
        if len(api_keys) != len(set(api_keys)):
            raise ValueError("api_key values must be unique.")
        return self
