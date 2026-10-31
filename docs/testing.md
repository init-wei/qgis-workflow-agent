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
| `uv run --locked pytest -o addopts= -q tests/test_spatial_validation.py tests/test_spatial_validation_gdal.py tests/test_spatial_oracle.py` | 空间验证接口、系统 GDAL 读回反例及固定数学预期；不代替真实 MCP 任务 |
| `DATABASE_URL='' PYTHON_DOTENV_DISABLED=1 uv run --locked pytest -o addopts= -q tests/test_t05_fixtures.py tests/test_spatial_validation_t05.py` | 固定矩形数学、输入物化与系统 GIS 的裁剪/融合/统计反例；不读取项目凭据，不代替公开用户路径 |
| `DATABASE_URL='' PYTHON_DOTENV_DISABLED=1 uv run --locked pytest tests/test_skill_registry.py tests/test_t06_workflow.py tests/test_rag_pool_import.py` | 六类候选、类型与占位符、同一审核路径，以及 RAG 导入和调用时连接池的无网络替身验证；不等于人工模板审阅或真实 GIS 复用 |
| `./scripts/dev.sh health --llm-live` | 配置/数据库/桌面桥接/MCP/真实模型连通；不能证明 GIS 任务正确 |

T00 的类型错误与 advisory 是历史基线，见[当次记录](onboarding/baseline-2026-10-30.md)，不能作为当前门禁豁免。不要删除断言、扩大忽略或将失败悄悄标成通过。原型测试通过也不代表新的审核语义满足 AC。

运行离线全库并覆盖 `addopts` 时，必须显式保留 `-m 'not external and not database'`；空 `DATABASE_URL` 不能代替测试筛选。外部检查逐文件选择并说明所需服务。持久化 SQL 的测试 fixture 另行拒绝 libpq 默认连接与非项目端口，避免缺配置时落到其他本地 PostgreSQL。

空间预期来自独立合成数据、已知数值或人工标注，不以同一个算法重跑作唯一 oracle。原始输入 checksum 不变；失败、空结果、结果不明和成功分别验证。审核要覆盖多次拒绝、上限、过期版本、重复恢复和三类暂停点的刷新/重启。

空间检查通过隔离的系统 GDAL/OGR/OSR 只读运行，普通 Python 环境不安装 GIS 库。重投影读回与 OSR 变换比较；缓冲检查米制投影及适用区域、数量、属性、几何和距离。固定三点样例另有不调用 GIS 库的 Snyder 坐标公式与正多边形预期，见[样例依据](../tests/fixtures/gis/README.md)。任意复杂缓冲的一部分检查依赖 OGR/GEOS，与 QGIS 可能共享 GEOS，因此不能宣称对所有几何都有完全独立的数值 oracle。无法证明的空结果与超出数值精度的微小非零距离应明确失败。

GeoPackage 只接受可独立读取的文件。原始输入、产物取证及消费边界若发现 SQLite 的 `-wal`、`-shm` 或 `-journal`，应明确停止；主文件 SHA 不足以描述这些旁路中的数据。首期不自动删除旁路或替用户做事务恢复，即使旁路为空或疑似陈旧也保守拒绝。工具已完成但产物无法验证时，保留完成事实，单独记录完整性或空间失败；加载工具回显的来源引用不算新生成文件。

每项 issue 写明命令、输入、模型/锁文件、退出码及证据。留出集和 A0/A1 的口径由[实施计划](implementation-plan.md)拥有。故障样例、重试、缺 usage 不从分母隐藏。运行日志置于忽略目录；需共享时导出脱敏的最小证据。

## 真实加载复验

启动项目专用 PostgreSQL、QGIS、MCP 和后台，运行生产构建后从 9000 进入页面。上传 [合成点数据](../tests/fixtures/gis/phase11_points.geojson)，确认页面列出的文件 ID、CRS、字段和要素数；提交仅加载并指定显示名称的请求。

