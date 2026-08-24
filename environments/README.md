# SafactoryServer managed environments

这里是 Job Server 自己维护的环境目录，作用与 Safactory 仓库的 `env/` 相同。

每个环境至少包含：

- agent config（`environments` 根节点）；
- 对应的 `start.rjob.yaml`；
- `datasets/*.jsonl`；
- start config 引用的 runner/evaluator 等文件（如果不在环境镜像内）。

在 `examples/real/ranges.yaml` 中登记环境文件。Job 创建时 Server 会校验这些文件，
为该 Job 生成不可变 env 快照，重写 dataset 和 results mount，然后把快照挂载到
Safactory launcher 的 `/app/env`。不要在这里保存 RJob、模型或数据库明文密钥。
