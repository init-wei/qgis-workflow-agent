# QGIS Agent v2

> 一个面向 QGIS 的自然语言自动化原型。当前主线是：DeepSeek/OpenAI-compatible LLM 生成结构化 `TaskPlan`，人工审核后编译为受控 `ToolCallPlan`，通过 QGIS MCP 执行并记录 `ExecutionTrace`。

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-green.svg)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## ✨ 核心特性

### 🧠 当前可用能力
- **自然语言规划**: 用中文描述 GIS 任务，Planner 生成严格 `TaskPlan` JSON。
- **计划人工审核**: UI 保留 legacy plan 审核流程，批准后同步提升 `TaskPlan`。
- **受控工具调用**: 审核后的计划会编译为 `ToolCallPlan`，再由 `ExecutionLoop` 调用 MCP 工具。
- **结果确认**: 执行后等待用户确认任务是否完成，再进入总结节点。

### 📚 检索证据与受控执行
- **TaskPlan → ToolCallPlan**: 计划审核后编译为明确的 MCP 工具调用序列
- **ExecutionLoop**: 每次工具调用都会生成 `ExecutionTrace`，失败后按错误类型评估、修复或请求人工介入
- **Error-driven Retrieval**: 参数错误、缺文件、执行错误可挂载检索证据，为修复提供上下文

### 🛡️ 审核与安全边界
- **ToolPolicy**: 对路径、工具范围和 `execute_code` 等高风险能力做边界检查。
- **Risk Review**: `execute_code`、保存工程、删除/覆盖等高风险调用会暂停，等待批准、拒绝或修改参数。
- **未审核步骤保护**: `metadata.tool_calls` 中未出现在审核步骤里的 `step_id` 不会被执行。

### 🧪 Demo 与可复放证据
- **静态 demo case**: 提供成功、失败修复、SkillDraft preview 三个可复放案例。
- **Trace Demo 页面**: `/trace-demo` 可查看 demo trace、artifact、repair metadata 和 SkillDraft 变量。
- **SkillDraft**: 可以从成功 trace 提取待审核草稿，但当前不会自动注册或自动复用。

### 🌐 现代化 Web 界面
- **实时流式输出**: SSE 推送 Agent 执行状态
- **多会话管理**: 支持多个任务并行，历史记录持久化
- **文件上传**: 支持上传地理数据文件进行处理
- **轻量团队协助**：支持用户文件上传与处理

## 🏗️ 系统架构

```
用户输入自然语言任务
        ↓
┌───────────────────────────────────────────────────────────────────┐
│ 阶段一: 意图识别与规划                                              │
│  Planner Node → 生成结构化 TaskPlan → HITL 审核与重规划               │
└───────────────────────────────────────────────────────────────────┘
        ↓
┌────────────────────────────────────────────────────────────────┐
│ 阶段二: 受控执行编译                                             │
│  TaskPlan → ToolCallPlan → ToolPolicy / Risk Review              │
└────────────────────────────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────────────────────────┐
│ 阶段三: 自动化执行                                           │
│  ExecutionLoop → Observe → Evaluate → Repair → Trace          │
└─────────────────────────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────────────────────────────┐
│ 阶段四: 结果确认与总结                                             │
│  用户确认结果 → Reflector Node → 总结与可选归档                     │
└─────────────────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 功能 |
|------|------|
| **Planner Node** | 意图识别、计划生成、HITL 审核 |
| **Tool Call Planner** | 将审核后的 TaskPlan 编译为 ToolCallPlan |
| **ExecutionLoop** | 受控 MCP 调用、观察、评估、修复、trace |
| **Risk Review** | 高风险工具调用暂停、批准、拒绝或修改参数 |
| **Reflector Node** | 任务总结和质量评分 |
| **Web UI** | 用户交互界面 | 

### 当前边界

- 真实 QGIS 最小闭环可运行，但复杂 processing 参数自动修复仍需继续打磨。
- RAG 目前作为 `RetrievalContext` 接入执行循环，尚未完成完整参数 schema 校验和前端 evidence panel。
- `SkillDraft` 只生成草稿，不注册、不检索、不自动复用。
- Phase 9/10 demo 是静态复放证据，不代表真实 QGIS 性能 benchmark。

## 🚀 快速开始

### 环境要求

- **Python**: 3.12+
- **PostgreSQL**: 16+ (扩展: PostGIS, pg_trgm)
- **QGIS**: 3.x (用于 MCP Server)
- **Docker**: 用于数据库服务

### 安装步骤

1. **克隆项目**
```bash
git clone https://github.com/your-org/qgis-agentv2.git
cd qgis-agentv2
```

2. **配置环境变量**
```bash
cp .env.example .env
```

编辑 `.env` 文件，填入必需的配置：
```bash
# 数据库配置
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/qgis_db

