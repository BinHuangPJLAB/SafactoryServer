from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import math
from datetime import UTC, datetime
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
    """Normalizes the public ``wt-data-platform-sdk`` v0.4.1 query surface.

    The pinned SDK exports ``wt_sdk.WTGatewayClient`` and queries Landing rows
    through ``query_data``. Legacy four-method clients remain accepted as an
    injection seam for deployments that provide a higher-level SDK facade.
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
        if hasattr(self._client, "query_data"):
            return
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
            if hasattr(self._client, "query_data"):
                rows = await self._query_landing(job_id, columns=["session_id"])
                values: list[str] = []
                for row in rows:
                    value = _field(row, "session_id")
                    if isinstance(value, str) and value and value not in values:
                        values.append(value)
                return tuple(values)
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
            if hasattr(self._client, "query_data"):
                rows = await self._query_landing(job_id, session_id)
                if not rows:
                    return None
                completed = [
                    row
                    for row in rows
                    if _truthy(_field(row, "is_session_completed"))
                ]
                rewarded = [
                    row for row in completed if _field(row, "reward") is not None
                ]
                if rewarded:
                    final = max(rewarded, key=_row_sort_key)
                    score = _finite_float(_field(final, "reward"), "reward")
                    completed_at = _row_datetime(final)
                    if completed_at is None:
                        raise ValueError("a completed Landing row requires created_at")
                    return SessionResult(
                        session_id,
                        ResultStatus.SUCCEEDED,
                        score,
                        completed_at,
                    )
                if completed:
                    final = max(completed, key=_row_sort_key)
                    return SessionResult(
                        session_id,
                        ResultStatus.FAILED,
                        None,
                        _row_datetime(final),
                    )
                return SessionResult(
                    session_id,
                    ResultStatus.RUNNING,
                    None,
                    None,
                )
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
            if hasattr(self._client, "query_data"):
                rows = await self._query_landing(job_id, session_id)
                if not rows:
                    return None
                steps = _trajectory_rows(rows)
                indexes = tuple(
                    StepIndex(str(_field(row, "step_id")), sequence_no)
                    for sequence_no, row in enumerate(steps, start=1)
                )
                sealed = any(_is_sealing_row(row) for row in rows)
                return SessionSteps(session_id, len(indexes), sealed, indexes)
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
            if hasattr(self._client, "query_data"):
                rows = await self._query_landing(job_id, session_id)
                steps = _trajectory_rows(rows)
                for sequence_no, row in enumerate(steps, start=1):
                    if str(_field(row, "step_id")) != step_id:
                        continue
                    timestamp = _row_datetime(row)
                    return StepTrajectory(
                        session_id=session_id,
                        step_id=step_id,
                        sequence_no=sequence_no,
                        started_at=timestamp,
                        finished_at=timestamp,
                        trajectory=_trajectory_payload(row),
                    )
                return None
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

    async def _query_landing(
        self,
        job_id: str,
        session_id: str | None = None,
        *,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        clauses = [f"job_id = '{_escape_sql_literal(job_id)}'"]
        if session_id is not None:
            clauses.append(f"session_id = '{_escape_sql_literal(session_id)}'")
        kwargs = {
            "filter_query": " AND ".join(clauses),
            "limit": None,
            "columns": columns,
            "partition": job_id,
            "order_by": "step_id" if session_id is not None else None,
            "ascending": True,
            "checkout_latest": True,
            "exclude_none": False,
            "deserialize_json": True,
        }
        query = self._client.query_data
        rows = (
            await query(**kwargs)
            if inspect.iscoroutinefunction(query)
            else await asyncio.to_thread(query, **kwargs)
        )
        if not isinstance(rows, (list, tuple)):
            raise TypeError("WTGatewayClient.query_data must return a list")
        if not all(isinstance(row, dict) for row in rows):
            raise TypeError("WTGatewayClient.query_data rows must be objects")
        return list(rows)


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


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _meta_json(value: Any) -> dict[str, Any]:
    parsed = _json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _row_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
    raw_step = _field(row, "step_id")
    try:
        step = int(raw_step)
    except (TypeError, ValueError):
        step = -1
    return (
        step,
        str(_field(row, "created_at") or ""),
        str(_field(row, "id") or ""),
    )


def _is_trajectory_row(row: dict[str, Any]) -> bool:
    metadata = _meta_json(_field(row, "meta_json"))
    event_type = metadata.get("event_type")
    if event_type in {
        "gateway_session_close",
        "episode_summary",
        "evaluation_summary",
    } or metadata.get("synthetic_stop"):
        return False
    if event_type == "gateway_inference":
        try:
            return int(metadata.get("status_code") or 200) < 400
        except (TypeError, ValueError):
            return True
    messages = _json_value(_field(row, "messages"), [])
    return bool(messages or _field(row, "response"))


def _trajectory_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_step: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_trajectory_row(row):
            continue
        step_id = str(_field(row, "step_id"))
        previous = latest_by_step.get(step_id)
        if previous is None or _row_sort_key(row) > _row_sort_key(previous):
            latest_by_step[step_id] = row
    return sorted(latest_by_step.values(), key=_row_sort_key)


def _is_sealing_row(row: dict[str, Any]) -> bool:
    metadata = _meta_json(_field(row, "meta_json"))
    return bool(
        _truthy(_field(row, "is_session_completed"))
        or _truthy(_field(row, "is_terminal"))
        or _truthy(metadata.get("is_session_completed"))
        or metadata.get("event_type")
        in {"gateway_session_close", "episode_summary", "evaluation_summary"}
    )


def _trajectory_payload(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _meta_json(_field(row, "meta_json"))
    messages = _json_value(_field(row, "messages"), [])
    request = metadata.get("request")
    model_input: dict[str, Any] = {}
    if messages:
        model_input["messages"] = messages
    if request is not None:
        model_input["request"] = _json_value(request, request)

    response = _json_value(_field(row, "response"), None)
    if isinstance(response, dict):
        model_output = response
    elif response is None:
        model_output = None
    elif isinstance(response, str):
        model_output = {"content": response}
    else:
        model_output = {"value": response}

    trajectory: dict[str, Any] = {}
    if model_input:
        trajectory["model_input"] = model_input
    if model_output is not None:
        trajectory["model_output"] = model_output
    for name in ("action", "observation"):
        value = metadata.get(name)
        if isinstance(value, dict):
            trajectory[name] = value
    return trajectory


def _datetime_or_none(value: Any) -> datetime | None:
    if value is None:
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Landing ``created_at`` is Unix seconds. Defensive support for
        # millisecond values keeps mixed historical rows readable.
        timestamp = float(value)
        if abs(timestamp) >= 100_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return parsed
    raise TypeError("timestamp must be datetime, RFC3339 string, or null")


def _row_datetime(row: dict[str, Any]) -> datetime | None:
    return _datetime_or_none(_field(row, "created_at"))


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
