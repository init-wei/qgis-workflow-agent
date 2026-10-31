# V1 验收证据与可陈述范围

本页是 [V1 规格 AC01–AC16](../specs/0001-qgis-workflow-agent-v1.md) 的证据入口，不是第二份进度板。正式状态、当次命令、版本与证据由各 GitHub issue 拥有。下表引用的是各切片实际执行时的版本，不能自动视为当前版本全部重跑；当前 T08 的[干净重建](clean-rebuild-2026-10-31.md)、浏览器、适用检查与最终 Standards/Spec 审查以 [#28](https://github.com/init-wei/qgis-workflow-agent/issues/28) 的最终记录为准。演示复验步骤见[浏览器演示](v1-demo.md)。

| AC | 已有证据入口及其证明范围 | 最终核对重点 |
| --- | --- | --- |
| AC01 | [环境基线](baseline-2026-10-30.md)记录当次环境；[T01](https://github.com/init-wei/qgis-workflow-agent/issues/21#issuecomment-6526916539) 的无运行目录源码导出只复用既有依赖，不是全新安装。 | [干净重建](clean-rebuild-2026-10-31.md)记录独立安装、新数据库卷与空 QGIS profile、实际版本、构建和单地址页面；首次健康检查 race 与就绪后通过分开。 |
| AC02 | [T01 实际浏览器加载](https://github.com/init-wei/qgis-workflow-agent/issues/21#issuecomment-6526916539)将文件 ID 绑定计划及执行，核对原始和上传 SHA、QGIS 来源及要素。 | 当前版本浏览器复验记录所选文件、调用、实际读回和前后 SHA；失败路径也不能覆盖原文件。 |
| AC03 | [T01](https://github.com/init-wei/qgis-workflow-agent/issues/21#issuecomment-6526916539)的公开接口反例覆盖非法整份计划与零执行；[T03](https://github.com/init-wei/qgis-workflow-agent/issues/23#issuecomment-6529726256)记录真实顺序及输入引用。 | 区分受控 fake 的非法引用、缺参、重复/缺失步骤、未知工具零调用证据，与真实 GIS 执行计数。 |
| AC04 | [T01](https://github.com/init-wei/qgis-workflow-agent/issues/21#issuecomment-6526916539)实际拒绝后同任务修订；[T02](https://github.com/init-wei/qgis-workflow-agent/issues/22#issuecomment-6528355015)两次拒绝、旧批准 409，以及上限和未批准反例。 | 核对具体 task/revision、意见、调用计数；自动尝试上限不授权执行。 |
| AC05 | [T02](https://github.com/init-wei/qgis-workflow-agent/issues/22#issuecomment-6528355015)真实新工程独立风险审核和保存一次；拒绝、过期、重复等由该票公开接口与受控反例补充。 | 计划批准和具体风险调用批准分开，验证重复决定不重复执行；原工程保持不变。 |
| AC06 | [T01 加载](https://github.com/init-wei/qgis-workflow-agent/issues/21#issuecomment-6526916539)、[T03 重投影/缓冲](https://github.com/init-wei/qgis-workflow-agent/issues/23#issuecomment-6529726256)、[T05 裁剪/融合/统计](https://github.com/init-wei/qgis-workflow-agent/issues/25#issuecomment-6532123611)分别提供真实 QGIS、输入、读回与固定数值预期。 | 六类真实样例与各自 CRS、数量、属性、几何、统计及容差一一对应；工具完成不等于空间通过。 |
| AC07 | [T03](https://github.com/init-wei/qgis-workflow-agent/issues/23#issuecomment-6529726256)缓冲消费重投影实际输出并加载；[T05](https://github.com/init-wei/qgis-workflow-agent/issues/25#issuecomment-6532123611)裁剪后融合及面积统计消费前序产物。 | 审核计划、实际步骤输入与已验证前序文件的身份和 SHA 一致。 |
| AC08 | [T02](https://github.com/init-wei/qgis-workflow-agent/issues/22#issuecomment-6528355015)三暂停点实际刷新、重选会话及后台重启；[T07](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)另有真实浏览器专项。 | 三种暂停身份和参数恢复，已完成调用与文件不重放；未知写入先核查。 |
| AC09 | [T02](https://github.com/init-wei/qgis-workflow-agent/issues/22#issuecomment-6528355015)会话隔离与 GIS 互斥反例；[T07 浏览器专项](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)覆盖会话切换及旧流隔离。 | 浏览器注入的忙响应只证明页面恢复；实际互斥须看独立调用计数。 |
| AC10 | [T03](https://github.com/init-wei/qgis-workflow-agent/issues/23#issuecomment-6529726256)保留工具完成及验证失败，[T05](https://github.com/init-wei/qgis-workflow-agent/issues/25#issuecomment-6532123611)含合法空结果，[T02](https://github.com/init-wei/qgis-workflow-agent/issues/22#issuecomment-6528355015)记录未知后核查。 | [T07](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)固定反例假成功 0/7，不能推广到任意输入；确认与工具完成、空间通过分别记录。 |
| AC11 | [T04](https://github.com/init-wei/qgis-workflow-agent/issues/24#issuecomment-6540416487)真实开启、关闭、局部不可用对照及审核恢复；[观测文档](langsmith.md)给出配置和复验。 | 应用开关默认关闭；局部超时注入不代表公共平台全面故障，单次耗时不证明稳定开销。 |
| AC12 | [六类模板审阅](skill-review.md)链接真实来源和维护者对六份 1.0.0 的确认；[T06 开发复验](https://github.com/init-wei/qgis-workflow-agent/issues/26#issuecomment-6533991650)及[人审补充](https://github.com/init-wei/qgis-workflow-agent/issues/26#issuecomment-6539651745)分别记录新文件/参数、类型与审核。 | 实际模板候选不是执行授权，也不证明模型一定采用；历史开发执行树按原记录归属。 |
| AC13 | [T04 实际 Cloud](https://github.com/init-wei/qgis-workflow-agent/issues/24#issuecomment-6540416487)关联成功、真实空间失败及规划失败与本地业务 ID，并检查关闭和局部不可用。 | 私有平台链接需账号权限；未返回的 usage 保持未知。Cloud 副本不能替代 PostgreSQL 决策和业务事实。 |
| AC14 | [T07 原始结果与封存](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)列配置、96 primary、异常专项、浏览器专项、失败/unknown、原始文件 SHA 和平台导入；[复验入口](benchmark.md)给出方法。 | 分母和版本固定，失败、空结果、unknown 和 0 rerun 均保留；[后续 #29](https://github.com/init-wei/qgis-workflow-agent/issues/29#issuecomment-6542289770)不能回填原实验。 |
| AC15 | [T01](https://github.com/init-wei/qgis-workflow-agent/issues/21#issuecomment-6526916539)与 [T07](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)记录当时浏览器、文本和深链接；当次类型与构建结果在各 issue。 | 当前生产构建的 `/`、`/settings`、退役 API JSON 404 和真实加载按 [T08 v2 定义](../../tests/fixtures/gis/t08_browser_routes.json)单独报告，首次 v2 为 2/3；同任务明确纠正与 [#30](https://github.com/init-wei/qgis-workflow-agent/issues/30) 补修后的只读刷新/重启分别见[实测](clean-rebuild-2026-10-31.md)，不能改写原分母。 |
| AC16 | 本页、[演示](v1-demo.md)、[代码规模](../size-estimate.md)及[来源说明](provenance.md)提供入口。 | [#28](https://github.com/init-wei/qgis-workflow-agent/issues/28)记录最终适用检查、真实集成、剩余限制和针对最终差异的 Standards/Spec 两轴；未完成前不宣称整体交付全部通过。 |

## 固定评测怎样解读

[T07](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)冻结 `c2d5a59658fff61b55b6a1f3fb78ffe71231f442`：24 个请求、A0/A1 各两次，共 96 个 primary、0 rerun。35 次完成含 4 次合法空结果；另有 33 次 planning_failed、26 次 unknown_outcome、2 次 invalid_plan。A0 最终完成 16/48、unknown 12；A1 完成 19/48、unknown 14。样本小且未知较多，不能据此声称模板带来稳定、显著或可泛化的提升。

固定异常专项假成功 0/7、未批准执行 0/4，只覆盖各自固定集合。普通任务的未批准执行已观测 0/37，另 59/96 缺少该项度量；不能说 96 次全部已测为零。开发操作者没有进行真实人类改动率观察，故该指标未知。T07 当时的浏览器专项 7/7 保留原定义与原证据；其中旧 Trace Demo 深链接属于历史 UI。当前 v2 是另一个集合，不能替换原七项、原 96、定义或 oracle。历史已运行失败应保留 failed；未运行的退役旧例记 not_run。

[#29 的修复后开发回归](https://github.com/init-wei/qgis-workflow-agent/issues/29#issuecomment-6542289770)在 `054baa4eb12f86adc157d28d2dd18acd97605e50` 上取得 3/3 真实新参数结果：EPSG:32648、135 米、每象限 12 段的重投影/缓冲/加载；按 `district` 融合得到 W/E 并集面积 24/6 m²；对 `[4, NULL, 9]` 做基础与分类统计。两条统计均消费原始输入，基础表 `count=2` 与 `filled=2` 是非空数，`empty=1`，总数 3 来自核验输入或 filled+empty，不能把 `count` 称为总数。三任务分别 3/2/2 次调用、共 5 个 GPKG；oracle、确认和终态前后业务事实与原输入 SHA 有核对。确认由自动开发操作者依据预期作出；此批是已曝光需求的开发回归，不是新盲测，也不改写原 96 次。

## 最终界面与摘要的事实来源

[T08 实测](clean-rebuild-2026-10-31.md)保留首次名称错误及后续驱动/界面失败；同任务 r2 批准后实际只加载一次，确认 true，补修后的只读刷新、重选及后台重启保持业务事实与 QGIS 不变。页面从必要业务记录恢复结果卡；新的完成摘要直接投影计划和尝试、验证、确认，不再让模型自由生成。旧摘要按原版本保留参考；blocked 尝试未发送，实际 MCP 次数由独立传输证明。

## 实际范围与限制

首期为本地单用户、一个后台进程和同一时间一个 GIS 执行；支持选定单图层的 2D GeoJSON/GeoPackage，六类有限任务为加载、重投影、缓冲、裁剪、融合和简单统计，组合限 2–4 个明确步骤。输入、目标 CRS、距离/单位、字段和角色应明确，规划器只支持有限的明确表达；不可靠或冲突时需要澄清。原输入不可覆盖；受控新 `.qgz` 保存需独立风险审核。三个审核暂停点由 PostgreSQL checkpoint 恢复，必要业务执行事实保留在 PostgreSQL；LangSmith Cloud 可选且默认关闭。

本范围不包括大型栅格、SAM、GPU、任意代码或 GIS 算法、多用户调度、可靠运行中取消、外部写入 exactly-once、自动发布模板或向量数据库。写入结果不明须人工核查，不自动重放。固定点投影/缓冲使用数学预期；复杂几何的部分检查可能与 QGIS 共享 OGR/GEOS，不能称所有空间 oracle 都完全独立。上述样例和专项不能保证任意数据或自然语言输入的结果。

## 可用于简历的措辞

可按本人真实参与情况调整为：

> 在既有 QGIS 工作流原型上，主导 V1 需求、架构与验收设计，借助 AI 辅助重构，将浏览器计划审核、PostgreSQL 暂停恢复和受控 QGIS 处理串成可核查的本地流程；为固定合成数据建立空间读回与失败反例，并以版本冻结的任务集记录成功、失败和未知结果。

面试说明可引用 [T07 原始评测](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)及其分母和 unknown，区分后续修复回归、真实人工模板确认与自动开发操作者的结果确认。项目继承原型，不应描述为从零独立编写全部代码；源码行数、测试数量不能当作个人产出或质量证明。实际规模和统计口径见[代码规模](../size-estimate.md)；原始与第三方来源、许可和署名边界见[来源说明](provenance.md)。
