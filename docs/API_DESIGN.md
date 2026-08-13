# Safactory Job Server API 设计文档

| 属性 | 内容 |
|---|---|
| API 名称 | Safactory Job API |
| API 版本 | v1 |
| 文档版本 | v1.1 |
| 更新日期 | 2026-08-13 |
| Base Path | `/v1` |

## 1. 设计范围

本 API 面向异步 Safactory Job，覆盖以下基础能力：

- 创建 Job；
- 列出和查询 Job；
- 查询 Job 下的 Task；
- 返回 Job 运行产生的数据；
- 查询 trajectory 和 artifact；
- 暂停 Job；
- 恢复 Job；
- 取消 Job；
- 删除 Job。

本文将需求中的“暂定 Job”解释为“暂停 Job”。暂停采用协作式 drain pause，定义见本文第 8 节。

## 2. 通用约定

### 2.1 协议和编码

- HTTPS；
- 请求和普通响应使用 `application/json; charset=utf-8`；
- 时间使用 UTC RFC 3339，例如 `2026-08-12T08:30:00Z`；
- ID 为不透明字符串，调用方不得解析其内部结构；
- 所有列表采用 cursor pagination；
- 字段默认使用 `snake_case`。

### 2.2 请求追踪

客户端可传：

```http
X-Request-ID: req_client_123
```

如果未传，服务端生成 request ID。所有响应返回：

```http
X-Request-ID: req_01K...
```

错误响应体也包含该 ID。

### 2.3 幂等

创建 Job 必须传：

```http
Idempotency-Key: 0f935f4e-8be0-42d7-b4fb-e5e7b080bbd3
```

规则：

- 幂等范围为当前 API 服务；
- 相同 key 和相同规范化请求返回同一 Job；
- 相同 key 和不同请求返回 `409 IDEMPOTENCY_KEY_REUSED`；
- key 建议至少保存 24 小时；
- pause、resume、cancel 和 delete 依据资源当前状态天然幂等，不要求 key。

### 2.4 成功响应

单资源响应直接返回资源对象，不增加 `data` 包装：

```json
{
  "job_id": "job_01K...",
  "status": "queued"
}
```

列表响应格式：