# LLM API 配置
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL_NAME=deepseek-chat

# Web UI 配置
WEB_UI_PORT=9000
WEB_UI_HOST=0.0.0.0

# 文件上传配置
UPLOAD_DIR=./uploads

```

3. **安装依赖**
```bash
# 使用 uv (推荐)
# uv安装-https://docs.astral.sh/uv/getting-started/installation/
uv sync

# 或使用 pip
pip install -e .
```

4. **启动数据库**
```bash
docker-compose up -d postgres
```

5. **安装 QGIS 插件**
在 QGIS → 插件 → 从 zip 文件安装，将 qgis_mcp_plugin 压缩包路径导入


### 启动项目
#### 初始化数据库表与API文档入库
```bash
uv run scripts/setup_database.py
uv run scripts/import_gdal_docs.py
```

#### 启动 FastAPI 后端
```bash
uv run ui\backend\main.py
```

访问 http://localhost:9000

#### 启动 QGIS MCP Server
```bash
uv run qgis_mcp_server\qgis_mcp_server.py
```

### Phase 0 开发环境检查

Phase 0 的目标是确认当前原型目录结构可继续开发，不要求迁移到 `src/` 布局。

```bash
# Python 导入与语法检查
uv run python -m compileall agent qgis_mcp_server ui/backend scripts

# 默认 smoke 测试：state、graph、health check 单元测试
uv run pytest

# 数据库 smoke 测试（需要先启动并初始化 PostgreSQL）
docker compose up -d postgres
uv run python scripts/setup_database.py
uv run pytest tests/test_database.py -q

# 本地服务健康检查，不会打印 API key
uv run python scripts/health_check.py

# 如果 HTTP MCP wrapper 尚未启动，可临时启动并检查
uv run python scripts/health_check.py --start-http-mcp

# 可选：对 DeepSeek/OpenAI-compatible 配置做一次真实模型调用
uv run python scripts/health_check.py --llm-live
```

健康检查覆盖：

- `.env` 中的 DeepSeek/OpenAI-compatible 配置；
- PostgreSQL / PostGIS 开发数据库；
- QGIS Desktop MCP 插件的 `localhost:9876` socket；
- `QGIS_MCP_SERVER_URL` 指向的 HTTP MCP wrapper，默认 `http://localhost:8000/mcp`。

### 前端构建与使用

#### 方式 A：开发模式（推荐）
```bash
cd ui/frontend
npm install
npm run dev
```

默认地址 http://localhost:5173。
开发服务器会将 `/api` 与 `/shared` 代理到 http://localhost:9000。

#### 方式 B：构建后由后端提供页面（推荐）
```bash
cd ui/frontend
npm install
npm run build
```

将 `ui/frontend/dist/` 里的文件复制到 `ui/backend/frontend/`（需包含 `index.html` 与 `assets/`）。
然后直接访问 http://localhost:9000。

### Phase 9/10 Demo Replay 与 Trace Viewer

Phase 9/10 提供可在本地和 CI 中复放的静态 demo case，用于检查受控执行系统的 trace、repair 与 SkillDraft 证据。它不依赖真实 QGIS、MCP、PostgreSQL 或 LLM，也不代表真实 QGIS 性能 benchmark。

```bash
# 复放全部 demo case
uv run python scripts/replay_demo_cases.py

# 复放单个 demo case
uv run python scripts/replay_demo_cases.py --case-id failure_repair

# 输出 JSON 结果
uv run python scripts/replay_demo_cases.py --json
```

当前包含 3 个 demo case：

- `simple_success`：单步受控工具调用成功，记录 artifact。
- `failure_repair`：第一次参数错误，随后使用确定性 repair arguments 重试成功。
- `skill_draft_preview`：从成功 trace 生成未注册 `SkillDraft`，并展示变量替换后的复用预览。

启动后端和前端后，可访问 `/trace-demo` 查看只读 trace inspection 页面。后端也提供只读 API：

- `GET /api/demo-cases`
- `GET /api/demo-cases/{case_id}`
- `GET /api/demo-cases/{case_id}/trace`

### 真实最小 E2E

