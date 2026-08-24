from __future__ import annotations

import asyncio

from server.infrastructure.real.data_platform import WTDataPlatformRepository


class SyncSDKClient:
    def __init__(self) -> None:
        self.filters = []

    def list_sessions(self, **filters):
        self.filters.append(filters)
        return [
            {"job_id": filters["job_id"], "session_id": "session_1"},
            {"job_id": filters["job_id"], "session_id": "session_1"},
        ]

    def get_session_result(self, **filters):
        self.filters.append(filters)
        return {
            "session_id": filters["session_id"],
            "result_status": "succeeded",
            "score": 8.5,
            "completed_at": "2026-08-22T01:00:00Z",
        }

    def list_steps(self, **filters):
        self.filters.append(filters)
        return {
            "sealed": True,
            "steps": [{"step_id": "step_1", "sequence_no": 1}],
        }

    def get_step_trajectory(self, **filters):
        self.filters.append(filters)
        return {
            "session_id": filters["session_id"],
            "step_id": filters["step_id"],
            "sequence_no": 1,
            "started_at": "2026-08-22T01:00:00Z",
            "finished_at": "2026-08-22T01:00:01Z",
            "trajectory": {"model_output": {"content": "ok"}, "ignored": "field"},
        }


def test_sdk_adapter_applies_exact_job_filters_and_normalizes_rows() -> None:
    async def exercise() -> None:
        client = SyncSDKClient()
        repository = WTDataPlatformRepository(client)
        await repository.preflight()

        assert await repository.list_session_ids("job_1") == ("session_1",)
        result = await repository.get_result("job_1", "session_1")
        steps = await repository.get_steps("job_1", "session_1")
        trajectory = await repository.get_trajectory(
            "job_1", "session_1", "step_1"
        )

        assert result is not None and result.score == 8.5
        assert steps is not None and steps.step_count == 1
        assert trajectory is not None
        assert trajectory.trajectory == {"model_output": {"content": "ok"}}
        assert all(filters["job_id"] == "job_1" for filters in client.filters)

    asyncio.run(exercise())
