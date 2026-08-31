# Safactory Job Server API 设计文档

| 属性 | 内容 |
|---|---|
| API 名称 | Safactory Job API |
| API 版本 | v1 |
| 文档版本 | v2.8 |
| 文档状态 | Frozen |
| 更新日期 | 2026-08-31 |
| Base Path | `/v1` |

本版本是 v1 的冻结契约。既有 Method、Path、请求字段、响应字段、字段类型、状态语义和
错误码不得做不兼容修改；不兼容需求必须发布新的 API 大版本。仅修正文案、补充不改变
调用方行为的说明，不构成协议变更。

## 1. 设计范围

本文只定义基座与调用方完成一次靶场任务所需的最小接口闭环：

1. 查询基座支持的模型；
2. 使用 `model_id` 和 `range_id` 创建 Job；
3. 使用 `job_id` 查询 `session_id` 列表；
4. 使用 `job_id` 和列表中的 `session_id` 查询运行结果（得分）；
5. 使用 `job_id` 和列表中的 `session_id` 查询轨迹 step 数及 `step_id`；
6. 使用 `job_id`、`session_id` 和 `step_id` 查询某一步的具体轨迹。

不在本文中定义 Job 列表、Task、暂停、恢复、取消、删除、Artifact、日志、事件推送和靶场模板管理接口。

### 1.1 核心约束

- 一个 Job 只运行一个靶场；
- 当前靶场为单页面、单拓扑，一个 Job 可对应一个或多个 Session；
- `range_id` 由基座受信任 Range Catalog 提供，其值必须与工程中心保存的靶场模板 ID 对齐；
- 模型信息来自 `initialization.yaml` 中 `gateway.config.llm_routes`；route 名称直接作为 `model_id` 和 `name` 返回；
- `model_id` 必须来自模型查询接口，且创建 Job 时仍处于可用状态；
- ID 均为不透明字符串，调用方不得解析或自行拼接 ID；
- 创建 Job 后的所有查询都必须携带 `job_id`，服务端必须校验 Session、Step 与 Job 的归属关系；
- 所有接口都需要 Bearer API Key；Job 只对创建它的认证用户可见，跨用户查询统一按资源不存在处理；
- Job 异步运行，`session_id` 列表、得分和轨迹均可能暂未生成，调用方应按本文约定轮询。

## 2. 调用流程

```mermaid
sequenceDiagram
    participant Client as 调用方
    participant Base as 基座 API

    Client->>Base: GET /v1/ranges
    Base-->>Client: 可用 range_id 和 description
    Client->>Base: GET /v1/models
    Base-->>Client: 可用 model_id
    Client->>Base: POST /v1/jobs (model_id + range_id)
    Base-->>Client: job_id
    Client->>Base: GET /v1/jobs/sessions?job_id=...
    Base-->>Client: session_ids（未就绪时为空列表）
    loop 遍历 session_ids
        Client->>Base: GET /v1/sessions/result?job_id=...&session_id=...
        Base-->>Client: 运行状态和得分
        Client->>Base: GET /v1/sessions/steps?job_id=...&session_id=...
        Base-->>Client: step_count 和 step_id 列表
        Client->>Base: GET /v1/sessions/steps/trajectory?job_id=...&session_id=...&step_id=...
        Base-->>Client: 指定 step 的具体轨迹
    end
```

接口总览：

| 顺序 | Method | Path | 用途 |
|---:|---|---|---|
| 1 | GET | `/v1/ranges` | 查询可用于创建 Job 的 Range |
| 2 | GET | `/v1/models` | 查询基座支持的模型 |
| 3 | POST | `/v1/jobs` | 选择模型和靶场并创建 Job |
| 4 | GET | `/v1/jobs/sessions` | 使用 query 参数 `job_id` 查询 Session ID 列表 |
| 5 | GET | `/v1/sessions/result` | 使用 query 参数 `job_id`、`session_id` 查询运行结果（得分） |
| 6 | GET | `/v1/sessions/steps` | 使用 query 参数 `job_id`、`session_id` 查询轨迹 step 数和 Step ID |
| 7 | GET | `/v1/sessions/steps/trajectory` | 使用 query 参数 `job_id`、`session_id`、`step_id` 查询某一步具体轨迹 |
| 8 | GET | `/v1/sessions/milestones` | 使用 query 参数 `job_id`、`session_id` 查询环境支持的里程碑进度 |

