# 开发环境

当前实测和限制见[基线报告](baseline-2026-10-30.md)。先完成环境，再从[任务交接](../agents/orchestration.md)领取 T01。现有原型尚未通过 V1 业务验收。

## 干净环境

前提：Linux/WSL、Git、uv、Docker+Compose、Node 24.20.0（可用 mise 或 nvm）、发行版 QGIS Desktop 及其 PyQGIS。不要向 uv 环境安装 QGIS。

1. checkout `codex/v1-refactor`。
2. 复制 `.env.example` 为 `.env`，填入临时模型配置和随机数据库密码；DATABASE_URL 与 POSTGRES_* 一致，密码可用随机 hex 避免 URL 转义。`chmod 600 .env`。本机已经完成，不覆盖现有值。
3. 如使用 mise，先审阅 `mise.toml`，执行 `mise trust` 和 `mise install`。不改变全局 Node。
4. `./scripts/dev.sh install` 安装锁定依赖。
5. `./scripts/dev.sh prepare` 创建运行目录、合成输入和独立 QGIS plugin 链接。
6. `./scripts/dev.sh db` 启动独立数据库，初始化原型 schema；不覆盖现有 5432 服务。
7. `./scripts/dev.sh check`，另跑 `./scripts/dev.sh typecheck` 核对基线失败/后续修复。

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

`./scripts/dev.sh health --llm-live` 会实际使用 `.env` 的临时 key 调用模型。LangSmith 默认关闭；缺 LangSmith key 不阻塞 T01–T03。`./scripts/dev.sh db-stop` 停止本项目数据库，保留卷；不要为日常清理执行 `down -v`。

前端当前产物在 ui/frontend/dist，而旧后端静态路径指向另一目录；本轮使用 Vite 入口。单地址生产构建和深链接由 T01/T08 验收，不把构建成功当成已可生产部署。
