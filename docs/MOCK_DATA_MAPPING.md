# Mock 数据与 API 字段映射

Phase 1 使用 `src/server/fixtures/mock/v1/scenarios.yaml`。Fixture 只描述可复现的场景，
对外 ID 和时间由每次 `POST /v1/jobs` 物化生成。

| Fixture | API | 规则 |
|---|---|---|
| `models[].model_id/name` | `GET /v1/models` | 只返回 `available=true` 的条目；`internal` 永不公开。 |
| `ranges[]` | `POST /v1/jobs` | 校验存在性、可用性和 `supported_models`。 |
| `ranges[].scenario_id` | 内部 Job binding | 仅用于选择场景，不出现在响应中。 |
| `sessions[].visible_after_ms` | Session 列表 | 相对 Job 创建时间到达阈值后追加 Session。 |
| `result.running_after_ms` | `result_status` | 阈值前为 `pending`，之后为 `running`。 |
| `result.completed_after_ms/score` | result 和 Job 状态 | 完成后返回 score；全部 Session 完成后 Job 为 `succeeded`。 |
| `steps[].visible_after_ms` | `step_count/steps` | Step 到达阈值后按 fixture 顺序出现。 |
| `steps[].duration_ms` | trajectory 时间 | `finished_at` 为可见时间，`started_at` 向前减 duration。 |
| `steps[].trajectory` | `trajectory` | 只允许文档定义的四个归一化区块。 |

生成的 Session ID 在 Job 内保持不变，生成的 Step ID 在 Session 内保持不变。
所有查询先校验 `job_id`，再校验 Session 和 Step 归属；同一 fixture 创建的多个 Job 也不会共享 ID。

