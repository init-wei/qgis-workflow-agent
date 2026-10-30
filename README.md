# QGIS Workflow Agent

一个将自然语言需求转为可审核 GIS 工作流、由独立 QGIS 执行的本地 Agent 项目。技术栈为 Vue 3/TypeScript、FastAPI、LangGraph/LangChain、MCP、PostgreSQL 与 QGIS。

当前分支 `codex/v1-refactor` 已配置开发基线，业务重构待按 GitHub 切片实施。目标是六类真实 GIS 任务、明确的人工审核、暂停点恢复及独立空间校验；本页不声明这些目标已完成。

- [Agent 开发入口](AGENTS.md)与[自主编排交接](docs/agents/orchestration.md)
- [环境安装与运行](docs/onboarding/README.md)
- [目标架构](docs/architecture/README.md)、[规格](docs/specs/0001-qgis-workflow-agent-v1.md)与[阶段计划](docs/implementation-plan.md)
- [任务索引](docs/tickets/README.md)、[实测基线与已知限制](docs/onboarding/baseline-2026-10-30.md)
- [完整文档](docs/README.md)

PostgreSQL 保留为唯一应用数据库。Checkpoint 管恢复，精简业务执行记录管任务事实；LangSmith Cloud 在真实 QGIS 闭环后作为可选观测与实验分析工具接入，默认关闭。

本地凭据来自忽略的 `.env`。空间数据和运行日志留在受控本地目录，不放入仓库或完整模型上下文。保留上游来源与许可证边界，见[来源说明](docs/onboarding/provenance.md)。
