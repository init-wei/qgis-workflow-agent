# QGIS Agent v2

> 基于自然语言交互的 QGIS 自动化 Agent，通过 AI Agent 基于改进的 QGIS-MCP 完成 API 检索与地理数据处理任务

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-green.svg)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## ✨ 核心特性

### 🧠 智能规划与执行
- **自然语言交互**: 用中文描述任务，Agent 自动理解并生成执行计划
- **多阶段工作流**: 意图识别 → RAG 检索 → 自动执行 → 总结与知识沉淀
- **人工审核 (HITL)**: 关键步骤前暂停，等待人工确认后继续
- **上下文管理**: 压缩智能体执行节点信息，使用总结反思节点简短的总结内容作为之前任务的回顾

### 📚 检索证据与受控执行
- **TaskPlan → ToolCallPlan**: 计划审核后编译为明确的 MCP 工具调用序列
- **ExecutionLoop**: 每次工具调用都会生成 `ExecutionTrace`，失败后按错误类型评估、修复或请求人工介入
- **Error-driven Retrieval**: 参数错误、缺文件、执行错误可挂载检索证据，为修复提供上下文

### 🔄 自我进化能力
- **知识闭环**: 从成功的历史任务中自动提炼逻辑并扩充案例库
- **动态 Cookbook**: 随着使用次数增加，对复杂指令的理解能力螺旋式上升
- **质量评分**: 基于人工判断任务完成程度与任务复杂度综合评分

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
│  Planner Node → 基于动态 Cookbook 生成执行计划 → HITL 审核与重规划    │
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
│ 阶段四: 自我进化                                                  │
│  Reflector Node → 基于用户反馈的任务总结 → 质量评分 → 高质量案例归档 │
└─────────────────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 功能 |
|------|------|
| **Planner Node** | 意图识别、计划生成、HITL 审核 |
| **Tool Call Planner** | 将审核后的 TaskPlan 编译为 ToolCallPlan |
| **ExecutionLoop** | 受控 MCP 调用、观察、评估、修复、trace |
| **Reflector Node** | 任务总结、知识归档 | 
| **Web UI** | 用户交互界面 | 

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



## 💡 使用示例

### 示例 1: 图层加载与投影转换

**用户输入**:
```
帮我加载 /path/to/data.shp 这个矢量图层，
并将其投影转换为 EPSG:4326，
然后根据 POPULATION 字段进行分级渲染。
```

**Agent 执行流程**:
1. **Planner Node**: 生成 3 步执行计划
2. **HITL 审核**: 展示计划，等待用户确认
3. **Tool Call Planner**: 将计划编译为 `add_layer` / `execute_processing` 等明确调用
4. **ExecutionLoop**: 执行 MCP 工具并记录 `ExecutionTrace`
5. **Reflector Node**: 根据用户的判断生成任务总结，自动归档到 Cookbook

### 示例 2: 栅格数据处理

**用户输入**:
```
对 /path/to/dem.tif 进行坡度分析，
输出为 slope.tif，
并使用热力图配色方案渲染。
```

**Agent 执行流程**:
1. 检索 GDAL Warp API 文档
2. 生成坡度计算代码
3. 执行并生成截图
4. 归档成功案例

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
│   ├── IMPLEMENTATION_GUIDE.md # 实现指南
│   └── OPTIMIZATION_REPORT.md  # 优化报告
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
