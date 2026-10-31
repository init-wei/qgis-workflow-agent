# 可选 LangSmith Cloud

本页拥有本项目的观测配置与复验步骤；职责边界见[架构](../architecture/README.md)，实际接入验收见 [T04](https://github.com/init-wei/qgis-workflow-agent/issues/24)。凭据只保留在服务端忽略配置中；平台关联必须以该票的真实 run 和本地任务证据核对，不能用本地 SDK 测试替代。

## 配置与目标

沿用锁文件中的官方 SDK，不另装平台依赖。将配置只写入服务端忽略的 `.env`，不要写入浏览器设置、任务正文或 Git。已有 `.env` 不要整份覆盖。

| 变量 | 本项目用途 |
| --- | --- |
| `QGIS_AGENT_LANGSMITH_ENABLED` | 本应用开关，默认 `false`；明确设为 `true` 且配置非空 key 后，由后台显式启用经过过滤的官方追踪上下文 |
| `LANGSMITH_TRACING` | 保持 `false` 或不设置；全局 SDK 自动追踪不属于本项目的过滤入口 |
| `LANGSMITH_API_KEY` | LangSmith 专用 key，与模型供应商 key 不同；缺失时保持关闭 |
| `LANGSMITH_PROJECT` | project-based 目标名；示例为 `qgis-workflow-agent-v1`，未配置时沿用 SDK 的 `default` |
| `LANGSMITH_ENDPOINT` | 目标区域的 API 地址；未配置使用 `https://api.smith.langchain.com`，按实际账号区域核对 |
| `LANGSMITH_WORKSPACE_ID` | SDK 支持的可选工作区 ID；是否需要取决于实际账号和 key |

本次锁定 SDK 的接线使用 project-based 目标。[官方追踪入口](https://docs.langchain.com/langsmith/observability-quickstart)同时介绍新版 agent-based workspace；其 `LANGSMITH_AGENT_ID` / `LANGSMITH_AGENT_ENVIRONMENT` 配置与 project 目标不同。当前安装版本未提供本项目可用的原生 agent 目标接线，不能把两组变量混用，也不能据此宣称新目标已兼容。配置实际账号时先核对目标类型；如需适配新的目标方式，单独验证 SDK 与配置，不盲目升级整个框架。

不要开启 `LANGCHAIN_TRACING_V2` 等 SDK 全局自动追踪开关。旧 CLI 也会读取同一 `.env`；全局启用会让这些脚本绕过后台的输入输出过滤。应用开关仅控制后台受保护的业务入口，未给旧 CLI 添加遥测支持。

修改 `.env` 后重启本项目后台；PostgreSQL、MCP 与独立 QGIS 保持各自原有入口。设回 `QGIS_AGENT_LANGSMITH_ENABLED=false` 即停用应用观测，业务查询、产物和审核仍由本地系统提供。

## 实际记录范围

首期隐藏 trace 输入与输出正文，原始模型提示、响应、工具错误文本及空间几何不上传。官方 SDK 支持这些[隐藏与元数据处理方式](https://docs.langchain.com/langsmith/mask-inputs-outputs)；本项目进一步仅保留允许的元数据。节点与模型、MCP 调用、空间验证按业务会话、task、修订和可选 case 关联；同一任务在审核恢复前后可以出现多个 run。

必要元数据用于定位调用与验证，包括工具/算法、非敏感标量参数、完成或失败类别、空间判断、文件大小与 SHA。完整文件和详细业务事实留本地；通过 task ID 查询 `GET /api/executions/{task_id}`，通过 Git 版本与 case 定义复现请求。平台运行正常或评分高不构成批准或产物通过。

模型用量只使用实际返回的 token 字段；未返回就保持未知。没有核实供应商计价和平台费用来源时，金额也保持未知。平台显示与本地实际用量需在真实接入验收时比较，不能把 SDK 连通或缺失值当作零费用。

[#30](https://github.com/init-wei/qgis-workflow-agent/issues/30) 的补修后，规划节点可能调用模型；`reflector_node` 仍可作为图节点 span 出现在观测中，但它只投影业务事实，不再调用模型，因此该节点没有模型用量或模型费用。不同版本的图结构与实际调用须分别核对，不能根据节点名称猜测模型调用。

框架标准化后的用量可能包含补零或推算，不能直接当作供应商逐字段实测。过滤入口仅从原始 `token_usage` 逐字段取值，实际返回的零保留，缺失字段不补零或推算。锁定版本的部分流式路径会丢失原始用量；必须逐个模型 run 核对，不能因为一个节点有计数，就认为整项任务用量完整。[T04 当时版本的真实验收](https://github.com/init-wei/qgis-workflow-agent/issues/24#issuecomment-6540416487)中规划 run 带可追溯计数，而当时调用模型的反思 run 缺原始来源、平台显示默认零；后者仍记未知，任务总用量与费用也保持未知。后续取消该模型调用不改变历史数据或补齐历史缺失用量。

平台的 `ls_provider=openai` 可以来自 OpenAI-compatible 适配器标签，不能据此套用 OpenAI 价格。实际供应商、响应模型、调用时间、缓存用量与计价规则需另外核对；平台费用字段为空不代表免费。本地评测若显式采用非流式请求，也必须保留原始逐字段用量及完整性证据，不回填历史流式缺失值。

## 复验与证据

先在关闭或缺 key 时运行本地门禁，以及真实 GIS 闭环、业务查询和 PostgreSQL 暂停恢复。开启后再在实际目标中分别运行一项成功任务和一项已知失败或结果未知任务，记录平台 run 链接、task ID、审核前后的关联和本地事实。

查询时先按 task/session 找到相关 trace，再展开这些 trace 的全部子 run，对照本地 completed attempt 与空间报告；不能只检查带某个 metadata 的部分节点。平台返回的 `ls_run_depth` 单独按父子树深度核对，不把它算成应用新增的业务元数据。输入、输出、events、serialized、attachments 等字段也要检查，只有 metadata 白名单并不足以证明正文未上传。

逐项核对：

1. 图节点、模型、实际 MCP 调用及空间验证可通过业务 ID 找到；输入输出正文、凭据、空间文件及全量几何未进入遥测。
2. usage 与供应商实际返回一致；缺失保持未知，费用来源单独说明。
3. 平台不可用时业务仍可完成，原始业务错误没有被观测处理吞掉；关闭后结果仍可本地查询。
4. 使用正常后台退出，核对退出后实际可查询的完整 run 与退出耗时；不要用强杀代替正常提交验收。后台运行时 SDK 也会异步发送，因此退出后可见只能证明本次正常退出的结果，不能单独证明由退出 flush 发送，更不能承诺遥测永不丢失。
5. 用相同输入、请求与模型分别取开启、关闭、局部不可用的对照，区分提交至计划审核、批准至结果审核与退出时间。人工等待和额外风险审核单独记录。单次样本受供应商延迟影响，只能作为观测样例，不能据差值宣称稳定开销；局部超时注入也不能代替公共平台全面故障测试。
6. 验证关闭或不可用时的平台缺席，使用同次已认证、完整分页的项目查询及开启任务的正向对照；孤立的零查询结果不能证明开关有效。

原始脱敏证据放在忽略的 `.target/`，具体 task/run、对照样本、正常退出测量与限制统一记录在 T04；本页只维护配置和复验方法，不同步第二份任务状态。
