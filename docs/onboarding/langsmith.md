# 可选 LangSmith Cloud

本页拥有本项目的观测配置与复验步骤；职责边界见[架构](../architecture/README.md)，实际接入验收见 [T04](https://github.com/init-wei/qgis-workflow-agent/issues/24)。当前缺少真实 Cloud 凭据，平台关联尚未验收，不能用本地 SDK 测试替代。

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

本次锁定 SDK 的接线使用 project-based 目标。[官方追踪入口](https://docs.langchain.com/langsmith/observability-quickstart)同时介绍新版 agent-based workspace；其 `LANGSMITH_AGENT_ID` / `LANGSMITH_AGENT_ENVIRONMENT` 配置与 project 目标不同。当前安装版本未提供本项目可用的原生 agent 目标接线，不能把两组变量混用，也不能据此宣称新目标已兼容。有实际账号后先核对目标类型；如需适配新的目标方式，单独验证 SDK 与配置，不盲目升级整个框架。

不要开启 `LANGCHAIN_TRACING_V2` 等 SDK 全局自动追踪开关。旧 CLI 也会读取同一 `.env`；全局启用会让这些脚本绕过后台的输入输出过滤。应用开关仅控制后台受保护的业务入口，未给旧 CLI 添加遥测支持。

修改 `.env` 后重启本项目后台；PostgreSQL、MCP 与独立 QGIS 保持各自原有入口。设回 `QGIS_AGENT_LANGSMITH_ENABLED=false` 即停用应用观测，业务查询、产物和审核仍由本地系统提供。

## 实际记录范围

首期隐藏 trace 输入与输出正文，原始模型提示、响应、工具错误文本及空间几何不上传。官方 SDK 支持这些[隐藏与元数据处理方式](https://docs.langchain.com/langsmith/mask-inputs-outputs)；本项目进一步仅保留允许的元数据。节点与模型、MCP 调用、空间验证按业务会话、task、修订和可选 case 关联；同一任务在审核恢复前后可以出现多个 run。

必要元数据用于定位调用与验证，包括工具/算法、非敏感标量参数、完成或失败类别、空间判断、文件大小与 SHA。完整文件和详细业务事实留本地；通过 task ID 查询 `GET /api/executions/{task_id}`，通过 Git 版本与 case 定义复现请求。平台运行正常或评分高不构成批准或产物通过。

模型用量只使用实际返回的 token 字段；未返回就保持未知。没有核实供应商计价和平台费用来源时，金额也保持未知。平台显示与本地实际用量需在真实接入验收时比较，不能把 SDK 连通或缺失值当作零费用。

框架标准化后的用量可能包含补零或推算，不能直接当作供应商逐字段实测。当前锁定版本的流式转换不保留原始用量字段，本项目因此将其用量视为未知；非流式结果仅从原始 `token_usage` 逐字段取值，实际返回的零保留，缺失字段不补零或推算。平台是否自行推算部分字段仍须在真实目标核对；任务成功不代表已获得 token 与费用证据。

## 复验与证据

先在关闭或缺 key 时运行本地门禁，以及真实 GIS 闭环、业务查询和 PostgreSQL 暂停恢复。开启后再在实际目标中分别运行一项成功任务和一项已知失败或结果未知任务，记录平台 run 链接、task ID、审核前后的关联和本地事实。

逐项核对：

1. 图节点、模型、实际 MCP 调用及空间验证可通过业务 ID 找到；输入输出正文、凭据、空间文件及全量几何未进入遥测。
2. usage 与供应商实际返回一致；缺失保持未知，费用来源单独说明。
3. 平台不可用时业务仍可完成，原始业务错误没有被观测处理吞掉；关闭后结果仍可本地查询。
4. 使用正常后台退出，核对 SDK 提交及退出耗时；不要用强杀代替正常提交验收。网络异常时也应有界退出，不能承诺遥测永不丢失。

实际 Cloud 验证前，以上开启状态与退出影响保持未验收。原始脱敏证据放在忽略的 `.target/`，正式结论更新 T04，不在这里同步第二份任务状态。
