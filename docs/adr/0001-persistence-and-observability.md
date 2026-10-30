---
status: accepted
---

# PostgreSQL 及三种记录的职责

保留 PostgreSQL 作为唯一应用数据库，避免迁到 SQLite 后保留两套存储；沿用 LangGraph PostgreSQL Checkpointer 负责暂停点和恢复状态。将自有 ExecutionTrace 收敛为必要的业务执行事实，按消费点迁移后删除多余模型；它不再复制通用模型与工具 span 系统。

LangSmith Cloud 在真实 QGIS 闭环之后可选接入，默认关闭，负责通用观测与实验分析；不自托管、不接入 Langfuse。平台故障或缺 key 不阻塞核心 GIS 与本地评测，平台记录也不能代替批准、Checkpoint 或空间校验。一次业务任务跨暂停可对应多个平台 trace，通过业务 ID 关联。模型和平台 SDK 已在现有锁文件中，不为接入观测盲目升级框架。

详细边界由[目标架构](../architecture/README.md)拥有，具体 AC 由[V1 规格](../specs/0001-qgis-workflow-agent-v1.md)拥有。
