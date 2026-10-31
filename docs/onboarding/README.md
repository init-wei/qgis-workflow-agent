# 开发环境

环境建立时的实测和限制见[基线报告](baseline-2026-10-30.md)。完成环境后从[任务交接](../agents/orchestration.md)查询正式任务前沿；业务验收证据由 GitHub issue 拥有。

## 干净环境

前提：Linux/WSL、Git、uv、Docker+Compose、Node 24.20.0（可用 mise 或 nvm）、发行版 QGIS Desktop 及其 PyQGIS、系统 Python 的 GDAL/OGR 绑定。不要向 uv 环境安装 QGIS。

1. checkout `codex/v1-refactor`。
2. 复制 `.env.example` 为 `.env`，填入临时模型配置和随机数据库密码；DATABASE_URL 与 POSTGRES_* 一致，密码可用随机 hex 避免 URL 转义。`chmod 600 .env`。本机已经完成，不覆盖现有值。
3. 如使用 mise，先审阅 `mise.toml`，执行 `mise trust` 和 `mise install`。不改变全局 Node。
4. `./scripts/dev.sh install` 安装锁定依赖。
5. `./scripts/dev.sh prepare` 创建运行目录、合成输入和独立 QGIS plugin 链接。
6. `./scripts/dev.sh db` 启动独立数据库，初始化项目 schema，保留既有表和数据；不覆盖其他 5432 服务。
7. `./scripts/dev.sh check` 运行本地门禁，包含前端类型检查和生产构建；外部服务验收见[测试规范](../testing.md)。

## 开发时启动

分别保留终端进程：

```bash
./scripts/dev.sh qgis
./scripts/dev.sh mcp
./scripts/dev.sh backend
./scripts/dev.sh frontend
```

浏览器访问 `http://127.0.0.1:5173`；开发代理转发到 9000。`qgis` 只启动 `.runtime/qgis/profiles/qwa-v1`，在 `.runtime/qgis/project.qgz` 工作，自动打开项目插件端口 9876；不要同时打开另一实例使用该端口。

端口：PostgreSQL 55433、MCP HTTP 8000、后端 9000、Vite 5173、QGIS socket 9876，均以本机回环为运行入口。若占用先查所属进程，不终止其他项目。`dev.sh` 的服务端口固定为该开发约定；如需修改，同时改 `.env`、前端代理与脚本，不宣称当前支持动态端口配置。

`./scripts/dev.sh health --llm-live` 会实际使用 `.env` 的临时 key 调用模型。LangSmith 默认关闭；配置与真实平台复验见[可选观测](langsmith.md)，缺 key 不阻塞核心 GIS 开发。`./scripts/dev.sh db-stop` 停止本项目数据库，保留卷；不要为日常清理执行 `down -v`。

生产构建使用 `./scripts/dev.sh build`，产物在 ui/frontend/dist，后端从同一目录提供页面和静态资源。构建后访问 `http://127.0.0.1:9000/` 或 `/settings`；此入口无需 Vite 进程。开发热更新继续使用 5173。

方案模板的候选提示、类型与审阅边界见[模板说明](skills.md)。模板与每次任务的执行批准分别记录。固定留出集、对照运行和证据汇总见[评测复验](benchmark.md)。

## 可选测试素材

用户提供的矢量与栅格已在本地解压并校验，见[测试数据说明](test-data.md)。原始数据不进入 Git 或默认 CI；后续派生样例和留出集按规格独立验收。
