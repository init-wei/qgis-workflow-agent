# 固定 GIS 评测

本页维护 T07 的运行方法与证据边界；实际运行、样本数和结论由 [T07](https://github.com/init-wei/qgis-workflow-agent/issues/27) 拥有。范围、指标与阶段依赖见[实施计划](../implementation-plan.md#评测执行约定)。本地逐次记录是实验依据，LangSmith 是可选分析副本。

## 数据与独立预期

[输入与请求定义](../../tests/fixtures/gis/t07_benchmark.json)包含六类任务的十二个留出场景，每场景两种中文表述，共二十四个请求；另列六个开发样例引用、十九个异常子例与七个浏览器专项。专项定义并不表示已执行，未取证的条目保持 `not_run`。

T07 冻结版本 `c2d5a59658fff61b55b6a1f3fb78ffe71231f442` 的浏览器七项及其 [7/7 实际记录](https://github.com/init-wei/qgis-workflow-agent/issues/27#issuecomment-6541786672)保持原有定义和分母，其中旧 `/trace-demo` 深链接属于当时的 UI。该 Vue 路由及默认入口现已退役；当前版本另按 [T08 v2 定义](../../tests/fixtures/gis/t08_browser_routes.json)检查，不以 v2 的结果替代 v1 旧例或改写原七项、96 次任务、定义与 oracle。若在新批次遇到尚未执行的退役旧例，记 `not_run`；已经执行的失败保留 `failed`，既有历史通过结果也不改写。当前复验结果以 [T08](https://github.com/init-wei/qgis-workflow-agent/issues/28)为准。

后续 [#30](https://github.com/init-wei/qgis-workflow-agent/issues/30) 将反思节点从模型自由总结改为业务事实投影，并修复完成会话结果卡恢复；这不改变 T07 原始 96 次的记录、分母、用量或 Cloud 导入。用后续版本运行的实验必须重新冻结并标注实际版本和配置，不能把新运行改写为原批次或把旧模型调用当作新版本的调用事实。

[冻结 oracle](../../tests/fixtures/gis/t07_benchmark_oracle.json)使用 Snyder 投影级数、正三十二边形公式、鞋带面积与固定属性手算。GDAL 写入输入并读取实际产物，不重跑 Processing 制作预期；实际多边形覆盖比较仍使用 OGR/GEOS，与 QGIS 可能共享几何库，这项依赖不能称为完全独立实现。预期仅适用于这些固定输入和参数。

在项目根目录检查定义；用系统 GDAL 物化到新的忽略目录，不覆盖已存在目录：

```bash
python3 scripts/build_t07_fixtures.py validate
/usr/bin/python3 scripts/build_t07_fixtures.py materialize \
  --output-dir .target/t07/inputs-NEW
```

当次清单绑定输入文件、定义、oracle、生成器和 Snyder 辅助脚本的 SHA。GeoPackage 创建时间可能影响文件字节，以当次清单为准；输入前后 checksum 不变，不能将新物化文件偷偷替换到已运行的实验。

## 先用开发样例试跑

使用既有 T03/T05/T06 开发数据，分别在 A0/A1 运行加载、重投影缓冲及统计，共六次试跑，按已有[空间复验](../testing.md)读取实际结果。核对审批前零调用、同一批准路径、原始用量和服务退出，再估算正式批次费用。不要用留出请求调试模板、参数解析或评价器。runner 的 dev 引用没有留出 oracle，会拒绝执行，避免用错评价器。

A0 使用 `QGIS_AGENT_SKILLS_ENABLED=false`，A1 为 `true`，只改变候选提示。显式启用 `QGIS_AGENT_EVAL_NONSTREAMING=true`，并将 `QGIS_AGENT_EVAL_USAGE_PATH` 指向本项目 `.target/t07/` 中的文件，才会记录本地原始用量；两项默认关闭。该开关使用锁定 SDK 的非流式选项，不改变普通交互默认行为。日志不包含提示、响应正文、几何或密钥。

## 冻结与运行

准备一份本地价格快照 JSON，记录 `source_url`、`fetched_at`、与请求一致的 `requested_model`、官方文档支持的 `provider_response_models` 白名单和 `usd_per_million`，其中 `peak`/`off_peak` 各含 `cache_hit`、`cache_miss`、`output`。从实际供应商官方价格页取得数值，不按适配器名称猜测。价格可变，正式批次使用当次核对的快照；无快照仍可评测，但费用未知。

```bash
uv run --locked python scripts/evaluate_t07.py schedule \
  --output-dir .target/t07/experiment-NEW \
  --experiment-id experiment-NEW --split heldout \
  --model deepseek-flash --temperature 0.1 --max-tokens 8192 \
  --pricing .target/t07/pricing.json
uv run --locked python scripts/evaluate_t07.py dry-run \
  --manifest .target/t07/experiment-NEW/manifest.json
```

每个请求按 A0第一次、A1第一次、A1第二次、A0第二次排列，共九十六个槽位，每组四十八次。manifest 冻结请求、模型请求参数、模板全集、业务 Python 文件全集、锁文件、oracle 与生成器；`inputs-binding.json` 另外绑定实际物化清单。冻结也包含上传、算法能力及产物验证所用的运行脚本。每槽及关键调用前后复核；冻结后任何受控源码、模板或评价器变动都要求新实验，变化期间的既有事实保留但不得计为可比较成功。

固定 `temperature` 表示请求参数一致；供应商思考模式是否采纳该参数、模型别名实际指向的版本，需要根据实际响应与官方文档单独说明。没有逐次返回证据的配置不能称为实际响应事实。

保持项目 PostgreSQL、MCP、QGIS 启动。确认旧任务全部结束且后台空闲，正常停止自己拥有的现有 9000 后台，再由 runner 管理新后台：

```bash
uv run --locked python scripts/evaluate_t07.py run \
  --manifest .target/t07/experiment-NEW/manifest.json \
  --inputs-dir .target/t07/inputs-NEW --manage-backend
```

manager 只停止自己创建的进程，只在 arm 改变时重启后台；端口被占用则拒绝接管，不能终止其他项目。每个请求通过公开 HTTP 上传、提交、计划审核、风险审核、独立产物检查与结果确认。自动开发操作者只批准符合冻结请求的计划，不作参数 repair，也不冒充人类质量评价。计划、工具完成、空间通过与结果确认分别保存。

未知、环境中断、检查器不可用或执行后未结束的 continuation 会停批并保留现场。先按具体任务、会话、调用和文件核查；继续原 checkpoint 才使用 `--resume`。不要重发已发送的写操作。不符合请求的计划拒绝后不自动重新规划：核对同一任务/修订、已拒绝、零 GIS attempt、无 inflight 或产物、仅只读规划待续后，可记录当前槽位未执行失败；这不表示工作流已经终止。该处置保留公开快照和哈希，任何状态变化都停批。无关历史会话只有能证明只读规划且无执行时才允许保留，不能修改或删除旧任务来清场。

只有核查后的明确失败才可用 `--rerun SLOT_ID` 创建有父链的新 attempt；旧失败仍在原始分母。未运行的槽位也保留。

## 汇总与专项证据

```bash
uv run --locked python scripts/evaluate_t07.py aggregate \
  --manifest .target/t07/experiment-NEW/manifest.json \
  --special-results .target/t07/experiment-NEW/special-results.jsonl
```

`results.jsonl` 为 append-only 的 started/checkpoint/finished 事实；汇总产生 JSON/CSV，可在不访问模型、数据库或平台的情况下重建。异常和浏览器由固定定义逐项运行，写入对应 `experiment_id/group/case_id/status` 与实际 `evidence_refs`；每项本地证据引用含路径及 SHA-256。定义、测试存在或 `status=passed` 本身不证明假成功为零、未批准执行为零，相关指标必须有明确布尔事实与可核对证据。

首次计划有效率消费当前修订的 `planning_attempt_diagnostics`：明确模型无效可记 false；请求拒绝、环境故障、未知或旧 checkpoint 缺首轮事实保持 unknown。最多两次自动尝试与人工修改分开，不从重试次数猜模型错误或模型调用量。

用量完整性同时检查每次 HTTP 已有 start 事件、图 invocation 清单、模型 start/end 配对和写失败计数。`journal_integrity_complete` 只表示日志配对与独立图计数完整，逐字段 `field_complete` 才表示该 token 字段来源完整；缺失不补零或推算，已知部分和完整总数分别展示。费用还要求每个实际响应模型匹配报价白名单、缓存分项不与 prompt 总数矛盾；只按有来源的原始字段和完整冻结价格给观察到用量的区间，不能称为供应商账单；SDK 透明重试也可能有无法观测的收费。完整性不通过时费用未知。

各指标同时报告 n/N 与 unknown，P95 附样本量。普通任务分母、固定反例、浏览器专项、重跑和自动开发审核分别列出；没有真实人类改动观察时，人类改动率保持未知。一次小样本比较不能宣称泛化收益或显著提升。平台导入若另行验收，只导入既有本地结果和业务 ID，不重跑 GIS，不将导入时间当成原业务延迟。

## 导入既有结果到 LangSmith

T04 实际通过且本地九十六个 primary 都有可核对身份后，先生成安全导出文件，再检查其 `ready`、分母、未知值和来源 SHA：

```bash
PYTHON_DOTENV_DISABLED=1 DATABASE_URL='' uv run --locked \
  python scripts/export_t07_langsmith.py \
  --manifest .target/t07/experiment-NEW/manifest.json \
  --output .target/t07/experiment-NEW/langsmith-export.json
```

明确执行时，在同一命令前设置 `QGIS_AGENT_LANGSMITH_ENABLED=true LANGSMITH_TRACING=false`，给 `uv run` 增加 `--env-file .env`，末尾增加 `--execute`。凭据仍只从服务端配置读取；导出文件必须与已审阅的版本完全相同。脚本用官方 Dataset/Experiment SDK 导入四十八个共享 example 和每组四十八个已测 primary，不发模型或 GIS 请求；失败和未知保持原样，成功 rerun 不替换原始分母。

trace 正文继续隐藏，业务 ID 与原始行 SHA 用于关联；原 GIS 毫秒单独作为反馈，平台原生导入 latency 不代表业务延迟。原始 journal 未包含完整 collector enrichment，本有限导出不猜测 token 或费用，相关结果仍由本地汇总拥有。SDK 附加的版本结构与项目 metadata 单独过滤、核对，不将 Git 身份上传。

导出同时绑定实际 `runtime.jsonl` 的 SHA，核对汇总的漂移状态；源码漂移、运行来源缺失或矛盾的批次禁止导入比较，既有事实仍保留本地。执行回执保存实际 Dataset/Project ID、官方 URL、来源 hash 和平台读回结果；未完整读回两组 run 及反馈，不标完成。已有回执、同名资源或任何写入不明都会停止并留待核查，不删除回执后盲目重跑。专项异常与浏览器结果保持在本地固定专项集合，不混进正常请求分母。
