# Safactory Job Server

Safactory Job Server 只运行真实调度链路。收到 `POST /v1/jobs` 后，服务持久化 Job，依次
创建 Gateway RJob、获取并检查 Gateway IP，最后创建 Safactory controller RJob；Session、
result、step 和 trajectory 只通过 `wt-data-platform-sdk` 查询。

## 配置

镜像、DB、RJob 连接、env/results 共享盘映射、完整 Gateway config 和 launcher 参数统一由
[`examples/real/initialization.yaml`](examples/real/initialization.yaml) 管理。环境信息维护在
[`env/`](env/README.md)，range 到 CyberRange case 的映射维护在
[`examples/real/ranges.yaml`](examples/real/ranges.yaml)。

`GET /v1/models` 直接枚举 `gateway.config.llm_routes`，route 名称同时作为响应的
`model_id` 和 `name`。创建 Job 时使用请求传入的 `model_id`，Range 不限制模型。

完整配置字段、Secret 环境变量和启动检查见
[部署说明](docs/PHASE2_DEPLOYMENT.md)，公开协议见 [API 设计](docs/API_DESIGN.md)。

## 启动

需要 Python 3.11 或更高版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
set -a
. ./.env.real
set +a
safactory-job-server
```

安装会按 Safactory 的 Cloud requirements 拉取固定版本
`wt-data-platform-sdk @ git+https://github.com/AI45Lab/wt-data-platform-sdk.git@v0.4.1`；
构建环境需要能够访问该 Git 仓库。

也可以使用 Uvicorn factory 方式启动：

```bash
.venv/bin/uvicorn server.main:create_app --factory --host 0.0.0.0 --port 8000
```

服务启动时检查初始化 YAML、环境目录、共享存储、Gateway 配置、RJob 平台和数据平台 SDK。
任一真实依赖不可用都会拒绝启动。Job 状态、Gateway IP 和两个顶层 RJob ID 保存在 SQLite
Control DB 中，服务重启后编排器会继续对账。

调试时可在启动 Server 前设置 `SAFACTORY_KEEP_RJOBS=true`。此时 Job 进入终态后，Server
不会主动 stop/delete 顶层 Safactory controller 和 Gateway RJob，并会将 Control DB 的
待清理标记正常收口，避免后台重复尝试。默认值为 `false`。episode 子 RJob 仍由对应 start
config 的 `cleanup_on_finish` 和 `keep_failed_jobs` 控制；保留的顶层 RJob 也仍受平台
`rjob.auto_delete_duration` 策略约束。

## 日志

stdout 只输出请求、启动、RJob 状态变化、Job 终态、清理结果和 warning/error；周期性对账、
BrainPP SDK 信息、完整 RJob 状态结构及终态 RJob 输出写入滚动文件。默认文件为 Control DB
同目录下的 `safactory-server.log`，权限为 `0600`，单文件 100 MiB，保留 5 个备份。

可通过以下环境变量覆盖：

- `SAFACTORY_LOG_LEVEL`：stdout 级别，默认 `INFO`；
- `SAFACTORY_LOG_FILE_PATH`：全量日志路径；
- `SAFACTORY_LOG_FILE_LEVEL`：文件日志级别，默认 `DEBUG`；
- `SAFACTORY_LOG_FILE_MAX_BYTES`：单文件轮转大小；
- `SAFACTORY_LOG_FILE_BACKUP_COUNT`：备份数量。

终态 RJob 输出可能包含任务数据，应只向服务运维人员开放日志文件。

## Docker

默认 target 以 root 运行，以兼容平台 worker-init：

```bash
docker build -t safactory-job-server .
```

不需要 worker-init 时可构建最小权限版本：

```bash
docker build --target runtime-nonroot -t safactory-job-server:nonroot .
```

## HTTP 认证

所有请求都必须携带受信任 API Key：

```http
Authorization: Bearer safactory-local-development-key
```

认证配置默认位于 `src/server/auth/trusted_api_keys.yaml`。部署时应替换示例凭据，并通过
`SAFACTORY_AUTH_CONFIG_PATH` 指向挂载的 YAML。请求日志包含 username、client IP、状态码、
耗时和 request ID，但不会记录 API Key。

## 验证

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest
```

契约测试和 E2E 均通过真实的 `RealCatalog + SQLiteControlStore + RealJobOrchestrator` 进入；
仅对外部 RJob 平台和数据平台 SDK 使用测试替身。
