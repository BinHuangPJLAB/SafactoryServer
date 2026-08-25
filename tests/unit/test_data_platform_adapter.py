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


class WTGatewayQueryClient:
    """Shape exported by wt-data-platform-sdk v0.4.1."""

    def __init__(self) -> None:
        self.queries = []

    def query_data(self, **kwargs):
        self.queries.append(kwargs)
        if kwargs["columns"] == ["session_id"]:
            return [
                {"session_id": "session_1"},
                {"session_id": "session_1"},
            ]
        return [
            {
                "id": "record_1",
                "job_id": "job_1",
                "session_id": "session_1",
                "step_id": 1,
                "messages": '[{"role":"user","content":"hello"}]',
                "response": '{"content":"working"}',
                "reward": None,
                "is_terminal": False,
                "is_session_completed": False,
                "created_at": 1787360400,
                "meta_json": '{"event_type":"gateway_inference"}',
            },
            {
                "id": "record_2",
                "job_id": "job_1",
                "session_id": "session_1",
                "step_id": 2,
                "messages": '[{"role":"user","content":"done?"}]',
                "response": '{"content":"done"}',
                "reward": 9.25,
                "is_terminal": True,
                "is_session_completed": True,
                "created_at": 1787360523,
                "meta_json": '{"event_type":"gateway_inference"}',
            },
        ]


def test_v041_query_data_client_is_adapted_to_the_server_contract() -> None:
    async def exercise() -> None:
        client = WTGatewayQueryClient()
        repository = WTDataPlatformRepository(client)
        await repository.preflight()

        assert await repository.list_session_ids("job_1") == ("session_1",)
        result = await repository.get_result("job_1", "session_1")
        steps = await repository.get_steps("job_1", "session_1")
        trajectory = await repository.get_trajectory("job_1", "session_1", "2")

        assert result is not None
        assert result.result_status.value == "succeeded"
        assert result.score == 9.25
        assert result.completed_at is not None
        assert steps is not None
        assert steps.sealed is True
        assert [item.step_id for item in steps.steps] == ["1", "2"]
        assert trajectory is not None
        assert trajectory.sequence_no == 2
        assert trajectory.trajectory["model_output"] == {"content": "done"}
        assert all(query["partition"] == "job_1" for query in client.queries)
        assert all("job_id = 'job_1'" in query["filter_query"] for query in client.queries)

    asyncio.run(exercise())
