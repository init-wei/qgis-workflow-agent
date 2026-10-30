# QGIS Workflow Agent 开发指引

## 开始工作

- 处理领域术语时读 [CONTEXT.md](CONTEXT.md)。
- 修改接口、目录职责或运行时装配前读[架构](docs/architecture/README.md)及相关 [ADR](docs/adr/README.md)。目标架构不表示现有代码已实现。
- 开发行为前读 [V1 规格](docs/specs/0001-qgis-workflow-agent-v1.md)、当前 GitHub issue 及全部评论，按[开发规范](docs/development.md)执行。
- 配置环境时读[环境入口](docs/onboarding/README.md)，测试时读[测试规范](docs/testing.md)，改文档时读[文档规范](docs/documentation.md)。
- 自主编排从[任务交接](docs/agents/orchestration.md)进入；每次重新查询 GitHub 的未关闭阻塞项，不凭本地快照判断进度。

## 项目不变量

- 首期本地单用户、一个后台进程、一个正在执行的 GIS 任务、独立 QGIS profile。多会话不等于并发执行。
- 浏览器对话、FastAPI/LangGraph 编排、MCP 桥接、独立 QGIS；普通 Python 用 uv，QGIS 用发行版依赖，不互相安装覆盖。
- PostgreSQL 是唯一应用数据库。Checkpoint 负责恢复；精简业务执行记录负责任务事实；LangSmith Cloud 默认关闭，真实 GIS 闭环后才独立验收。
- 单一类型化 TaskPlan。未批准、拒绝、过期批准或达到重试上限均不能执行；执行修改重新审核。外部写入结果不明时标记需核查，不自动重放。
- 首期有限六类 GIS 任务；没有任意代码、删除原始文件、覆盖原始资产、向量数据库、自动模板发布或多用户调度。
- 原始输入不可覆盖。产物文件与空间校验、工具执行完成、用户结果确认分别记录；Mock、导入和模型连通不等于真实 GIS 验收。
- 密钥只在服务端环境读取。`.env`、`.runtime/`、`shared/`、`.target/`、虚拟环境、数据和日志不提交；不向模型或观测平台上传大型空间文件。
- 保留原仓库和第三方来源；不要补写未经确认的许可证。精确工具链版本由配置/锁文件拥有，升级与业务改动分别验收。
- 子代理按不重叠文件范围分工，用户偏好 gpt-6-luna、max；不可用时报告实际模型。共享接口/锁文件由主代理负责。两轴审查结论要核对证据。
- 提交前运行适用检查并审阅差异。push、merge、部署遵守当前用户明确授权；本轮授权覆盖开发前置分支和任务发布，不授权后续自动合并。

## Agent skills

- ask-matt：已确认的决定直接继承，按规格 → 纵向切片 → 行为测试/实现 → Standards 与 Spec 审查，不重复访谈。
- 审查删除、合并、状态收敛或依赖替换，以及调查简化候选时，读取[简化审查补充规则](docs/agents/simplification-review.md)，由 Standards 轴核对证据。
- GitHub 是正式 issue tracker，见[tracker 规则](docs/agents/issue-tracker.md)；已发布本地票据只作范围参考，不维护第二份进度。
- 标签映射见[triage](docs/agents/triage-labels.md)；单一领域上下文与根级 ADR 见[领域规则](docs/agents/domain.md)。
