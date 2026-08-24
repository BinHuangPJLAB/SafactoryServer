from __future__ import annotations

from server.domain.entities import Model, Range
from server.domain.errors import DomainError, ErrorCode
from server.infrastructure.mock.fixture_schema import FixtureDocument


class MockCatalog:
    def __init__(self, document: FixtureDocument | None) -> None:
        self._document = document

    async def list_models(self) -> tuple[Model, ...]:
        document = self._require_document()
        return tuple(
            Model(model.model_id, model.name, model.available) for model in document.models
        )

    async def get_model(self, model_id: str) -> Model | None:
        document = self._require_document()
        fixture = next((item for item in document.models if item.model_id == model_id), None)
        if fixture is None:
            return None
        return Model(fixture.model_id, fixture.name, fixture.available)

    async def get_range(self, range_id: str) -> Range | None:
        document = self._require_document()
        fixture = next(
            (item for item in document.ranges if item.range_id == range_id), None
        )
        if fixture is None:
            return None
        return Range(
            range_id=fixture.range_id,
            available=fixture.available,
            availability_retryable=fixture.availability_retryable,
            supported_model_ids=frozenset(fixture.supported_models),
        )

    def _require_document(self) -> FixtureDocument:
        if self._document is None:
            raise DomainError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        return self._document