仓库提供一份真实运行说明，用于检查 DeepSeek、QGIS MCP、Web UI 和受控执行链路是否贯通：

[doc/minimal_real_e2e_task.md](doc/minimal_real_e2e_task.md)

推荐先用该说明中的简单图层加载任务验证主链路，再尝试 buffer、投影转换等复杂 processing 任务。



## 💡 使用示例

### 示例 1: 最小真实图层加载

**用户输入**:
```
请在 QGIS 中加载 shared/uploads/phase11_points.geojson 这个矢量点图层，
图层名称使用 phase11_points。
只需要加载图层，不要删除、覆盖或保存工程。
```

**Agent 执行流程**:
1. **Planner Node**: 生成结构化 `TaskPlan`
2. **HITL 审核**: 展示计划，等待用户确认
3. **Tool Call Planner**: 将计划编译为 `add_layer` 调用
4. **ExecutionLoop**: 通过 QGIS MCP 加载图层并记录 `ExecutionTrace`
5. **结果确认**: 用户确认结果是否完成
6. **Reflector Node**: 生成任务总结

### 示例 2: 复杂处理任务（后续验证）

**用户输入**:
```
对 /path/to/dem.tif 进行坡度分析，
输出为 slope.tif，
并使用热力图配色方案渲染。
```

这类任务会涉及 processing 参数、输出路径、渲染样式和截图。当前系统已经有 `ExecutionLoop`、error-driven retrieval 和 repair metadata 的基础，但真实成功率取决于 QGIS MCP 工具、API 参数和 LLM 输出质量，建议在最小图层加载 E2E 通过后再验证。

## 📁 项目结构

```
qgis-agentv2/
├── agent/                      # LangGraph Agent 核心模块
│   ├── graph.py                # 状态图定义
│   ├── state.py                # State 定义
│   ├── llm.py                  # LLM 配置
│   ├── nodes/                  # 节点实现
│   │   ├── planner_node.py     # 规划节点
│   │   ├── executor_node.py    # 受控执行节点
│   │   └── reflector_node.py   # 总结归档节点
│   └── tools/                  # 工具模块
│       ├── retrieval.py        # 检索证据上下文
│       ├── tool_call_planner.py # TaskPlan 到 ToolCallPlan 编译
│       ├── database.py         # 数据库操作
│       └── mcp_client.py       # MCP 通信客户端
├── qgis_mcp_server/            # QGIS MCP Server
│   ├── qgis_mcp_server.py      # MCP 服务器
│   ├── qgis_mcp_plugin/        # QGIS 插件
│   └── rag_mcp.py              # RAG 工具
├── ui/                         # Web UI
│   ├── backend/                # FastAPI 后端
│   │   └── main.py
│   └── frontend/               # 前端资源
│       └── index.html
├── shared/                     # 共享资源
│   ├── uploads/                # 上传文件
│   └── screenshots/            # 执行截图
├── tests/                      # 测试套件
├── doc/                        # 文档
│   ├── PRD.md                  # 产品需求文档
│   ├── qgis_workflow_agent_engineering_plan.md
│   ├── benchmark_notes.md
│   └── minimal_real_e2e_task.md
├── scripts/                    # 脚本工具
├── pyproject.toml              # 项目配置
├── docker-compose.yml          # Docker 配置
└── .env.example                # 环境变量模板
```

## 🔧 技术栈

### 核心框架
- **LangGraph**: Agent 工作流编排
- **LangChain**: LLM 应用框架
- **FastAPI**: Web API 后端框架
- **PostgreSQL**: 状态持久化和文档存储
- **Vue 3**: 前端 UI 框架


### 地理信息
- **QGIS**: 桌面 GIS 软件
- **GDAL**: 地理数据抽象库
- **PyQGIS**: QGIS Python API

### 通信协议
- **MCP (Model Context Protocol)**: Agent 与 QGIS 通信
- **SSE (Server-Sent Events)**: 实时状态推送

## 🤝 贡献指南

欢迎贡献代码、报告问题或提出建议！

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

### 开发规范
- 遵循 PEP 8 代码规范
- 添加单元测试
- 更新相关文档
- 通过所有测试后提交

## 🙏 致谢

- [LangChain](https://github.com/langchain-ai/langchain)
- [LangGraph](https://github.com/langchain-ai/langgraph)
- [QGIS](https://qgis.org/)
- [GDAL](https://gdal.org/)
- [QGIS-MCP](https://github.com/jjsantos01/qgis_mcp)