```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

### 2.5 错误响应

统一格式：

```json
{
  "error": {
    "code": "JOB_STATE_CONFLICT",
    "message": "A succeeded job cannot be paused.",
    "details": {
      "job_id": "job_01K...",
      "current_status": "succeeded",
      "allowed_statuses": ["queued", "preparing", "running"]
    },
    "retryable": false
  },
  "request_id": "req_01K..."
}
```

`message` 用于人类排障，客户端逻辑必须依赖稳定的 `code`。

### 2.6 常用 HTTP 状态码

| 状态码 | 使用场景 |
|---:|---|
| 200 | 查询成功；幂等控制操作已经达到目标状态。 |
| 202 | 创建或控制请求已接受，状态仍在异步变化。 |
| 204 | 删除已完成或资源已经处于逻辑删除状态。 |
| 400 | JSON、字段格式或查询参数错误。 |
| 404 | Job/Task/artifact 不存在或已逻辑删除。 |
| 409 | 状态冲突或幂等键冲突。 |
| 413 | 请求或 inline task 数据过大。 |
| 422 | 请求结构合法但不符合 profile/task schema。 |
| 429 | 配额或频率限制。 |
| 500 | 未分类的服务端错误。 |
| 503 | Scheduler、DB 或依赖暂不可用。 |

## 3. 资源模型

### 3.1 JobResource

```json
{
  "job_id": "job_01K2XYZ...",
  "name": "cybergym-smoke",
  "profile": {
    "id": "cybergym",
    "version": "2026-08-01"
  },
  "runtime": "docker",
  "model": "glm-route",
  "status": "running",
  "phase": "episode_execution",
  "status_reason": null,
  "progress": {
    "total": 10,
    "pending": 4,
    "provisioning": 1,
    "running": 2,
    "evaluating": 0,
    "succeeded": 2,
    "failed": 1,
    "timed_out": 0,
    "cancelled": 0,
    "completed": 3,
    "percent": 30.0
  },
  "control": {
    "pause_requested": false,
    "cancel_requested": false,
    "delete_requested": false,
    "can_pause": true,
    "can_resume": false,
    "can_cancel": true,
    "can_delete": false
  },
  "execution": {
    "max_steps": 100,
    "timeout_seconds": 14400,
    "task_timeout_seconds": 1800,
    "pool_size": 4,
    "max_workers": 4,
    "evaluation_enabled": true
  },
  "labels": {
    "source": "evaluation-platform"
  },
  "error": null,
  "created_at": "2026-08-12T08:00:00Z",
  "started_at": "2026-08-12T08:00:05Z",
  "updated_at": "2026-08-12T08:02:14Z",
  "finished_at": null,
  "links": {
    "self": "/v1/jobs/job_01K2XYZ...",
    "tasks": "/v1/jobs/job_01K2XYZ.../tasks",
    "result": "/v1/jobs/job_01K2XYZ.../result",
    "artifacts": "/v1/jobs/job_01K2XYZ.../artifacts"
  }
}
```

字段说明：

- `progress.total` 在 Task 展开完成前可以为 `null`；
- `progress.percent = completed / total * 100`，`total` 未知时为 `null`；
- `completed` 是 succeeded、failed、timed_out、cancelled 之和；
- `error` 只表示 Job 级错误；Task 失败见 TaskResource；
- `control.can_*` 是服务端根据当前状态计算的提示，最终仍以控制接口响应为准。

### 3.2 TaskResource

```json
{
  "task_id": "task_01K2...",
  "job_id": "job_01K2XYZ...",
  "external_task_id": "arvo:10400",
  "session_id": "0402501d-e68c-429b-ac1c-3c2bd8fd715f",
  "attempt_id": "attempt_01K2...",
  "attempt_no": 1,
  "status": "succeeded",
  "phase": "completed",
  "step_count": 12,
  "reward": 8.5,
  "terminated": true,
  "truncated": false,
  "error": null,
  "created_at": "2026-08-12T08:00:01Z",
  "started_at": "2026-08-12T08:00:08Z",
  "finished_at": "2026-08-12T08:04:10Z",
  "links": {
    "result": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../result",
    "trajectory": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../trajectory",
    "artifacts": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../artifacts"
  }
}
```

### 3.3 ErrorResource

```json
{
  "code": "RUNNER_TIMEOUT",
  "message": "Task execution exceeded 1800 seconds.",
  "phase": "episode_execution",
  "retryable": true,
  "details": {
    "timeout_layer": "docker_exec"
  }
}
```

`details` 必须经过允许列表过滤，不能包含堆栈、密钥或宿主机路径。

## 4. 创建 Job

### `POST /v1/jobs`

异步创建 Job。

#### Headers

```http
Content-Type: application/json
Idempotency-Key: 0f935f4e-8be0-42d7-b4fb-e5e7b080bbd3
```

#### Request

```json
{
  "name": "cybergym-smoke",
  "profile": {
    "id": "cybergym",
    "version": "2026-08-01"
  },
  "runtime": "docker",
  "model": "glm-route",
  "task_source": {
    "type": "profile_dataset",
    "task_ids": ["arvo:10400", "arvo:10401"]
  },
  "parameters": {
    "agent_type": "opencode"
  },
  "sampling": {
    "temperature": 0.3
  },
  "execution": {
    "max_steps": 100,
    "timeout_seconds": 14400,
    "task_timeout_seconds": 1800,
    "pool_size": 2,
    "max_workers": 2,
    "evaluation_enabled": true
  },
  "labels": {
    "source": "evaluation-platform",
    "batch": "nightly-20260812"
  }
}
```

#### 字段规则

| 字段 | 必需 | 规则 |
|---|---:|---|
| `name` | 否 | 1～128 字符，仅用于展示。 |
| `profile.id` | 是 | 必须是服务端已注册且可用的 profile。 |
| `profile.version` | 否 | 省略时解析当前默认版本，创建后固定。 |
| `runtime` | 是 | `docker`、`rjob`、`sandbox`，且必须被 profile 允许。 |
| `model` | 是 | Gateway route key，不允许 URL。 |
| `task_source` | 是 | 见下方 task source。 |
| `parameters` | 否 | 必须符合 profile 的公开参数 schema。 |
| `sampling.temperature` | 否 | 默认由 profile 决定，必须在 profile 允许范围内。 |
| `execution.max_steps` | 否 | 正整数，不能超过 profile 上限。 |
| `execution.timeout_seconds` | 否 | Job 总超时。 |
| `execution.task_timeout_seconds` | 否 | 单 Task 超时。 |
| `execution.pool_size` | 否 | 默认 1，不能超过服务端或 profile 限额。 |
| `execution.max_workers` | 否 | 不得大于 `pool_size`。 |
| `execution.evaluation_enabled` | 否 | 默认由 profile 决定。 |
| `labels` | 否 | 最多 20 个键值，键和值长度受限。 |

#### Task source：profile dataset

```json
{
  "type": "profile_dataset",
  "task_ids": ["case-001", "case-002"]
}
```

- `task_ids` 为空或省略表示使用 profile 默认 dataset 的全部 Task；
- 服务端必须验证指定 ID 存在；
- 单个 Job 的 Task 数不能超过限制。

#### Task source：inline

```json
{
  "type": "inline",
  "items": [
    {
      "external_task_id": "client-case-001",
      "input": {
        "prompt": "Write one short greeting."
      }
    }
  ]
}
```

- 只有 profile 明确允许时才接受 inline；
- `input` 必须符合 profile task schema；
- `external_task_id` 在当前 Job 内必须唯一；
- inline 内容会被固化为 Job 配置快照。

#### Response

```http
HTTP/1.1 202 Accepted
Location: /v1/jobs/job_01K2XYZ...
Retry-After: 2
```

```json
{
  "job_id": "job_01K2XYZ...",
  "status": "queued",
  "phase": "validating_request",
  "created_at": "2026-08-12T08:00:00Z",
  "links": {
    "self": "/v1/jobs/job_01K2XYZ...",
    "tasks": "/v1/jobs/job_01K2XYZ.../tasks",
    "result": "/v1/jobs/job_01K2XYZ.../result"
  }
}
```

#### 创建时同步校验与异步校验

同步校验失败不创建 Job：

- JSON/schema；
- profile 存在性和可用状态；
- runtime 和显式参数范围；
- 服务端基础配额；
- idempotency key。

异步校验失败会创建 Job 后转入 `failed`：

- profile dataset 实际展开；
- Gateway ready 和 route；
- trajectory storage 一致性；
- runtime 镜像或远端环境可用性。

## 5. 列出 Job

### `GET /v1/jobs`

#### Query

| 参数 | 默认 | 说明 |
|---|---:|---|
| `status` | 无 | 可重复，例如 `status=running&status=paused`。 |
| `profile_id` | 无 | 按 profile 过滤。 |
| `label` | 无 | `key:value`，可重复。 |
| `created_after` | 无 | RFC 3339。 |
| `created_before` | 无 | RFC 3339。 |
| `limit` | 50 | 1～100。 |
| `cursor` | 无 | 上一页返回的不透明 cursor。 |

#### Response

```json
{
  "items": [
    {
      "job_id": "job_01K2XYZ...",
      "name": "cybergym-smoke",
      "profile": {"id": "cybergym", "version": "2026-08-01"},
      "runtime": "docker",
      "status": "running",
      "phase": "episode_execution",
      "progress": {
        "total": 10,
        "completed": 3,
        "succeeded": 2,
        "failed": 1,
        "percent": 30.0
      },
      "created_at": "2026-08-12T08:00:00Z",
      "updated_at": "2026-08-12T08:02:14Z"
    }
  ],
  "next_cursor": "eyJjcmVhdGVkX2F0Ijo...",
  "has_more": true
}
```

逻辑删除的 Job 默认不返回。查询已删除资源不属于基础 API。

## 6. 查询 Job 运行状态

### `GET /v1/jobs/{job_id}`

返回完整 JobResource。

#### Response

```http
HTTP/1.1 200 OK
ETag: "job-version-17"
```

```json
{
  "job_id": "job_01K2XYZ...",
  "name": "cybergym-smoke",
  "profile": {"id": "cybergym", "version": "2026-08-01"},
  "runtime": "docker",
  "model": "glm-route",
  "status": "pausing",
  "phase": "episode_execution",
  "status_reason": "Waiting for 2 active tasks to finish.",
  "progress": {
    "total": 10,
    "pending": 4,
    "provisioning": 0,
    "running": 2,
    "evaluating": 0,
    "succeeded": 3,
    "failed": 1,
    "timed_out": 0,
    "cancelled": 0,
    "completed": 4,
    "percent": 40.0
  },
  "control": {
    "pause_requested": true,
    "cancel_requested": false,
    "delete_requested": false,
    "can_pause": false,
    "can_resume": false,
    "can_cancel": true,
    "can_delete": false
  },
  "execution": {
    "max_steps": 100,
    "timeout_seconds": 14400,
    "task_timeout_seconds": 1800,
    "pool_size": 2,
    "max_workers": 2,
    "evaluation_enabled": true
  },
  "labels": {"source": "evaluation-platform"},
  "error": null,
  "created_at": "2026-08-12T08:00:00Z",
  "started_at": "2026-08-12T08:00:05Z",
  "updated_at": "2026-08-12T08:02:14Z",
  "finished_at": null,
  "links": {
    "self": "/v1/jobs/job_01K2XYZ...",
    "tasks": "/v1/jobs/job_01K2XYZ.../tasks",
    "result": "/v1/jobs/job_01K2XYZ.../result",
    "artifacts": "/v1/jobs/job_01K2XYZ.../artifacts"
  }
}
```

支持条件查询：

```http
If-None-Match: "job-version-17"
```

未变化时可返回 `304 Not Modified`，用于降低轮询成本。

建议轮询策略：

- `queued/preparing`：2～5 秒；
- `running/pausing/cancelling`：3～10 秒；
- `paused`：仅在用户操作后查询；
- 终态：停止轮询。

## 7. 查询 Job Task

### `GET /v1/jobs/{job_id}/tasks`

#### Query

| 参数 | 默认 | 说明 |
|---|---:|---|
| `status` | 无 | Task 状态，可重复。 |
| `external_task_id` | 无 | 精确匹配。 |
| `limit` | 50 | 1～100。 |
| `cursor` | 无 | 不透明 cursor。 |

#### Response

```json
{
  "items": [
    {
      "task_id": "task_01K2...",
      "job_id": "job_01K2XYZ...",
      "external_task_id": "arvo:10400",
      "session_id": "0402501d-e68c-429b-ac1c-3c2bd8fd715f",
      "attempt_id": "attempt_01K2...",
      "attempt_no": 1,
      "status": "succeeded",
      "phase": "completed",
      "step_count": 12,
      "reward": 8.5,
      "terminated": true,
      "truncated": false,
      "error": null,
      "created_at": "2026-08-12T08:00:01Z",
      "started_at": "2026-08-12T08:00:08Z",
      "finished_at": "2026-08-12T08:04:10Z",
      "links": {
        "result": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../result",
        "trajectory": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../trajectory",
        "artifacts": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../artifacts"
      }
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

### `GET /v1/jobs/{job_id}/tasks/{task_id}`

返回单个完整 TaskResource，用于查询该 Task 当前状态。`task_id` 必须属于路径中的 `job_id`，否则统一返回 `404 TASK_NOT_FOUND`，避免跨 Job 枚举资源。

```json
{
  "task_id": "task_01K2...",
  "job_id": "job_01K2XYZ...",
  "external_task_id": "arvo:10400",
  "session_id": "0402501d-e68c-429b-ac1c-3c2bd8fd715f",
  "attempt_id": "attempt_01K2...",
  "attempt_no": 1,
  "status": "running",
  "phase": "episode_execution",
  "step_count": 8,
  "reward": null,
  "terminated": false,
  "truncated": false,
  "error": null,
  "created_at": "2026-08-12T08:00:01Z",
  "started_at": "2026-08-12T08:00:08Z",
  "finished_at": null,
  "links": {
    "result": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../result",
    "trajectory": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../trajectory",
    "artifacts": "/v1/jobs/job_01K2XYZ.../tasks/task_01K2.../artifacts"
  }
}
```

## 8. 暂停和恢复 Job

### 8.1 暂停

### `POST /v1/jobs/{job_id}/pause`

#### Request

```json
{
  "reason": "maintenance window"
}
```

`reason` 可选，最大 500 字符，写入审计事件。

#### 状态语义

| 当前状态 | 行为 | 响应 |
|---|---|---:|
| `queued` | 立即置为 `paused` | 200 |
| `preparing` | 置为 `pausing`，在安全点停止 | 202 |
| `running` | 置为 `pausing`，不再领取新 Task | 202 |
| `pausing` | 保持当前状态 | 200 |
| `paused` | 已达到目标状态 | 200 |
| 终态或 `cancelling` | 不允许暂停 | 409 |

#### Response：异步暂停

```http
HTTP/1.1 202 Accepted
Location: /v1/jobs/job_01K2XYZ...
Retry-After: 2
```

```json
{
  "job_id": "job_01K2XYZ...",
  "status": "pausing",
  "control": {
    "pause_requested": true
  },
  "active_task_count": 2,
  "message": "No new tasks will start. The job will become paused after active tasks finish.",
  "updated_at": "2026-08-12T08:05:00Z"
}
```

暂停不是对当前 Task 的 checkpoint：

- 正在执行的 Task 不会被冻结；
- 单个长任务 Job 可能在该 Task 完成后直接进入完成态；
- 客户端必须等待查询结果出现 `status=paused`，不能将 202 视为已经暂停；
- 达到 Job 总超时时，暂停中的 Job仍可被系统取消；
- 最大暂停时长由服务端策略决定。

### 8.2 恢复

### `POST /v1/jobs/{job_id}/resume`

#### Request

请求体可以省略，或传审计原因：

```json
{
  "reason": "maintenance completed"
}
```

#### 状态语义

| 当前状态 | 行为 | 响应 |
|---|---|---:|
| `paused` | 清除 pause flag，由保留的原 worker 进入 `running` | 202 |
| `queued/preparing/running` 且无 pause flag | 已处于运行方向 | 200 |
| `pausing` | 等待暂停完成后再恢复 | 409 |
| 终态或 `cancelling` | 不允许恢复 | 409 |

#### Response

```http
HTTP/1.1 202 Accepted
Location: /v1/jobs/job_01K2XYZ...
```

```json
{
  "job_id": "job_01K2XYZ...",
  "status": "running",
  "control": {
    "pause_requested": false
  },
  "remaining_task_count": 4,
  "updated_at": "2026-08-12T09:00:00Z"
}
```

恢复必须只调度 `pending` Task。已经终态的 Task 及其 `session_id` 不得重建或重跑。

MVP 在暂停期间保留原 Job worker、执行上下文和 runtime lease，并持续 heartbeat。暂停中的 Job 仍占用并发配额；达到服务端配置的最大暂停时长后自动取消。MVP 不支持释放 worker 后在另一 worker 上恢复，因此暂停期间发生 `WORKER_LOST` 时 Job 进入 `failed`。

## 9. 取消 Job

### `POST /v1/jobs/{job_id}/cancel`

#### Request

```json
{
  "reason": "no longer needed"
}
```

#### 状态语义

| 当前状态 | 行为 | 响应 |
|---|---|---:|
| `queued` | 未开始 Task 标记 cancelled，Job 进入 cancelled | 200 |
| `paused` | pending Task 标记 cancelled，Job 进入 cancelled | 200 |
| `preparing/running/pausing` | 进入 cancelling 并异步清理 | 202 |
| `cancelling` | 保持当前状态 | 200 |
| `cancelled` | 已达到目标状态 | 200 |
| `succeeded/completed_with_failures/failed` | 已结束，返回冲突 | 409 |

#### Response

```http
HTTP/1.1 202 Accepted
Location: /v1/jobs/job_01K2XYZ...
Retry-After: 2
```

```json
{
  "job_id": "job_01K2XYZ...",
  "status": "cancelling",
  "control": {
    "cancel_requested": true
  },
  "message": "Cancellation was accepted and runtime cleanup is in progress.",
  "updated_at": "2026-08-12T09:10:00Z"
}
```

已完成 Task 的结果在 Job 被删除前仍可查询。未开始 Task 进入 `cancelled`；正在执行的 Task 根据优雅终止结果进入 `cancelled`、`failed` 或保留已经完成的状态。

## 10. 返回 Job 运行产生的数据

### 10.1 Job 聚合结果

### `GET /v1/jobs/{job_id}/result`

该接口可在 Job 运行中调用，返回当前已持久化的数据。

#### Query

| 参数 | 默认 | 说明 |
|---|---:|---|
| `limit` | 50 | 本页 Task result 数，1～100。 |
| `cursor` | 无 | Task result cursor。 |
| `status` | 无 | 只返回指定 Task 状态，可重复。 |
| `include_input` | false | 是否返回脱敏后的 Task 输入。 |

#### Response：运行中部分结果

```json
{
  "job_id": "job_01K2XYZ...",
  "job_status": "running",
  "partial": true,
  "summary": {
    "total": 10,
    "completed": 4,
    "succeeded": 3,
    "failed": 1,
    "timed_out": 0,
    "cancelled": 0,
    "average_reward": 6.375
  },
  "items": [
    {
      "task_id": "task_01K2...",
      "external_task_id": "arvo:10400",
      "session_id": "0402501d-e68c-429b-ac1c-3c2bd8fd715f",
      "status": "succeeded",
      "start_result": {
        "status": "succeeded",
        "total_reward": 0.0,
        "step_count": 12,
        "terminated": true,
        "truncated": false,
        "error_text": null,
        "metrics": {
          "bench": "cybergym",
          "task_id": "arvo:10400",
          "duration_ms": 79291.638
        }
      },
      "evaluation": {
        "status": "succeeded",
        "normalized_score_10": 8.5,
        "raw_score": 0.85,
        "reason": "all required checks passed"
      },
      "output": {
        "final_response": "...",
        "token_usage": {
          "prompt_tokens": 1200,
          "completion_tokens": 380,
          "total_tokens": 1580
        },
        "trajectory_sealed": true
      },
      "artifacts": [
        {
          "artifact_id": "artifact_01K2...",
          "type": "benchmark_result",
          "filename": "result.json",
          "content_type": "application/json",
          "size_bytes": 4096,
          "sha256": "b4f5...",
          "download_url": "/v1/artifacts/artifact_01K2.../content"
        }
      ],
      "error": null,
      "started_at": "2026-08-12T08:00:08Z",
      "finished_at": "2026-08-12T08:04:10Z"
    }
  ],
  "next_cursor": "eyJ0YXNrX2lkIjo...",
  "has_more": true,
  "generated_at": "2026-08-12T08:05:00Z"
}
```

#### 结果规则

- `partial=true`：Job 非终态，结果之后仍可能增加；
- `partial=false`：Job 已终态，本次读取基于最终 Task 集合；
- `average_reward` 默认按拥有最终 evaluation reward 的已完成 Task 计算；
- `start_result.metrics` 必须经过 profile 允许列表和敏感信息过滤；
- final response 过大时可以截断，并返回独立 artifact；
- artifact 内容不内联；
- 结果不存在但 Job 存在时，返回 200 和空 `items`，不返回 404；
- Job 级基础设施错误通过 JobResource 的 `error` 返回。

### 10.2 单 Task 结果

### `GET /v1/jobs/{job_id}/tasks/{task_id}/result`

返回与聚合结果 `items[]` 相同的完整 Task result。

Task 尚未产生结果时：

```http
HTTP/1.1 409 Conflict
```

```json
{
  "error": {
    "code": "TASK_RESULT_NOT_READY",
    "message": "The task has not produced a result yet.",
    "details": {
      "task_id": "task_01K2...",
      "status": "running"
    },
    "retryable": true
  },
  "request_id": "req_01K..."
}
```

终态失败 Task 仍然返回 200，其 result 中包含 `status` 和 `error`。HTTP 500 不用于表达 Task 自身执行失败。

### 10.3 Trajectory

### `GET /v1/jobs/{job_id}/tasks/{task_id}/trajectory`

#### Query

| 参数 | 默认 | 说明 |
|---|---:|---|
| `view` | `normalized` | v1 仅支持 `normalized`。 |
| `limit` | 100 | 轨迹 row/step 数。 |
| `cursor` | 无 | cursor。 |

#### Response

```json
{
  "job_id": "job_01K2XYZ...",
  "task_id": "task_01K2...",
  "session_id": "0402501d-e68c-429b-ac1c-3c2bd8fd715f",
  "sealed": true,
  "final_response": "...",
  "token_usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 380,
    "total_tokens": 1580
  },
  "steps": [],
  "warnings": [],
  "next_cursor": null,
  "has_more": false
}
```

默认 normalized view 必须过滤 Gateway close、evaluation summary 等非轨迹行，并执行敏感字段脱敏。

## 11. Artifact API

### 11.1 列出 Job artifact

### `GET /v1/jobs/{job_id}/artifacts`

可选 query：`task_id`、`type`、`limit`、`cursor`。

```json
{
  "items": [
    {
      "artifact_id": "artifact_01K2...",
      "job_id": "job_01K2XYZ...",
      "task_id": "task_01K2...",
      "type": "benchmark_result",
      "filename": "result.json",
      "content_type": "application/json",
      "size_bytes": 4096,
      "sha256": "b4f5...",
      "created_at": "2026-08-12T08:04:00Z",
      "expires_at": "2026-09-11T08:04:00Z",
      "download_url": "/v1/artifacts/artifact_01K2.../content"
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

### 11.2 列出 Task artifact

### `GET /v1/jobs/{job_id}/tasks/{task_id}/artifacts`

返回格式同 Job artifact 列表，但只包含该 Task。

### 11.3 下载 artifact

### `GET /v1/artifacts/{artifact_id}/content`

实现方式二选一：

1. 小文件由 API 流式返回；
2. 对象存储文件返回 `302 Found` 到短时有效的签名 URL。

必须支持：

- `Content-Type`、`Content-Length`；
- 安全的 `Content-Disposition`；
- checksum；
- 可选 HTTP Range；
- 禁止由用户输入拼接本地路径。

artifact 已过期或随 Job 删除后返回 404。

## 12. 删除 Job

### `DELETE /v1/jobs/{job_id}`

#### Query

| 参数 | 默认 | 说明 |
|---|---:|---|
| `force` | false | 活跃 Job 是否先取消再删除。 |

#### 默认行为

| 当前状态 | `force=false` | `force=true` |
|---|---|---|
| `succeeded/completed_with_failures/failed/cancelled` | 立即软删除，204 | 立即软删除，204 |
| `queued/paused` | 409 | 取消并删除，202 或 204 |
| `preparing/running/pausing/cancelling` | 409 | 设置 cancel/delete flag，202 |
| 已软删除 | 204 | 204 |

#### 活跃 Job 默认删除失败

```http
HTTP/1.1 409 Conflict
```

```json
{
  "error": {
    "code": "JOB_ACTIVE",
    "message": "An active job must be cancelled before deletion, or delete must use force=true.",
    "details": {
      "job_id": "job_01K2XYZ...",
      "status": "running"
    },
    "retryable": false
  },
  "request_id": "req_01K..."
}
```

#### 强制异步删除

```http
DELETE /v1/jobs/job_01K2XYZ...?force=true
```

```http
HTTP/1.1 202 Accepted
```

```json
{
  "job_id": "job_01K2XYZ...",
  "status": "cancelling",
  "delete_requested": true,
  "message": "The job will be hidden after cancellation and logical deletion complete."
}
```

#### 终态软删除

```http
HTTP/1.1 204 No Content
```

删除后：

- `GET /v1/jobs/{job_id}` 返回 404；
- artifact 下载返回 404 或在后台清理完成前保持不可见；
- control record、trajectory 和 artifact 由后台 retention worker 物理清理；
- 审计事件保留；
- DELETE 必须幂等。

## 13. 状态转换和并发控制

### 13.1 允许的 Job 转换

| From | To |
|---|---|
| `queued` | `preparing`, `paused`, `cancelled` |
| `preparing` | `running`, `pausing`, `cancelling`, `failed` |
| `running` | `pausing`, `cancelling`, `succeeded`, `completed_with_failures`, `failed` |
| `pausing` | `paused`, `cancelling`, `succeeded`, `completed_with_failures`, `failed` |
| `paused` | `running`, `cancelled` |
| `cancelling` | `cancelled`, `failed` |

终态不可逆。删除是独立的 `deleted_at/delete_requested` 维度，不应将 `deleted` 混入执行状态机。

### 13.2 控制操作原子性

pause、resume、cancel 和 delete 必须：

1. 在事务中读取 Job 当前版本；
2. 校验状态转换；
3. 更新 control flag、状态、version 和审计事件；
4. 提交后通知 Scheduler/worker；
5. 依靠 worker fencing token 防止失效 worker 覆盖新状态。

客户端重复操作时，接口应返回当前目标状态，而不是重复触发执行副作用。

## 14. 稳定错误码

同步 API 错误：

| Error code | HTTP | Retryable | 说明 |
|---|---:|---:|---|
| `INVALID_REQUEST` | 400 | 否 | 请求格式错误或缺少必需 header。 |
| `PROFILE_NOT_FOUND` | 404 | 否 | Profile 不存在或未启用。 |
| `JOB_NOT_FOUND` | 404 | 否 | Job 不存在或已删除。 |
| `TASK_NOT_FOUND` | 404 | 否 | Task 不存在。 |
| `ARTIFACT_NOT_FOUND` | 404 | 否 | Artifact 不存在或已过期。 |
| `IDEMPOTENCY_KEY_REUSED` | 409 | 否 | 相同 key 配合不同请求。 |
| `JOB_STATE_CONFLICT` | 409 | 否 | 当前状态不允许该操作。 |
| `JOB_ACTIVE` | 409 | 否 | 活跃 Job 不能默认删除。 |
| `TASK_RESULT_NOT_READY` | 409 | 是 | Task 结果尚未产生。 |
| `PROFILE_VALIDATION_FAILED` | 422 | 否 | 参数或 Task 不符合 profile schema。 |
| `TASK_LIMIT_EXCEEDED` | 422 | 否 | Task 数超过限制。 |
| `QUOTA_EXCEEDED` | 429 | 是 | 并发或资源配额不足。 |
| `DEPENDENCY_UNAVAILABLE` | 503 | 是 | DB、Scheduler 等同步依赖不可用。 |
| `INTERNAL_ERROR` | 500 | 是 | 未分类错误。 |

异步执行错误写入 JobResource 或 TaskResource 的 `error` 字段：

| Error code | 位置 | Retryable | 说明 |
|---|---|---:|---|
| `GATEWAY_UNAVAILABLE` | Job | 是 | Gateway 不可用。 |
| `MODEL_ROUTE_NOT_FOUND` | Job | 否 | Gateway route 不存在。 |
| `STORAGE_MISMATCH` | Job | 否 | Gateway 和 runner storage 不一致。 |
| `RUNTIME_PROVISION_FAILED` | Job/Task | 是 | runtime 创建失败。 |
| `RUNNER_FAILED` | Task | 视情况 | runner 异常。 |
| `RUNNER_TIMEOUT` | Task | 是 | runner 超时。 |
| `RESULT_INVALID` | Task | 否 | `SimulationStartResult` 非法。 |
| `EVALUATION_FAILED` | Task | 视情况 | evaluation 失败。 |
| `ARTIFACT_COLLECTION_FAILED` | Task | 是 | artifact 收集失败。 |
| `WORKER_LOST` | Job | 是 | worker heartbeat 超时，包括暂停期间失联。 |

Job/Task 执行失败通常通过资源的 `error` 字段表达，而不是将结果查询接口返回为 HTTP 500。

## 15. Safactory 适配契约

为了实现上述 API，Safactory Adapter 需要提供三类边界。

### 15.1 RunObserver

```python
class RunObserver(Protocol):
    async def on_job_phase_changed(self, event): ...
    async def on_task_discovered(self, event): ...
    async def on_runtime_allocated(self, event): ...
    async def on_task_started(self, event): ...
    async def on_rollout_completed(self, event): ...
    async def on_evaluation_completed(self, event): ...
    async def on_task_completed(self, event): ...
    async def on_job_completed(self, event): ...
```

要求：

- 默认 no-op，保持原 CLI 行为；
- 事件必须包含 `job_id`、`task_id/env_id/session_id` 和 attempt 信息；
- 同一事件可以被重复投递，control repository 必须幂等；
- observer 写入失败不能悄悄丢失最终结果。

### 15.2 RunControl

```python
class RunControl(Protocol):
    async def before_acquire_next_task(self, job_id: str): ...
    async def is_pause_requested(self, job_id: str) -> bool: ...
    async def is_cancel_requested(self, job_id: str) -> bool: ...
```

Worker 必须在领取新 Task 前经过 control gate。暂停时原 worker 保持 lease 和 heartbeat，并在 gate 等待恢复；取消时停止领取并触发 shutdown。活跃 Task 在 drain pause 中继续执行。

### 15.3 ResultSink

ResultSink 必须持久化：

- 原始和规范化 `SimulationStartResult`；
- evaluation；
- trajectory summary；
- artifact manifest；
- Task 最终状态和错误；
- Job summary。

不能仅依赖 runner “可能存在”的 `safactory_result.json`。artifact 文件是结果来源之一，但不是控制面最终状态的唯一来源。

## 16. 推荐的最小端点集合

| Method | Path | 用途 |
|---|---|---|
| POST | `/v1/jobs` | 创建 Job |
| GET | `/v1/jobs` | 列出 Job |
| GET | `/v1/jobs/{job_id}` | 查询 Job 状态 |
| GET | `/v1/jobs/{job_id}/tasks` | 查询 Task |
| GET | `/v1/jobs/{job_id}/tasks/{task_id}` | 查询单 Task 状态 |
| GET | `/v1/jobs/{job_id}/result` | 返回 Job 运行数据 |
| GET | `/v1/jobs/{job_id}/tasks/{task_id}/result` | 返回单 Task 结果 |
| GET | `/v1/jobs/{job_id}/tasks/{task_id}/trajectory` | 返回 Task 轨迹 |
| GET | `/v1/jobs/{job_id}/artifacts` | 列出 Job artifact |
| GET | `/v1/jobs/{job_id}/tasks/{task_id}/artifacts` | 列出 Task artifact |
| GET | `/v1/artifacts/{artifact_id}/content` | 下载 artifact |
| POST | `/v1/jobs/{job_id}/pause` | 暂停 Job |
| POST | `/v1/jobs/{job_id}/resume` | 恢复 Job |
| POST | `/v1/jobs/{job_id}/cancel` | 取消 Job |
| DELETE | `/v1/jobs/{job_id}` | 删除 Job |

## 17. 暂不纳入 v1 的扩展端点

- `POST /v1/jobs/{job_id}/tasks/{task_id}/retry`；
- `GET /v1/jobs/{job_id}/events`；
- `GET /v1/jobs/{job_id}/logs`；
- WebSocket/SSE 状态推送；
- 完成 webhook；
- 管理员物理 purge；
- Job clone/rerun；
- Profile 管理 API。

这些扩展不应改变 v1 Job/Task/Result 资源结构。