在待审计划中核对具体调用、文件引用与参数。拒绝并填写新名称后，确认同一 task_id 的新修订使用新名称；批准前 QGIS 图层数保持不变。批准后读回 QGIS 的来源、CRS、字段、属性和坐标，与[样例依据](../tests/fixtures/gis/README.md)核对，确认只新增一个图层。对原始文件及受控上传副本复核 SHA-256。

使用页面中的 task_id 查询 `GET /api/executions/{task_id}`，核对批准计划、实际 attempt 和图层 ID；确认前 confirmed 为 null，提交结果确认后为对应布尔值。加载既有输入不生成新空间文件，其图层引用由步骤结果中的实际 ID 和 source 表达。审核恢复与 Processing 按各自正式切片另行验收。

## 重投影与缓冲复验

上传同一三点样例，请求先重投影到 EPSG:32650，再缓冲 100 米，使用每象限 8 段、不融合、保留属性的 GeoPackage，仅加载最终缓冲结果。批准前核对两项 Processing 调用的实际参数、顶层 `distance_unit: m`、文件引用和目标 CRS；QGIS 图层及输出文件应保持不变。

批准后从业务记录取得两个实际产物。缓冲的 INPUT 必须等于重投影步骤的已验证实际输出；读回两个文件，与固定数学预期核对坐标、CRS、三条属性及缓冲半径、边数、面积和周长。每项 Processing 保留工具完成事实、文件大小及 SHA-256、空间报告；通过空间检查的产物才可交付，用户确认单独保留。QGIS 最终图层来源应指向缓冲产物，原始文件 checksum 保持不变。

在公开工作流接口用 GIS seam 注入“调用完成但坐标、CRS 或半径错误”的文件，验证任务失败、产物仅作诊断、后续加载不发生且用户确认仍为空。检查器不可用或前序产物被修改也必须停止。没有明确单位、度作为米或计划距离偏离用户数值时，应在首次 GIS 写入前要求修正；这些受控反例不能报告为真实 QGIS 执行证据。

## 裁剪、融合与统计复验

按[开发样例](../tests/fixtures/gis/README.md)创建并上传独立 GeoPackage 来源和裁剪面。T05 的裁剪、融合、几何属性及统计输出限定 GPKG，以保留空图层与全 NULL 字段类型；输入仍按首期支持格式选择。

审核并执行裁剪→按字段融合→查看，逐项读回固定交集、分组及完整属性来源。面积统计使用受限的几何属性辅助算法，明确图层 CRS 平面计算与可信单位；逐要素面积和与全部融合后的并集面积分别核对。原有 `area`/`perimeter` 字段碰撞、不同 CRS、未知或非数值字段、错误计算方式应在首次写入前要求修正，不能静默选择旧字段或退化成计数。

统计表是无几何、无 CRS 的合法表；空间源的 CRS 要求不随之放宽。核对总要素数、非空数值数、NULL、sum/min/max、分类与被 QGIS 省略的全 NULL 类别。空和原始值 0 同时标明无有效数值，MIN/MAX 为无值。总数若由加载步骤取得，计划须明确展示其加载行为，并以独立文件读回复核真实回显，不冒充新文件产物。

远离裁剪面及空来源应产生可读的合法零行；当前 QGIS 对空来源的全部融合可能生成一条空几何占位，验证不能通过时保留工具完成与诊断，停止后续消费。按字段空融合、空统计及全 NULL 统计分别复验。错误分组、几何、数值或被修改的前序产物不能进入成功结果。页面与业务查询中的值必须来自通过验证的实际产物，用户确认仍单独记录。

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

## 固定评测复验

T07 的输入物化、A0/A1 冻结运行、用量完整性、原始分母及专项证据格式由[评测入口](onboarding/benchmark.md)维护。离线 runner/collector/规划分类测试使用替身或拦截的锁定 SDK；`tests/test_t07_fixtures.py -m external` 只证明系统 GDAL 对合成产物的读取，不代替真实模型/MCP/QGIS 或浏览器验收。
