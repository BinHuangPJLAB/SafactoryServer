# SafactoryServer env

该目录是 SafactoryServer 自己维护的环境信息源，直接沿用 Safactory 仓库的 `env/`
目录结构。

每个环境通常包含：

- agent config，例如 `harbor_config.rjob.yaml`；
- start config，例如 `harbor_start.rjob.yaml`；
- `datasets/*.jsonl`；
- runner、rule evaluator 和 runner library；
- 构建环境镜像所需的 Dockerfile（如有）。

真实模式通过 `examples/real/initialization.yaml` 的 `catalog.environment_root` 指向本目录，
并由 `examples/real/ranges.yaml` 选择每个 Range 对应的 agent config、dataset、start
config，以及可选的 launcher RJob config。创建 Job 时，Server 不会直接修改这里的文件，
而是校验后为 Job 生成独立的运行时 env 快照，并在快照中重写 dataset 路径和 results mount。

Harbor Vulhub Claude/Kimi 全量任务使用
`range_harbor_vulhub_claude_kimi_all_001`。该映射还会发布
`harbor_vulhub_rjob_config.yaml`，使 launcher 使用 Harbor 已验证的下游 RJob 并发与命名配置。

`runtime/`、`results/`、`__pycache__/` 是本地运行产物，不属于环境定义，默认不会进入
Git 或 Server Docker 镜像。
