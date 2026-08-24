# Safactory Job Server

Safactory Job API 同时支持两个显式部署模式：

- `mock`：本地联调用 Phase 1 fixture，不访问外部依赖；
- `real`：Phase 2 持久化并编排 Gateway/Safactory 两个顶层 RJob，运行数据只通过
  `wt-data-platform-sdk` 查询，依赖异常时不会回退 Mock。

## 本地启动

需要 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
SAFACTORY_MODE=mock .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Phase 1 的 Job 保存在进程内存中，因此服务必须以单 worker 运行，重启后已有 Job 会丢失。

## Phase 2 真实模式

真实模式直接调用 `brainpp.rjob`，复用了 `create_test_rjob.py` 和
`create_safactory_rjob.py` 已验证的创建逻辑。收到 `POST /v1/jobs` 后，后台编排器按顺序：

1. 校验并发布该 Job 的 env 快照与 results 目录；
2. 使用 `server:safactory003` 创建 daemon Gateway RJob；
3. 从 RJob `replicaStatus`（必要时从 bootstrap 日志）取得 IP，并检查 `/readyz`；
4. 把 `http://<IP>:8000/v1/sessions` 传给 `launcher.py --mode rjob`，创建 Safactory RJob。

DB、RJob 连接、env/results 共享盘映射、Gateway 配置及 launcher 参数都集中在
[`examples/real/initialization.yaml`](examples/real/initialization.yaml)。Server 自己维护的环境
目录位于 [`env/`](env/README.md)，直接沿用 Safactory 的 `env/` 结构。
完整字段和部署检查见 [Phase 2 部署说明](docs/PHASE2_DEPLOYMENT.md)。

```bash
set -a
. ./.env.real
set +a
.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000
```

真实模式在 startup 检查 YAML、环境目录、共享存储、Gateway 配置、RJob 和数据平台 SDK；
任一不可用都会拒绝启动。Job 状态、Gateway IP 和两个 RJob 名称保存在 Control DB 中，
重启后后台 orchestrator 会继续对账原工作负载。

## Docker 构建

仓库使用单一 `Dockerfile`。默认构建 `runtime-root` target，以兼容部署平台启动前
需要写入系统目录的 worker-init：

```bash
docker build -t safactory-job-server .
```

不需要 worker-init 的环境可以构建最小权限运行版本：

```bash
docker build --target runtime-nonroot -t safactory-job-server:nonroot .
```

## HTTP 认证

所有 HTTP 请求都必须提供受信任 API Key：

```http
Authorization: Bearer safactory-local-development-key
```

认证配置默认位于 `src/server/auth/trusted_api_keys.yaml`，格式如下：

```yaml
schema_version: "1.0"
users:
  - username: safactory-local
    api_key: safactory-local-development-key
```

部署时应替换示例凭据，并通过 `SAFACTORY_AUTH_CONFIG_PATH` 指向部署系统挂载的
YAML 文件。Bearer token 匹配 YAML 中的 `api_key`；对应 `username` 只用于请求上下文
和访问日志。配置不可读、字段非法、用户名重复或 API Key 重复时，服务会拒绝启动。
请求缺少 Bearer token 或 token 不匹配时统一返回 `403 FORBIDDEN`。

通过认证的请求会向 stdout 输出访问日志，其中包含 `username`、客户端 `client_ip`、
请求方法、路径、状态码、耗时和 `request_id`，但不会记录 API Key。IP 使用服务端连接
看到的客户端地址；若经反向代理部署，应由运行环境正确配置可信代理头。

## 测试

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests
```

## 联调

- 可直接导入 `examples/mock_api.http`；
- 服务启动后可运行 `scripts/mock_happy_path.sh`；
- Mock 字段和 API 字段的关系见 `docs/MOCK_DATA_MAPPING.md`。

默认 fixture 位于 `src/server/fixtures/mock/v1/scenarios.yaml`。设置
`SAFACTORY_FIXTURE_PATH` 可以加载另一个同版本 fixture。Fixture 不可读或校验失败时，
模型和创建接口返回 `503 DEPENDENCY_UNAVAILABLE`，不会使用其他数据兜底。
