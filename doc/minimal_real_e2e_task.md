# Minimal Real E2E Task

本说明用于验证当前项目的真实最小闭环：DeepSeek 规划、人工审核、受控工具调用、QGIS MCP 执行、结果确认和总结。它不是性能 benchmark，也不要求复杂 GIS 流程；目标是确认主链路真的能跑通。

## 目标

使用仓库内已有示例数据 `shared/uploads/phase11_points.geojson`，让 Agent 在 QGIS 中加载点图层，并输出一次执行 trace 和结果确认流程。

## 前置条件

1. Docker Desktop 已启动。
2. QGIS Desktop 已启动，并已安装/启用项目的 QGIS MCP 插件。
3. QGIS MCP 插件监听 `localhost:9876`。
4. `.env` 已配置 DeepSeek：

```bash
OPENAI_API_KEY=你的 DeepSeek key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL_NAME=deepseek-chat
QGIS_MCP_SERVER_URL=http://localhost:8000/mcp
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/qgis_db
```

5. 示例数据存在：

```bash
Test-Path shared\uploads\phase11_points.geojson
```

预期输出为 `True`。

## 启动步骤

### 1. 启动数据库

```bash
docker compose up -d postgres
uv run python scripts/setup_database.py
```

### 2. 检查开发环境

```bash
uv run python scripts/health_check.py --llm-live --start-http-mcp
```

预期：

- PostgreSQL 为 `PASS`。
- QGIS socket 为 `PASS`。
- HTTP MCP 为 `PASS`。
- LLM live call 为 `PASS`。

如果不想马上调用 DeepSeek，可先运行：

```bash
uv run python scripts/health_check.py --skip-llm --start-http-mcp
```

### 3. 启动 HTTP MCP wrapper

健康检查的 `--start-http-mcp` 只用于临时检查。正式 E2E 建议单独开一个终端保持 wrapper 运行：

```bash
uv run python qgis_mcp_server\qgis_mcp_server.py
```

保持该终端不要关闭。

### 4. 启动后端

另开终端：

```bash
uv run python ui\backend\main.py
```

后端默认地址为 `http://localhost:9000`。

### 5. 启动前端

开发模式：

```bash
cd ui\frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。

也可以构建后由后端提供静态页面：

```bash
cd ui\frontend
npm install
npm run build
```

然后访问 `http://localhost:9000`。

## 建议输入任务

在 Web UI 中输入：

```text
请在 QGIS 中加载 shared/uploads/phase11_points.geojson 这个矢量点图层，图层名称使用 phase11_points。只需要加载图层，不要删除、覆盖或保存工程。
```

这个任务刻意保持简单：

- 不触发高风险 `execute_code`。
- 不要求复杂 processing 参数。
- 主要验证 Planner、计划审核、ToolCallPlan 编译、MCP `add_layer` 调用和结果确认。

## 预期流程

1. **Planner 输出计划**
   - UI 收到计划审核事件。
   - 计划中应包含加载 `shared/uploads/phase11_points.geojson` 的步骤。

2. **人工审核计划**
   - 点击批准。
   - 后端将 `TaskPlan` 提升为正式计划。

3. **受控执行**
   - `executor_node` 将 `TaskPlan` 编译为 `ToolCallPlan`。
   - `ExecutionLoop` 调用 HTTP MCP wrapper。
   - QGIS 中应出现点图层。

4. **结果确认**
   - 成功执行后 UI 应出现结果确认，而不是直接结束。
   - 确认任务成功后，Graph 进入 `reflector_node`。

5. **最终总结**
   - UI 收到最终总结。
   - 后端 checkpoint 中应保留 `execution_trace`。

## 成功判定

满足以下条件即可认为最小真实 E2E 通过：

- DeepSeek live call 成功。
- QGIS MCP socket 和 HTTP MCP wrapper 均可用。
- UI 能展示计划审核。
- 批准计划后 QGIS 实际加载了 `phase11_points.geojson`。
- UI 出现结果确认。
- 确认后出现最终总结。
- `uv run pytest` 仍然通过。

## 常见失败与处理

### HTTP MCP 失败

现象：`health_check.py` 显示 HTTP MCP `FAIL`。

处理：

```bash
uv run python qgis_mcp_server\qgis_mcp_server.py
```

再运行：

```bash
uv run python scripts/health_check.py --skip-llm
```

### QGIS socket 失败

现象：QGIS socket `FAIL`。

处理：

1. 确认 QGIS Desktop 已打开。
2. 确认 QGIS MCP 插件已启用。
3. 确认插件监听 `localhost:9876`。
4. 重启 QGIS 后再运行 health check。

### 没有结果确认弹窗

这通常说明 `executor_node` 后的 interrupt/result review 编排有问题。检查：

- `ui/backend/main.py` 的 `interrupt_after` 是否包含 `executor_node`。
- `executor_node` 成功后是否保持 `is_completed=None`。

### 高风险审核没有暂停

检查：

- `ui/backend/main.py` 的 `interrupt_before` 是否包含 `risk_review_node`。
- `agent.graph.should_continue_after_executor()` 是否能在 `pending_risk_review` 存在时路由到 `risk_review_node`。

## 不在本 E2E 范围内

- 复杂 buffer/slope 等 processing 参数自动修复。
- SkillRegistry 或自动复用 Skill。
- 真实性能 benchmark。
- 批量数据处理。
- 完整 RAG evidence 前端面板。
