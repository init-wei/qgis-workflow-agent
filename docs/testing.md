# 测试与验收

默认 CI 离线，不读取真实 API key、不要求桌面 QGIS，不假造外部调用。不同层次分别报告：依赖安装、Python 导入、离线单测、数据库、协议连通、真实模型、真实 GIS 产物、审核恢复、留出评测。

| 命令 | 证明的范围 |
| --- | --- |
| `./scripts/dev.sh check` | 编译、现有 Ruff、当前文档链接、离线 pytest、Vue/TS 类型检查和前端构建 |
| `./scripts/dev.sh typecheck` | Vue/TS 静态类型；CI 的必过门禁 |
| `./scripts/dev.sh test-db` | 项目专用 PostgreSQL 的数据库边界测试，包含执行声明和恢复事实比较 |
| `uv run --locked pytest -o addopts= -q tests/test_execution_persistence.py` | 生产 SQL 在 PostgreSQL 临时表中验证 claim、孤儿执行查询与暂停事实匹配；需先初始化 schema，不修改已有任务或 checkpoint |
| `uv run --locked pytest -o addopts= -q tests/test_vector_inspection.py` | 系统 Python/GDAL 只读输入检查；需发行版 GDAL，属于 external，不是任务产物正确性验收 |
| `uv run --locked pytest -o addopts= -q tests/test_processing_inspection.py tests/test_artifact_integrity.py` | 隔离系统 QGIS 的参数预检和基本产物完整性；属于 external，不能代替独立空间数值验证 |
| `./scripts/dev.sh health --llm-live` | 配置/数据库/桌面桥接/MCP/真实模型连通；不能证明 GIS 任务正确 |

T00 的类型错误与 advisory 是历史基线，见[当次记录](onboarding/baseline-2026-10-30.md)，不能作为当前门禁豁免。不要删除断言、扩大忽略或将失败悄悄标成通过。原型测试通过也不代表新的审核语义满足 AC。

空间预期来自独立合成数据、已知数值或人工标注，不以同一个算法重跑作唯一 oracle。原始输入 checksum 不变；失败、空结果、结果不明和成功分别验证。审核要覆盖多次拒绝、上限、过期版本、重复恢复和三类暂停点的刷新/重启。

每项 issue 写明命令、输入、模型/锁文件、退出码及证据。留出集和 A0/A1 的口径由[实施计划](implementation-plan.md)拥有。故障样例、重试、缺 usage 不从分母隐藏。运行日志置于忽略目录；需共享时导出脱敏的最小证据。

## 真实加载复验

启动项目专用 PostgreSQL、QGIS、MCP 和后台，运行生产构建后从 9000 进入页面。上传 [合成点数据](../tests/fixtures/gis/phase11_points.geojson)，确认页面列出的文件 ID、CRS、字段和要素数；提交仅加载并指定显示名称的请求。

在待审计划中核对具体调用、文件引用与参数。拒绝并填写新名称后，确认同一 task_id 的新修订使用新名称；批准前 QGIS 图层数保持不变。批准后读回 QGIS 的来源、CRS、字段、属性和坐标，与[样例依据](../tests/fixtures/gis/README.md)核对，确认只新增一个图层。对原始文件及受控上传副本复核 SHA-256。

使用页面中的 task_id 查询 `GET /api/executions/{task_id}`，核对批准计划、实际 attempt 和图层 ID；确认前 confirmed 为 null，提交结果确认后为对应布尔值。加载既有输入不生成新空间文件，其图层引用由步骤结果中的实际 ID 和 source 表达。审核恢复与 Processing 按各自正式切片另行验收。

## 审核恢复与新工程副本复验

保留项目专用数据库和 QGIS，记录初始图层数、原工程及输入校验和。上传同一合成点数据，请求“先加载三点图层，再另存为新的工程副本；保持原工程和输入不变，保存需要独立风险审核”。在计划审核中拒绝两次并填写修改意见，核对同一个 task_id 的修订递增；旧修订的批准请求应返回 409。

分别在计划审核、风险审核、结果确认三个等待点执行以下操作：

1. 刷新页面，重选原会话，核对任务、修订、审核 ID 和具体参数。
2. 使用“稍后处理”关闭展示，切换到另一会话后再切回；旧弹窗不能进入另一会话。
3. 在专用后台终端停止后台，再执行 `./scripts/dev.sh backend`。保持 PostgreSQL、MCP 与 QGIS 运行；从原库恢复原会话后再核对等待点。

`GET /api/state/{thread_id}` 的 pause 表示待作出的决定，continuation 表示已保存决定后可手动继续的工作；响应的展示时间不是审核身份。未作出决定时，直接 resume 返回 409。计划批准后只新增一次图层，独立风险批准前不生成工程文件；风险批准后只保存一次副本。结果确认前 confirmed 仍为 null。每次恢复都应保持已完成步骤的调用次数和产物校验和不变。

在另一独立 QGIS profile 中打开新 `.qgz`，核对原图层来源、CRS、字段、三个要素的属性和坐标；原工程仍保留原内容。memory、临时或网络来源及尚未提交的编辑不在首期工程副本保障范围，保存应在写入前明确拒绝；不要自动提交或回滚用户编辑。

“停止读取”只断开事件订阅。使用“刷新当前状态”取回后台到达的等待点；已批准任务遇到忙碌响应后，应能通过“继续已决定的任务”重试。浏览器注入 HTTP 409 只能证明页面恢复，实际 GIS 互斥须另以两个任务的接口调用和计数验证。

未知写入不得重放。人工核查实际 QGIS、工程、图层及文件后，通过只读实例信息和明确说明记录核查；记录为 reconciled，不当作成功。旧调用仍不能恢复，新任务需要新 ID 和重新审核。已知失败或已核查任务之后的新需求，刷新与会话切换仍应保留旧请求及其实际结论。
