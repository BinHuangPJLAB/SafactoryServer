from __future__ import annotations

from typing import Protocol
from uuid import uuid4


class IdentifierFactory(Protocol):
    def new(self, prefix: str) -> str: ...


class RandomIdentifierFactory:
    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

