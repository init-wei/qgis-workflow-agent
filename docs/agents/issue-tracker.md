# Issue tracker：GitHub

正式 tracker 为 origin 对应的 `init-wei/qgis-workflow-agent` GitHub Issues。规格和 ADR 在 Git，任务领取、工作状态、评论和验收证据在 GitHub。PRs as a request surface: no.

优先使用已连接的 GitHub 工具。原生阻塞依赖、标签管理和批量查询可使用已认证的 `gh`；不得输出认证 token。多行正文写临时文件后用 `--body-file`，不拼接 shell 文本。

- 读取：`gh issue view <n> --comments`，并读取 JSON labels、assignees、state。
- 发布：一个切片一个 issue；已发布任务的链接见[任务索引](../tickets/README.md)。不要重复创建。
- 原生阻塞：`POST repos/init-wei/qgis-workflow-agent/issues/<child>/dependencies/blocked_by`，`issue_id` 使用阻塞 issue 的数值数据库 ID。正文的 Blocked by 便于阅读，原生关系为编排依据。
- 领取：确认所有 blockers 已关闭且无人领取，再 assign 当前账号，并评论当前运行 ID、分支和文件范围；同账号多个 agent 用评论声明协调，不把 assignee 当分布式锁。
- 完成：逐条附验收证据和两轴审查结果，再关闭 issue；如 PR 承载最终集成，则合并前保留未完成状态。T00 本地前置配置可以在该分支验证后独立关闭。

已发布本地票据保留范围和 AC，不同步复选框或充当第二进度板。变更验收条件先更新 Git 中的规格，再更新正式 issue；已完成的历史证据不改写。
