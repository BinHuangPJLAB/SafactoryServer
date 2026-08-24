from __future__ import annotations

import importlib
import inspect
import math
from datetime import datetime
from typing import Any, Protocol

from server.domain.entities import (
    ResultStatus,
    SessionResult,
    SessionSteps,
    StepIndex,
    StepTrajectory,
)


class DataPlatformError(RuntimeError):
    """The configured data-platform SDK failed or returned invalid data."""


class DataPlatformRepository(Protocol):
    async def preflight(self) -> None: ...

    async def list_session_ids(self, job_id: str) -> tuple[str, ...]: ...

    async def get_result(self, job_id: str, session_id: str) -> SessionResult | None: ...

    async def get_steps(self, job_id: str, session_id: str) -> SessionSteps | None: ...

    async def get_trajectory(
        self, job_id: str, session_id: str, step_id: str
    ) -> StepTrajectory | None: ...


class WTDataPlatformRepository:
    """Normalizes the public ``wt-data-platform-sdk`` query surface.

    A deployment factory returns the authenticated SDK client. The client must
    expose the four query methods used here; every call receives an exact
    ``job_id`` filter.
    """

    REQUIRED_METHODS = (
        "list_sessions",
        "get_session_result",
        "list_steps",
        "get_step_trajectory",
    )

    def __init__(self, client: Any) -> None:
        self._client = client

    async def preflight(self) -> None:
        missing = [name for name in self.REQUIRED_METHODS if not hasattr(self._client, name)]
        if missing:
            raise DataPlatformError(
                f"data-platform SDK client is missing methods: {', '.join(missing)}"
            )
        preflight = getattr(self._client, "preflight", None)
        if preflight is not None:
            await _maybe_await(preflight())

    async def list_session_ids(self, job_id: str) -> tuple[str, ...]:
        try:
            rows = await _maybe_await(self._client.list_sessions(job_id=job_id))
            if not isinstance(rows, (list, tuple)):
                raise TypeError("list_sessions must return a list")
            values: list[str] = []
            for row in rows:
                value = row if isinstance(row, str) else _field(row, "session_id")
                if isinstance(value, str) and value and value not in values:
                    values.append(value)
            return tuple(values)
        except Exception as exc:
            if isinstance(exc, DataPlatformError):
                raise
            raise DataPlatformError("failed to query sessions") from exc

    async def get_result(self, job_id: str, session_id: str) -> SessionResult | None:
        try:
            row = await _maybe_await(
                self._client.get_session_result(job_id=job_id, session_id=session_id)
            )
            if row is None:
                return None
            returned_session = _field(row, "session_id")
            if returned_session != session_id:
                raise ValueError("SDK returned a result for a different session")
            status = ResultStatus(str(_field(row, "result_status")))
            raw_score = _field(row, "score")
            if isinstance(raw_score, bool):
                raise TypeError("score must be a number or null")
            score = None if raw_score is None else float(raw_score)
            if score is not None and not math.isfinite(score):
                raise ValueError("score must be finite")
            completed_at = _datetime_or_none(_field(row, "completed_at"))
            if status == ResultStatus.SUCCEEDED and (score is None or completed_at is None):
                raise ValueError("a succeeded result requires score and completed_at")
            if status != ResultStatus.SUCCEEDED and score is not None:
                raise ValueError("only a succeeded result may contain a score")
            return SessionResult(session_id, status, score, completed_at)
        except Exception as exc:
            if isinstance(exc, DataPlatformError):
                raise
            raise DataPlatformError("failed to query session result") from exc

    async def get_steps(self, job_id: str, session_id: str) -> SessionSteps | None:
        try:
            response = await _maybe_await(
                self._client.list_steps(job_id=job_id, session_id=session_id)
            )
            if response is None:
                return None
            rows = _field(response, "steps")
            sealed = _field(response, "sealed")
            if not isinstance(rows, (list, tuple)) or not isinstance(sealed, bool):
                raise TypeError("invalid list_steps response")
            steps = tuple(
                StepIndex(
                    step_id=_required_string(row, "step_id"),
                    sequence_no=_required_positive_int(row, "sequence_no"),
                )
                for row in rows
            )
            steps = tuple(sorted(steps, key=lambda item: item.sequence_no))
            if len({item.step_id for item in steps}) != len(steps):
                raise ValueError("step IDs must be unique")
            if tuple(item.sequence_no for item in steps) != tuple(
                range(1, len(steps) + 1)
            ):
                raise ValueError("step sequence numbers must be contiguous")
            return SessionSteps(session_id, len(steps), sealed, steps)
        except Exception as exc:
            if isinstance(exc, DataPlatformError):
                raise
            raise DataPlatformError("failed to query session steps") from exc

    async def get_trajectory(
        self, job_id: str, session_id: str, step_id: str
    ) -> StepTrajectory | None:
        try:
            row = await _maybe_await(
                self._client.get_step_trajectory(
                    job_id=job_id, session_id=session_id, step_id=step_id
                )
            )
            if row is None:
                return None
            if _field(row, "session_id") != session_id or _field(row, "step_id") != step_id:
                raise ValueError("SDK returned a trajectory outside the requested scope")
            raw_trajectory = _field(row, "trajectory")
            if not isinstance(raw_trajectory, dict):
                raise TypeError("trajectory must be an object")
            trajectory = {}
            for name in ("model_input", "model_output", "action", "observation"):
                value = raw_trajectory.get(name)
                if value is not None and not isinstance(value, dict):
                    raise TypeError(f"trajectory.{name} must be an object or null")
                if value is not None:
                    trajectory[name] = value
            sequence_no = _required_positive_int(row, "sequence_no")
            return StepTrajectory(
                session_id=session_id,
                step_id=step_id,
                sequence_no=sequence_no,
                started_at=_datetime_or_none(_field(row, "started_at")),
                finished_at=_datetime_or_none(_field(row, "finished_at")),
                trajectory=trajectory,
            )
        except Exception as exc:
            if isinstance(exc, DataPlatformError):
                raise
            raise DataPlatformError("failed to query step trajectory") from exc


def load_sdk_repository(factory_path: str) -> WTDataPlatformRepository:
    module_name, separator, attribute_name = factory_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise DataPlatformError("data-platform factory must use module:callable syntax")
    try:
        factory = getattr(importlib.import_module(module_name), attribute_name)
        client = factory()
    except Exception as exc:
        raise DataPlatformError("unable to initialize data-platform SDK") from exc
    return WTDataPlatformRepository(client)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _datetime_or_none(value: Any) -> datetime | None:
    if value is None:
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return parsed
    raise TypeError("timestamp must be datetime, RFC3339 string, or null")


def _required_string(value: Any, name: str) -> str:
    item = _field(value, name)
    if not isinstance(item, str) or not item.strip():
        raise TypeError(f"{name} must be a non-empty string")
    return item


def _required_positive_int(value: Any, name: str) -> int:
    item = _field(value, name)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise TypeError(f"{name} must be a positive integer")
    return item
