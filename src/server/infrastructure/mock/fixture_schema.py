from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FixtureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelFixture(FixtureModel):
    model_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    available: bool
    internal: dict[str, Any] = Field(default_factory=dict)


class RangeFixture(FixtureModel):
    range_id: str = Field(min_length=1)
    available: bool
    availability_retryable: bool = False
    supported_models: tuple[str, ...] = Field(min_length=1)
    scenario_id: str = Field(min_length=1)


class ResultFixture(FixtureModel):
    running_after_ms: int = Field(ge=0)
    completed_after_ms: int = Field(gt=0)
    score: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.completed_after_ms <= self.running_after_ms:
            raise ValueError("Result completion must occur after result start.")
        return self


class TrajectoryFixture(FixtureModel):
    model_input: dict[str, Any] | None = None
    model_output: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    observation: dict[str, Any] | None = None


class StepFixture(FixtureModel):
    fixture_id: str = Field(min_length=1)
    visible_after_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    trajectory: TrajectoryFixture


class SessionFixture(FixtureModel):
    fixture_id: str = Field(min_length=1)
    visible_after_ms: int = Field(ge=0)
    result: ResultFixture
    steps: tuple[StepFixture, ...]


class ScenarioFixture(FixtureModel):
    preparing_after_ms: int = Field(ge=0)
    sessions: tuple[SessionFixture, ...] = Field(min_length=1)


class FixtureDocument(FixtureModel):
    schema_version: Literal["1.0"]
    models: tuple[ModelFixture, ...] = Field(min_length=1)
    ranges: tuple[RangeFixture, ...] = Field(min_length=1)
    scenarios: dict[str, ScenarioFixture] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references_and_timelines(self) -> Self:
        model_ids = [model.model_id for model in self.models]
        range_ids = [range_config.range_id for range_config in self.ranges]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("model_id values must be unique.")
        if len(range_ids) != len(set(range_ids)):
            raise ValueError("range_id values must be unique.")

        known_models = set(model_ids)
        for range_config in self.ranges:
            if range_config.scenario_id not in self.scenarios:
                raise ValueError(
                    f"Unknown scenario_id for range {range_config.range_id}."
                )
            if unknown_models := set(range_config.supported_models) - known_models:
                raise ValueError(
                    f"Unknown supported model for range {range_config.range_id}: "
                    f"{sorted(unknown_models)}"
                )

        for scenario_id, scenario in self.scenarios.items():
            session_ids = [session.fixture_id for session in scenario.sessions]
            if len(session_ids) != len(set(session_ids)):
                raise ValueError(f"Session fixture IDs must be unique in {scenario_id}.")
            for session in scenario.sessions:
                if session.result.running_after_ms < session.visible_after_ms:
                    raise ValueError("A result cannot start before its session is visible.")
                step_ids = [step.fixture_id for step in session.steps]
                if len(step_ids) != len(set(step_ids)):
                    raise ValueError(
                        f"Step fixture IDs must be unique in session {session.fixture_id}."
                    )
                for step in session.steps:
                    if step.visible_after_ms < session.visible_after_ms:
                        raise ValueError("A step cannot be visible before its session.")
                    if step.visible_after_ms > session.result.completed_after_ms:
                        raise ValueError("A step cannot appear after result completion.")
                    if step.duration_ms > step.visible_after_ms:
                        raise ValueError("Step duration cannot predate the job.")
        return self