## 3. 通用约定

### 3.1 协议与数据格式

- 使用 HTTPS；
- 请求和响应使用 `application/json; charset=utf-8`；
- 字段名使用 `snake_case`；
- 时间使用 UTC RFC 3339，例如 `2026-08-17T08:30:00Z`；
- API 路径使用固定资源路径，不得将 `job_id`、`session_id`、`step_id` 等可变参数编码为路径片段；
- `job_id`、`session_id`、`step_id` 统一通过 query 参数传递，其值必须进行 URL 编码；
- 必需的 query 参数缺失或为空时，返回 `400 INVALID_REQUEST`。

### 3.2 错误响应

所有接口使用统一错误结构：

```json
{
  "error": {
    "code": "MODEL_NOT_AVAILABLE",
    "message": "The selected model is not available.",
    "details": {
      "model_id": "kimi-k3"
    },
    "retryable": false
  },
  "request_id": "req_01K..."
}
```

客户端逻辑应依赖稳定的 `error.code`，不得依赖 `message` 文案。

### 3.3 认证与请求追踪

所有请求必须携带：

```http
Authorization: Bearer <api-key>
```

凭据缺失、格式错误或无效时返回 `403 FORBIDDEN`。服务端为每次请求生成不透明的
`request_id`，通过响应头 `X-Request-ID` 返回；错误响应正文中的 `request_id` 必须与响应头一致。

认证用户只能查询自己创建的 Job。对不存在或不属于当前用户的 `job_id`，统一返回
`404 JOB_NOT_FOUND`，不得泄露资源是否属于其他用户。

### 3.4 通用状态码

| HTTP 状态码 | 说明 |
|---:|---|
| 200 | 查询成功 |
| 202 | Job 创建请求已接受 |
| 400 | 请求格式或参数格式错误 |
| 403 | 认证凭据缺失或无效 |
| 404 | 指定资源不存在 |
| 422 | 请求语义无效，或目标环境不支持所请求的能力 |
| 500 | 未分类的服务端错误 |
| 503 | Range Catalog、执行引擎或数据存储暂不可用 |

## 4. 查询基座支持的模型

### `GET /v1/models`

创建 Job 前必须先调用此接口。真实模式直接读取统一初始化 YAML 的
`gateway.config.llm_routes`，每个 route 的键就是可用于新 Job 的模型 ID。

每个模型条目只返回 `model_id` 和 `name`，两者都等于 route 名称。route 的
`base_url`、`api_key` 和其他内部字段不得出现在响应中。

### Response

```http
HTTP/1.1 200 OK
```

