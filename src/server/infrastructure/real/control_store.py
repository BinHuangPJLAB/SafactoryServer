from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.domain.entities import JobStatus


@dataclass(frozen=True, slots=True)
class ControlJob:
    job_id: str
    request_id: str
    owner_username: str
    model_id: str
    model_checksum: str
    model_gateway_json: str
    range_id: str
    status: str
    phase: str
    status_reason: str | None
    binding_json: str | None
    gateway_image: str
    gateway_rjob_id: str | None
    gateway_status: str | None
    gateway_address: str | None
    gateway_port: int | None
    gateway_url: str | None
    gateway_ready_at: str | None
    gateway_exit_code: int | None
    gateway_failure_summary: str | None
    safactory_image: str
    safactory_rjob_id: str | None
    safactory_status: str | None
    safactory_exit_code: int | None
    safactory_failure_summary: str | None
    episode_total: int
    episode_running: int
    episode_succeeded: int
    episode_failed: int
    episode_collected: int
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str
    orchestrator_attempt: int
    version: int
    cleanup_requested: int

    @property
    def terminal(self) -> bool:
        return self.status in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value}


JOB_COLUMNS = {field.name for field in fields(ControlJob)}
MUTABLE_COLUMNS = JOB_COLUMNS - {
    "job_id",
    "request_id",
    "owner_username",
    "model_id",
    "model_checksum",
    "model_gateway_json",
    "range_id",
    "created_at",
    "version",
}


class SQLiteControlStore:
    """Durable Job state and append-only orchestration events."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        owner_username TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        model_checksum TEXT NOT NULL,
                        model_gateway_json TEXT NOT NULL,
                        range_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        status_reason TEXT,
                        binding_json TEXT,
                        gateway_image TEXT NOT NULL,
                        gateway_rjob_id TEXT UNIQUE,
                        gateway_status TEXT,
                        gateway_address TEXT,
                        gateway_port INTEGER,
                        gateway_url TEXT,
                        gateway_ready_at TEXT,
                        gateway_exit_code INTEGER,
                        gateway_failure_summary TEXT,
                        safactory_image TEXT NOT NULL,
                        safactory_rjob_id TEXT UNIQUE,
                        safactory_status TEXT,
                        safactory_exit_code INTEGER,
                        safactory_failure_summary TEXT,
                        episode_total INTEGER NOT NULL DEFAULT 0,
                        episode_running INTEGER NOT NULL DEFAULT 0,
                        episode_succeeded INTEGER NOT NULL DEFAULT 0,
                        episode_failed INTEGER NOT NULL DEFAULT 0,
                        episode_collected INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        updated_at TEXT NOT NULL,
                        orchestrator_attempt INTEGER NOT NULL DEFAULT 0,
                        version INTEGER NOT NULL DEFAULT 1,
                        cleanup_requested INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, updated_at);
                    CREATE TABLE IF NOT EXISTS job_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                    );
                    CREATE INDEX IF NOT EXISTS job_events_job_idx
                        ON job_events(job_id, event_id);
                    """
                )

    async def add(self, job: ControlJob) -> None:
        values = {field.name: getattr(job, field.name) for field in fields(job)}
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        async with self._lock:
            with self._connect() as connection:
                connection.execute(
                    f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",  # noqa: S608
                    tuple(values.values()),
                )

    async def get(self, job_id: str) -> ControlJob | None:
        async with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
        return None if row is None else ControlJob(**dict(row))

    async def list_active(self) -> tuple[ControlJob, ...]:
        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE status NOT IN (?, ?) ORDER BY created_at",
                    (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value),
                ).fetchall()
        return tuple(ControlJob(**dict(row)) for row in rows)

    async def list_cleanup_pending(self) -> tuple[ControlJob, ...]:
        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE cleanup_requested = 1 ORDER BY updated_at"
                ).fetchall()
        return tuple(ControlJob(**dict(row)) for row in rows)

    async def update(self, job_id: str, *, now: datetime, **changes: Any) -> ControlJob:
        unknown = set(changes) - MUTABLE_COLUMNS
        if unknown:
            raise ValueError(f"unsupported Job fields: {sorted(unknown)}")
        async with self._lock:
            with self._connect() as connection:
                current_row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if current_row is None:
                    raise KeyError(job_id)
                current = ControlJob(**dict(current_row))
                requested_status = changes.get("status", current.status)
                if current.terminal and requested_status != current.status:
                    raise ValueError("terminal Job state is immutable")
                changes["updated_at"] = _timestamp(now)
                assignments = ", ".join(f"{column} = ?" for column in changes)
                parameters = [*changes.values(), current.version + 1, job_id, current.version]
                cursor = connection.execute(
                    f"UPDATE jobs SET {assignments}, version = ? "  # noqa: S608
                    "WHERE job_id = ? AND version = ?",
                    parameters,
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("concurrent Job update detected")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
        return ControlJob(**dict(row))

    async def add_event(
        self,
        job_id: str,
        event_type: str,
        phase: str,
        now: datetime,
        payload: dict[str, Any] | None = None,
    ) -> None:
        safe_payload = _sanitize_event_payload(payload or {})
        async with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO job_events "
                    "(job_id, event_type, phase, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        event_type,
                        phase,
                        json.dumps(safe_payload, sort_keys=True),
                        _timestamp(now),
                    ),
                )

    async def events(self, job_id: str) -> tuple[dict[str, Any], ...]:
        async with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT event_type, phase, payload_json, created_at "
                    "FROM job_events WHERE job_id = ? ORDER BY event_id",
                    (job_id,),
                ).fetchall()
        return tuple(
            {
                "event_type": row["event_type"],
                "phase": row["phase"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def new_control_job(
    *,
    job_id: str,
    request_id: str,
    owner_username: str,
    model_id: str,
    model_checksum: str,
    model_gateway_json: str,
    range_id: str,
    gateway_image: str,
    safactory_image: str,
    now: datetime,
) -> ControlJob:
    timestamp = _timestamp(now)
    return ControlJob(
        job_id=job_id,
        request_id=request_id,
        owner_username=owner_username,
        model_id=model_id,
        model_checksum=model_checksum,
        model_gateway_json=model_gateway_json,
        range_id=range_id,
        status=JobStatus.QUEUED.value,
        phase="validating_request",
        status_reason=None,
        binding_json=None,
        gateway_image=gateway_image,
        gateway_rjob_id=None,
        gateway_status=None,
        gateway_address=None,
        gateway_port=None,
        gateway_url=None,
        gateway_ready_at=None,
        gateway_exit_code=None,
        gateway_failure_summary=None,
        safactory_image=safactory_image,
        safactory_rjob_id=None,
        safactory_status=None,
        safactory_exit_code=None,
        safactory_failure_summary=None,
        episode_total=0,
        episode_running=0,
        episode_succeeded=0,
        episode_failed=0,
        episode_collected=0,
        created_at=timestamp,
        started_at=None,
        completed_at=None,
        updated_at=timestamp,
        orchestrator_attempt=0,
        version=1,
        cleanup_requested=0,
    )


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    denied = {"token", "secret", "password", "route", "mount_source"}
    return {key: value for key, value in payload.items() if key.lower() not in denied}
