# V1 干净重建记录（2026-10-31）

本记录属于 [T08](https://github.com/init-wei/qgis-workflow-agent/issues/28)，用于证明本机 Linux/WSL 上的独立源码克隆、锁定依赖、新 PostgreSQL 数据卷和独立 QGIS profile 可以构建并进入真实浏览器路径。它不证明任意操作系统从空白主机自动安装 QGIS/Docker，也不将历史切片证据当成本版本全部重跑。完整功能与限制见[AC 证据入口](acceptance.md)，操作者复现步骤见[演示](v1-demo.md)。

## 固定来源与隔离

从 GitHub 克隆 `codex/v1-refactor` 到忽略目录 `.target/t08/clean-01/repo`，实际克隆后更新到 `2a6b2d01093ef3e917abab4d1cc569c7c8c4aac9`。应用源码为已审查的 `3b8f29653fb854f19e20134a281cde2f5f63dd1d`；后一提交只调整首次执行前的浏览器验收定义，不改变应用源码。新目录独立安装 `.venv` 与 `node_modules`，不复制原虚拟环境、应用数据库、上传、产物或 profile。系统工具和包管理器缓存可复用，不能称依赖每个字节均重新下载。

服务端 `.env` 按既有授权复制并设为 0600，工作区绝对路径指向新克隆；不导出其内容。`COMPOSE_PROJECT_NAME=qwa-v1-t08-clean-01` 使用事先确认不存在的新卷 `qwa-v1-t08-clean-01_postgres_data`，运行端口为 55433。原项目数据库正常停止并保留原卷，没有执行 `down -v`。本次 QGIS 的工作路径为新克隆的 `.runtime/qgis`，初始工程图层数为 0。仅停止归属已核实的本项目 QA 实例，其他 QGIS 实例保留。

## 命令与结果

| 实际操作 | 结果与范围 |
| --- | --- |
| Git clone、fast-forward 更新 | 成功；浏览器开始前源码工作树干净，记录固定 commit/tree |
| `mise trust`、`./scripts/dev.sh install` | 成功；`uv sync --locked` 与锁定 npm 安装，新环境依赖就绪 |
| `./scripts/dev.sh prepare` | 成功；创建独立运行目录、合成输入与插件链接 |
| `./scripts/dev.sh db` | 成功；新卷健康，初始化 schema；实际 PostGIS 3.4.3、pg_trgm 1.6 |
| `./scripts/dev.sh test-db` | **10 passed、1,635 deselected**；真实项目专用 PostgreSQL 边界，不是 GIS 数值正确性 |
| `./scripts/dev.sh check` | **1,446 passed、199 deselected**；compile、Ruff、38 份当时文档本地链接、离线 pytest、前端类型及生产构建均退出 0 |
| 单独 `typecheck`、`build` | 退出 0；构建由后台 9000 提供 |
| `./scripts/dev.sh health` | 首次在 QGIS 插件尚未监听时失败；保留日志。等待服务实际就绪后重查，配置、数据库、socket、HTTP MCP 均通过，列出 9 个工具。未带 `--llm-live`，该次健康检查不等于真实模型验收 |

实际版本：应用 Python **3.12.14**、Node **24.20.0**、npm **11.19.0**；系统 Python **3.14.4**、QGIS **3.40.15-Bratislava**、GDAL **3.12.2**。QGIS 使用系统依赖，普通应用环境没有安装 PyQGIS/GDAL。

安装阶段 npm 记录了三个未被 allowScripts 覆盖的安装脚本提示；数据库测试有 psycopg_pool `open` 默认值弃用提示；离线测试有 LangSmith SDK 使用已弃用 ast.Str 的提示；前端构建有 Browserslist 数据陈旧提示。上述当次命令仍退出 0，没有为消除提示升级依赖或更改锁文件。它们不构成已经完成依赖升级或完整安全审计的证据。

## 当前浏览器专项

本次使用 [T08 v2 定义](../../tests/fixtures/gis/t08_browser_routes.json)，与 T07 冻结的七项历史浏览器专项分开。原始运行 `browser-01` 的结果为 **2/3**：根页面/设置页的直接打开和刷新、必要资源均通过；三个退役 API 均 JSON 404，默认按钮和 Vue 路由不存在。真实加载第三项因计划显示名与请求不符而失败，保留原记录，不能改报为 3/3。

真实任务提交前发现原验收脚本误读 `execution.revision`，接口实际为 `plan_revision`；同时，当前 QGIS MCP 的 `get_layer_features` 用四位小数 WKT，不能证明 1e-10°。这些问题在首次真实请求前修正；定义采用 1e-4° 比较**显示 WKT**，舍入本身另有约 5e-5° 不确定度，不宣称底层坐标误差上限。原始和上传副本的 SHA 严格一致性另行核对，不能用显示精度替代原始字节校验。

首次真实任务 `0dbe835a-6457-4592-bf4c-fee34805b3c8` 的 revision 1 将显示名从 `T08 当前真实加载` 缩成 `T08`；门禁在批准前停止，业务记录尚不存在，专用 MCP 传输记录中写调用为 0，QGIS 仍为空。原记录 SHA-256 为 `30536af9c26558567e6252e36b049ff1d327eea96bdf535848d0634b2e6da074`。这说明当前自然语言规划可能丢失未加引号的多词名称，审核必须检查实际参数。后续明确意见的同任务修订与该原始失败分开记录，不新建任务来替换分母。

## 同任务纠正、真实发现与补修

在同一 task 下通过页面拒绝 r1，并明确显示名必须完整等于引号内的“T08 当前真实加载”。r2 的规划为 1 次生成且实际收到模型响应；批准前独立传输写调用仍为 0。批准后真实 QGIS 只新增 1 个 EPSG:4326 三点图层，来源、字段、属性及显示 WKT 与固定预期一致；原始及上传 SHA、原工程 SHA 不变，没有新文件产物。业务记录保留 1 个完成 attempt，确认前为 null，核对后由自动开发操作者确认 true，并到达无 pause/continuation 的终态。这是同任务的明确纠正，不能替换首次失败或当作新的盲测。

续跑驱动 `browser-revision-02` 在已经完成确认后，因为主界面与弹窗各有同一成功文本而触发 strict selector 错误，仍保留 failed。其证据 SHA 为 `578642190b0fa62f63e8dfc84f37845997ab0458852e737e571a7797788ca4af`。随后只读 `browser-audit-03` 发现实际展示缺陷：重新进入完成会话没有业务结果卡，只剩错误引用 r1 名称的模型摘要。由此建立 [#30](https://github.com/init-wei/qgis-workflow-agent/issues/30)，不是把这些失败改写为通过。

补修固定在 [`b5a0d40dc239c2d4979ce640a9d75bb0079453ac`](https://github.com/init-wei/qgis-workflow-agent/commit/b5a0d40dc239c2d4979ce640a9d75bb0079453ac)。新摘要直接投影当前计划、执行与验证事实，不再调用模型自由总结；旧模型文本原样保留并标为参考。终态页面从现有 PostgreSQL 业务 API 恢复记录，分开展示批准调用、尝试/审核记录、实际返回、空间验证与确认。风险审核的 blocked attempt 明示未发送，不能把 attempt 数当作 MCP 派发数；只读成功没有写入 outcome 字段，也不会被误判成未完成。

更新独立克隆的锁定依赖与构建后，`browser-audit-04` 与正常退出/重启后台后的 `browser-audit-05` 均只执行 GET、刷新和重选：原 task/r2、完整名称、验证与 confirmed=true 均恢复；前后业务事实及 QGIS 完全一致，历史错误摘要没有被改写。另对保存的真实 result_state 调用新的纯事实投影节点，得到正确 r2 名称；这不是重跑 graph、模型、数据库写入或 GIS。

独立 Python 再审已关闭的完整代理日志：10 份 POST 均可解析，唯一写调用为 index 9 的 `add_layer`，对应实际来源、请求 ID、成功上游响应与真实图层 ID；累计仍是 **1**。该结论独立于业务 attempt。详细对比收据为 `restart-and-readonly-audit.json`，闭合传输核查为 `.target/t08/final-transport-independent.json`。

## 最终门禁与环境归还

| b5a0d40 检查 | 实际结果 |
| --- | --- |
| 主工作区完整 `check` | **1,459 passed、198 deselected、1 SDK warning**；compile/Ruff/41 份当时文档链接/前端类型/生产构建通过 |
| 独立克隆完整 `check` | **1,459 passed、198 deselected、1 SDK warning**；其已提交版本的 38 份文档链接、类型、构建通过 |
| 两处独立 `npm run test:browser` | **4 passed**；Node 内建测试运行器 + 锁定 Playwright，本地生产构建和固定 API 替身，不要求实际数据库、模型或 GIS |
| 新永久浏览器测试对旧构建 | 同一测试文件在 2a6b 构建 **4 failed**，在补修构建 **4 passed**；不是删除旧断言来通过 |
| 独立代码两轴复核 | Standards 与 Spec 对 tree `7b09feaab4d3609e37cc59957f15dd90be9c0903` 均通过；真实只读证据与最终文档随 T08 最终检查点另行核对 |
| 远端 CI | [run 41199799580](https://github.com/init-wei/qgis-workflow-agent/actions/runs/41199799580) 三个 job 成功，Frontend build 内含 Chromium 安装与四项浏览器回归 |

新增 Playwright 1.63.0 及 core 两个开发依赖，未升级其他锁定包；不进入产品运行链。原 reflector 外部调试文件只有打印、没有断言，已由有断言的事实投影回归代替。默认 CI 仍不读取真实模型 key、不调用 QGIS 或用户数据；Chromium/依赖下载属于测试准备。

验证后只停止归属已核对的独立克隆后台、MCP、代理和 QGIS，停止其数据库并保留新卷及全部证据。原工作区的 PostgreSQL 卷、QGIS profile、MCP 和后台已恢复，`health` 通过；9000 的根页面、设置页、会话及 QGIS 状态接口正常。原保存工程 SHA 与备份一致，另一用户 QGIS 实例未被终止。此前 36 个未保存 QA 图层保留了只读快照，没有重放历史 GIS 写入去恢复它们。主后台直连 8000，LangSmith 应用和全局开关均实查为 false；服务身份与归还结果位于 `.target/t08/main-restored/`。

## 证据与复现边界

详细日志在本地忽略目录 `.target/t08/clean-01/`，`environment-receipt.json` 保存环境日志的 SHA、路径和字节数，`runtime-versions.json` 记录实际版本及数据库卷身份。浏览器还需冻结输入、定义、驱动和代理的 SHA，并分开核对业务 attempt 与实际 MCP 请求。原始 T07 定义、oracle、96 条 primary、seal 和失败/unknown 不改写；其旧源码可通过原 commit 及本地历史来源映射重建。

这些日志包含本地路径和运行状态，未作为公开源码提交。需分享时按最小范围脱敏导出；源码、锁文件和跟踪的微型合成 fixture 足以重新开始一轮独立验收，但新的模型响应和 GIS 结果必须重新核对，不能复用旧成功标签。