```json
{
  "items": [
    {
      "model_id": "kimi-k3",
      "name": "kimi-k3"
    },
    {
      "model_id": "qwen-max",
      "name": "qwen-max"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---:|---|
| `items` | array | 是 | 当前可用于创建 Job 的模型列表 |
| `items[].model_id` | string | 是 | 创建 Job 时使用的模型 ID |
| `items[].name` | string | 是 | 模型展示名称 |

约束：

- `llm_routes` 的 route 名称必须非空且天然唯一；
- 接口返回 `llm_routes` 中的全部 route 名称，不返回 route value；
- Gateway config 缺失、为空或校验失败时，真实模式拒绝启动；
- 模型列表可能发生变化。创建 Job 时，服务端必须从同一 `llm_routes` 再次校验 `model_id`。

## 5. 创建 Job

### `POST /v1/jobs`

使用选中的模型和靶场创建异步 Job。

### Request

```json
{
  "model_id": "kimi-k3",
  "range_id": "range_web_001"
}
```

字段说明：

| 字段 | 类型 | 必需 | 说明 |
|---|---|---:|---|
| `model_id` | string | 是 | 来自 `GET /v1/models` 的模型 ID |
| `range_id` | string | 是 | 基座提供的靶场 ID，必须与工程中心的靶场模板 ID 对齐 |

服务端必须完成以下校验：

- `model_id` 存在且当前可用；
- `range_id` 能在受信任 Range Catalog 中唯一匹配一个可用配置，且该 ID 与工程中心模板对齐；
- 模型和 Range 分别校验，不维护或检查模型与 Range 的组合白名单。

### Response

```http
HTTP/1.1 202 Accepted
Location: /v1/jobs/sessions?job_id=job_01K2XYZ...
```

```json
{
  "job_id": "job_01K2XYZ...",
  "status": "queued",
  "model_id": "kimi-k3",
  "range_id": "range_web_001",
  "created_at": "2026-08-17T08:00:00Z"
}
```

创建响应只保证 Job 已被接受，不保证 Session 已经创建。调用方应使用返回的 `job_id` 查询 `session_id` 列表。

### 主要错误码

| Error code | HTTP | 说明 |
|---|---:|---|
| `MODEL_NOT_FOUND` | 422 | `model_id` 不存在 |
| `MODEL_NOT_AVAILABLE` | 422 | 模型当前不可用于新 Job |
| `RANGE_NOT_FOUND` | 422 | `range_id` 无法匹配受信任 Range Catalog |
| `RANGE_NOT_AVAILABLE` | 422 | 靶场模板当前不可用 |
| `DEPENDENCY_UNAVAILABLE` | 503 | 模型配置、Range Catalog 或执行依赖暂不可用 |

## 6. 使用 Job ID 查询 Session ID 列表

### `GET /v1/jobs/sessions`

查询当前 Job 关联的 Session ID 列表。由于 Job 异步启动，接口可能在 Session 创建前被调用。

### Query parameters

| 参数 | 类型 | 必需 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | Job ID |

请求示例：

```http
GET /v1/jobs/sessions?job_id=job_01K2XYZ... HTTP/1.1
```

### Response：Session ID 列表为空

```http
HTTP/1.1 200 OK
Retry-After: 2
```

```json
{
  "job_id": "job_01K2XYZ...",
  "job_status": "preparing",
  "session_ids": []
}
```

### Response：Session ID 列表非空

```http
HTTP/1.1 200 OK
```

```json
{
  "job_id": "job_01K2XYZ...",
  "job_status": "running",
  "session_ids": [
    "session_01K3ABC...",
    "session_01K3DEF..."
  ]
}
```

字段说明：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | Job ID |
| `job_status` | string | 是 | `queued`、`preparing`、`running`、`succeeded` 或 `failed` |
| `session_ids` | array | 是 | 当前 Job 关联的 Session ID 字符串列表；尚未创建或创建失败时为空列表 |
| `error` | object | 否 | `job_status=failed` 时的失败原因 |

约束：

- `session_ids` 为空且 Job 未失败时，调用方可根据 `Retry-After` 继续轮询；
- 同一个 `job_id` 可返回多个 Session ID，列表中不得包含重复项；
- Job 进入终态前，`session_ids` 可追加新值，但已返回的 Session ID 不得变更或移除；Job 进入终态后，列表不得再变更；
- Job 不存在时返回 `404 JOB_NOT_FOUND`。

## 7. 使用 Session ID 查询结果（得分）

### `GET /v1/sessions/result`

查询指定 Job 下 Session 的运行状态和得分。该接口可以在运行中调用；如果对应环境在
`ranges.yaml` 中声明了 `result_artifact`，还会返回该 JSON 文件的内容。

### Query parameters

| 参数 | 类型 | 必需 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | Job ID |
| `session_id` | string | 是 | Session ID，且必须属于指定 Job |

请求示例：

```http
GET /v1/sessions/result?job_id=job_01K2XYZ...&session_id=session_01K3ABC... HTTP/1.1
```

### Response：结果尚未就绪

```http
HTTP/1.1 200 OK
Retry-After: 2
```

```json
{
  "session_id": "session_01K3ABC...",
  "result_status": "running",
  "score": null,
  "completed_at": null
}
```

### Response：结果已完成（环境未配置结果文件）

```http
HTTP/1.1 200 OK
```

```json
{
  "session_id": "session_01K3ABC...",
  "result_status": "succeeded",
  "score": 8.5,
  "completed_at": "2026-08-17T08:04:10Z"
}
```

### Response：结果已完成（环境配置了结果文件）

例如环境配置了 `result_artifact: runtime-test-result.json`：

```http
HTTP/1.1 200 OK
```

```json
{
  "session_id": "session_01K3ABC...",
  "result_status": "succeeded",
  "score": 8.5,
  "completed_at": "2026-08-17T08:04:10Z",
  "result": {
    "schema_version": "runtime-test-result/v1",
    "e2e_success": true,
    "objective_state": "completed"
  }
}
```

`result` 内部字段由具体环境定义，服务端不改名或展开这些字段。未配置
`result_artifact` 时不返回 `result`。运行中的 Session 尚未生成结果文件时，也暂不返回
`result`，调用方继续根据 `Retry-After` 轮询。

字段说明：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---:|---|
| `session_id` | string | 是 | Session ID |
| `result_status` | string | 是 | `pending`、`running`、`succeeded` 或 `failed` |
| `score` | number/null | 是 | 最终得分；结果未完成或失败时为 `null` |
| `completed_at` | string/null | 是 | 结果完成时间 |
| `result` | object | 否 | `ranges.yaml` 配置的环境结果 JSON，内部结构由环境定义 |
| `error` | object | 否 | `result_status=failed` 时的失败原因 |

### 状态码

| HTTP | Error code | 说明 |
|---:|---|---|
| 200 | — | 返回公共结果；配置的环境结果文件存在时同时返回 `result` |
| 400 | `INVALID_REQUEST` | Query 参数缺失或格式错误 |
| 403 | `FORBIDDEN` | 认证凭据缺失或无效 |
| 404 | `JOB_NOT_FOUND` | Job 不存在 |
| 404 | `SESSION_NOT_FOUND` | Session 不存在或不属于指定 Job |
| 503 | `DEPENDENCY_UNAVAILABLE` | 数据平台或已配置的终态结果文件不可读取、缺失或内容无效 |

结果尚未完成时返回 200 和空得分，调用方可根据 `Retry-After` 继续轮询。

## 8. 使用 Session ID 查询轨迹 step

### `GET /v1/sessions/steps`

在查询具体轨迹前，先调用此接口取得指定 Job 下 Session 的当前 step 数和可用的 `step_id`。

### Query parameters

| 参数 | 类型 | 必需 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | Job ID |
| `session_id` | string | 是 | Session ID，且必须属于指定 Job |

请求示例：

```http
GET /v1/sessions/steps?job_id=job_01K2XYZ...&session_id=session_01K3ABC... HTTP/1.1
```

### Response

```http
HTTP/1.1 200 OK
```

```json
{
  "session_id": "session_01K3ABC...",
  "step_count": 3,
  "sealed": false,
  "steps": [
    {
      "step_id": "step_001",
      "sequence_no": 1
    },
    {
      "step_id": "step_002",
      "sequence_no": 2
    },
    {
      "step_id": "step_003",
      "sequence_no": 3
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---:|---|
| `session_id` | string | 是 | Session ID |
| `step_count` | integer | 是 | 当前已持久化、可查询的 step 数，等于 `steps` 数组长度 |
| `sealed` | boolean | 是 | 轨迹是否已结束且不会再增加 step |
| `steps` | array | 是 | 按 `sequence_no` 升序返回的 Step 索引 |
| `steps[].step_id` | string | 是 | 查询具体轨迹时使用的 Step ID |
| `steps[].sequence_no` | integer | 是 | 从 1 开始的展示顺序 |

约束：

- Session 运行中允许返回 `step_count=0` 和空 `steps`；
- `sealed=false` 表示后续查询可能得到更多 step；
- `sealed=true` 表示轨迹已经完整；
- 已返回的 `step_id` 及其 `sequence_no` 不得变化或被复用；
- Job 不存在时返回 `404 JOB_NOT_FOUND`；Session 不存在或不属于指定 Job 时返回 `404 SESSION_NOT_FOUND`。

## 9. 使用 Session ID 和 Step ID 查询具体轨迹

### `GET /v1/sessions/steps/trajectory`

返回指定 Job 下 Session 中某一步的具体轨迹。`job_id`、`session_id` 和 `step_id` 必须同时参与查询和归属校验。

### Query parameters

| 参数 | 类型 | 必需 | 说明 |
|---|---|---:|---|
| `job_id` | string | 是 | Job ID |
| `session_id` | string | 是 | Session ID，且必须属于指定 Job |
| `step_id` | string | 是 | Step ID，且必须属于指定 Session |

请求示例：

```http
GET /v1/sessions/steps/trajectory?job_id=job_01K2XYZ...&session_id=session_01K3ABC...&step_id=step_002 HTTP/1.1
```

### Response

```http
HTTP/1.1 200 OK
```

```json
{
  "session_id": "session_01K3ABC...",
  "step_id": "step_002",
  "sequence_no": 2,
  "started_at": "2026-08-17T08:01:12Z",
  "finished_at": "2026-08-17T08:01:18Z",
  "trajectory": {
    "model_input": {
      "messages": []
    },
    "model_output": {
      "content": "...",
      "tool_calls": []
    },
    "action": {},
    "observation": {}
  }
}
```

字段说明：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---:|---|
| `session_id` | string | 是 | Session ID |
| `step_id` | string | 是 | Step ID |
| `sequence_no` | integer | 是 | Step 在 Session 中的顺序 |
| `started_at` | string/null | 是 | Step 开始时间 |
| `finished_at` | string/null | 是 | Step 完成时间 |
| `trajectory` | object | 是 | 归一化后的该步轨迹内容 |
| `trajectory.model_input` | object/null | 否 | 本步模型输入 |
| `trajectory.model_output` | object/null | 否 | 本步模型输出 |
| `trajectory.action` | object/null | 否 | 本步执行的动作或工具调用 |
| `trajectory.observation` | object/null | 否 | 动作执行后的环境反馈 |

服务端必须过滤密钥、鉴权信息、宿主机路径等敏感数据。不存在的 `job_id` 返回 `404 JOB_NOT_FOUND`；`session_id` 不存在或不属于指定 Job 时返回 `404 SESSION_NOT_FOUND`；`step_id` 不存在或不属于该 Session 时统一返回 `404 STEP_NOT_FOUND`，不得返回其他 Job 或 Session 的轨迹。

## 10. 查询 Session 里程碑

### `GET /v1/sessions/milestones`

查询指定 Job 下 Session 的最新里程碑进度。服务端每次请求都重新读取当前
`milestones.json`；仅声明支持 milestone 的环境可使用此接口。

```http
GET /v1/sessions/milestones?job_id=job_01K2XYZ...&session_id=session_01K3ABC... HTTP/1.1
```

### Response：里程碑已生成

```http
HTTP/1.1 200 OK
Cache-Control: no-store
```

```json
{
  "job_id": "job_01K2XYZ...",
  "session_id": "session_01K3ABC...",
  "milestone_status": "available",
  "snapshot": {
    "schema_version": "agent-range.milestones/v1",
    "run_id": "run_0123456789abcdef",
    "updated_at": "2026-08-31T06:19:56.392304Z",
    "completed": 2,
    "verified": 0,
    "total": 12,
    "latest_reached": "react-root-shell",
    "next_expected": "dubbo-user-shell",
    "milestones": [
      {
        "ordinal": 0,
        "id": "react-user-shell",
        "service": "react",
        "status": "observed",
        "observed_at": "2026-08-31T06:09:19.642840Z",
        "source": "provider_telemetry",
        "trust_class": "guest-reported"
      }
    ]
  }
}
```

`verified`、`observed_at` 和 `verified_at` 为可选字段；`milestones[].status` 可能为
`pending`、`candidate`、`observed` 或 `verified`。API 按文件原值返回状态，不自动升级。

### Response：里程碑尚未生成

```http
HTTP/1.1 200 OK
Retry-After: 5
Cache-Control: no-store
```

```json
{
  "job_id": "job_01K2XYZ...",
  "session_id": "session_01K3ABC...",
  "milestone_status": "pending",
  "snapshot": null
}
```

### Response：环境不支持 milestone

```http
HTTP/1.1 422 Unprocessable Content
```

```json
{
  "error": {
    "code": "MILESTONES_NOT_SUPPORTED",
    "message": "Milestones are not supported for this environment.",
    "details": {
      "job_id": "job_01K2XYZ...",
      "session_id": "session_01K3ABC..."
    },
    "retryable": false
  },
  "request_id": "req_01K4DEF..."
}
```

### 状态码

| HTTP | Error code/状态 | 说明 |
|---:|---|---|
| 200 | `available` | 返回当前最新的里程碑快照 |
| 200 | `pending` | Session 存在但快照尚未生成；按 `Retry-After` 轮询 |
| 400 | `INVALID_REQUEST` | 请求参数格式错误 |
| 403 | `FORBIDDEN` | 认证凭据缺失或无效 |
| 404 | `JOB_NOT_FOUND` | Job 不存在或无权访问 |
| 404 | `SESSION_NOT_FOUND` | Session 不存在或不属于指定 Job |
| 404 | `MILESTONES_NOT_FOUND` | Job 已结束，但没有生成里程碑快照 |
| 422 | `MILESTONES_NOT_SUPPORTED` | 目标环境不支持 milestone；不可重试 |
| 503 | `MILESTONES_UNAVAILABLE` | 快照暂时不可读取或内容无效；可重试 |

## 11. 查询可用 Range

### `GET /v1/ranges`

返回当前可用于创建 Job 的 `range_id` 及其用途说明。接口无请求参数。

```http
GET /v1/ranges HTTP/1.1
Authorization: Bearer <api-key>
```

```http
HTTP/1.1 200 OK
Cache-Control: no-store
```

```json
[
  {
    "range_id": "range_cyberrange_smoke_001",
    "description": "CyberRange Range 3 冒烟验证，用于快速检查运行链路"
  },
  {
    "range_id": "range_cyberrange_full_001",
    "description": "CyberRange Range 3 至 Range 6 完整攻防评测"
  }
]
```

接口只返回 `available=true` 的 Range，并保持 `ranges.yaml` 中的声明顺序；没有可用
Range 时返回空数组。

### 状态码

| HTTP | Error code | 说明 |
|---:|---|---|
| 200 | — | 成功返回 Range 列表，列表可能为空 |
| 403 | `FORBIDDEN` | 认证凭据缺失或无效 |
| 503 | `DEPENDENCY_UNAVAILABLE` | Range Catalog 不可读取或内容不合法 |
| 500 | `INTERNAL_ERROR` | 未分类的服务端错误 |

## 12. 稳定错误码

| Error code | HTTP | Retryable | 说明 |
|---|---:|---:|---|
| `FORBIDDEN` | 403 | 否 | 认证凭据缺失、格式错误或无效 |
| `INVALID_REQUEST` | 400 | 否 | 请求格式或参数格式错误 |
| `MODEL_NOT_FOUND` | 422 | 否 | 模型不存在 |
| `MODEL_NOT_AVAILABLE` | 422 | 否 | 模型当前不可用 |
| `RANGE_NOT_FOUND` | 422 | 否 | 靶场 ID 无法匹配受信任 Range Catalog |
| `RANGE_NOT_AVAILABLE` | 422 | 视情况 | 靶场模板当前不可用 |
| `JOB_NOT_FOUND` | 404 | 否 | Job 不存在 |
| `SESSION_NOT_FOUND` | 404 | 否 | Session 不存在或不属于指定 Job |
| `STEP_NOT_FOUND` | 404 | 否 | Step 不存在或不属于指定 Session |
| `MILESTONES_NOT_FOUND` | 404 | 否 | Job 已结束但未生成里程碑快照 |
| `MILESTONES_NOT_SUPPORTED` | 422 | 否 | 目标环境不支持 milestone |
| `MILESTONES_UNAVAILABLE` | 503 | 是 | 里程碑快照暂时不可读取或内容无效 |
| `DEPENDENCY_UNAVAILABLE` | 503 | 是 | 模型配置、Range Catalog、执行引擎或存储暂不可用 |
| `INTERNAL_ERROR` | 500 | 是 | 未分类的服务端错误 |

## 13. 闭环验收标准

使用一组有效的 `model_id` 和 `range_id`，调用方必须能够完成以下流程：

1. 从 Range 接口取得 `range_id`；
2. 从模型接口取得 `model_id`；
3. 创建 Job 并取得 `job_id`；
4. 轮询 Job 的 Session 列表接口，取得 `session_ids` 列表；
5. 对列表中的每个 `session_id`，使用 `job_id + session_id` 查询运行结果并在完成后取得得分；
6. 对列表中的每个 `session_id`，使用 `job_id + session_id` 查询 `step_count` 和全部可用 `step_id`；
7. 对每个 `step_id`，使用 `job_id + session_id + step_id` 查询对应的具体轨迹；
8. 对每个 Session，当 `sealed=true` 时，已查询到的 step 数量必须等于 `step_count`，且每个 Step 均可获取唯一的轨迹详情。
