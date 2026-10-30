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
