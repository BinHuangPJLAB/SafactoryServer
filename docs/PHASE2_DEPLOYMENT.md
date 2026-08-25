# 真实 RJob 调度部署说明

`POST /v1/jobs` 只负责持久化并返回 `202`，后台编排器实际执行：

```text
生成 Job env 快照和 results 目录
  -> 创建 Gateway daemon RJob
  -> 等待 Running
  -> 从 replicaStatus 或 SAFACTORY_RJOB_IP 日志取得 IP
  -> GET http://<IP>:8000/readyz
  -> 创建 Safactory launcher RJob
  -> launcher 创建/回收 episode RJob 并退出
  -> 清理 Safactory RJob 和 Gateway RJob
```

Gateway 和 Safactory 都使用
`registry.h.pjlab.org.cn/ailab-evobox-evobox_proxy/server:safactory003`。Gateway 的命令、
IP bootstrap、daemon 设置和 Safactory 的 launcher 参数与 Safactory 仓库中
`scripts/create_test_rjob.py`、`scripts/create_safactory_rjob.py` 保持一致。

## 统一 YAML

复制 [`examples/real/initialization.yaml`](../examples/real/initialization.yaml) 作为部署配置，并通过：

```bash
export SAFACTORY_INITIALIZATION_CONFIG_PATH=/absolute/path/initialization.yaml
```

指定它。YAML 是以下信息的唯一配置入口：

- `database`：Server Control DB、`wt-data-platform-sdk` factory 和 Gateway/launcher 的 Cloud DB 环境；
- `rjob`：`brainpp.rjob` cluster entry、namespace、charged group、AK/SK 及生命周期参数；
- `storage.environment`：Server 写入 Job env 快照的本地共享盘路径、对应 RJob source 和 `/app/env` target；
- `storage.results`：results 本地共享盘路径、对应 RJob source 和 `/app/results` target；
- `catalog`：ranges 和 Server 自管环境目录；模型直接来自 `gateway.config.llm_routes`；
- `gateway`：完整 Gateway `config`、生成后的 mount、端口、健康检查、资源/request 和超时；
- `safactory`：launcher、并发、step、episode RJob 默认值、资源和超时。

`${NAME}` 会在 Server 启动时从环境变量展开；变量缺失会直接拒绝启动；
`${NAME:-default}` 可声明默认值。AK/SK、S3 凭据和模型密钥不要写入 Git。

`wt-data-platform-sdk` 与 Safactory 的 `requirements-cloud.txt` 保持一致，固定安装 GitHub
`v0.4.1`。其运行时 import/factory 为 `wt_sdk:WTGatewayClient`，查询通过该版本公开的
`query_data` 接口完成。

不再维护独立的 Gateway 配置文件。`gateway.config` 就是 Gateway 的完整业务配置；Server
在创建 Job 时将它渲染为 `<environment.local_path>/<job_id>/gateway/gateway.yaml`，再通过
对应的 `environment.rjob_source` 只读挂载到 Gateway RJob 的
`gateway.config_mount_dir/gateway.config_filename`。因此 DB、RJob、Gateway 与镜像只有一份
初始化 YAML，不需要额外同步 `gateway.yaml`。

## env 与 results 映射

`local_path` 和 `rjob_source` 必须指向同一份共享存储的两种视图。例如：

```yaml
storage:
  environment:
    local_path: /mnt/gpfs/team/server-env
    rjob_source: gpfs://gpfs1/team/server-env
    mount_path: /app/env
  results:
    local_path: /mnt/gpfs/team/results
    rjob_source: gpfs://gpfs1/team/results
    mount_path: /app/results
```

Server 在 `environment.local_path/<job_id>` 原子发布不可变快照；RJob 使用
`environment.rjob_source/<job_id>:/app/env` 挂载它。每个 Job 的结果目录同理为
`results.rjob_source/<job_id>:/app/results`。start config 内的 result mount 会自动重写，
因此不需要在每个环境 YAML 中重复硬编码 results source。

## Server 自管环境目录

[`env/`](../env/README.md) 是 Server 维护的环境信息源，直接沿用 Safactory 仓库的
`env/` 结构。
每个环境需要包含 agent YAML、dataset JSONL、start RJob YAML，以及 start config 引用但未
内置于环境镜像的 runner/evaluator 文件。`ranges.yaml` 将一个 range 映射到这些文件。

当前已验证的 launcher CLI 每个 Job 只接收一个 `--agent-start-config`，所以一个 range
暂时只允许一个 environment group。创建 Job 时会校验：

- `model_id` 只从请求参数取得并在 `gateway.config.llm_routes` 中校验，Range 不限制模型；
- agent config 的 `environments[]` 与 range 的 `env_name` 一致；
- dataset 非空且每行是 JSON object；
- start config 的 `agent_name`、`runner_entrypoint` 和 results mount 有效；
- environment image、`env_num` 和 `env_params` 有效。

## Gateway 地址与就绪

Gateway 容器启动前先打印 `SAFACTORY_RJOB_IP=<ip>`，再 `exec python -m gateway
--config /app/runtime-config/gateway.yaml`。Server 优先读取 RJob
`job.spec.tasks[*].replicaStatus` 的 Pod IP；SDK 未返回时从日志 marker 回退。只有 RJob 为
`Running`、IP 非 loopback 且 `/readyz` 返回 2xx，才提交 Safactory launcher。

传给 launcher 的地址固定加上 `gateway.sessions_path`，默认是
`http://<ip>:8000/v1/sessions`，不会把仅用于健康检查的根 URL 误传给 episode worker。

## 恢复与清理

Control DB 使用 SQLite WAL，部署时应只有一个 writer。每个顶层 RJob 使用由 `job_id`
派生的稳定名称；创建前会查询同名 RJob，避免 Server 在提交后、写 DB 前崩溃造成重复创建。
Server 重启后按已保存的 RJob 名称继续轮询。终态清理顺序为 Safactory launcher、Gateway；
删除失败会保留清理标记并后台重试。

## 启动检查

服务 startup 会检查：

1. 初始化 YAML 和所有 `${VAR}`；
2. `gateway.config.llm_routes`、ranges 和 env 文件；
3. env staging 与 results 本地共享路径可写；
4. 内联 Gateway config、端口/session path 和 storage type 相互一致；
5. Cloud DB 必需环境完整且 URI 合法；
6. `brainpp.rjob` 可连接；
7. `wt-data-platform-sdk` client 可初始化。

任一项失败都会拒绝启动。建议先运行：

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest
```

再启动服务并调用 `POST /v1/jobs`。
