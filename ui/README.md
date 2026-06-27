# QGIS Agent Web UI

基于 LangGraph 的 QGIS 地理数据处理 Agent 的 Web 界面。

## 功能特性

- 🌐 **Web 界面**: 现代化的暗色主题界面
- 💬 **实时聊天**: SSE 流式推送 Agent 执行状态
- 👤 **人工审核 (HITL)**: 在 Agent 执行关键步骤前进行人工审核
- 📁 **文件上传**: 支持上传地理数据文件
- 📊 **节点监控**: 实时显示 Agent 当前执行的节点
- 💾 **会话管理**: 多会话支持，历史记录持久化

## 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web Browser                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   Chat UI    │  │ Node Monitor │  │   Review Modal       │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                  │                     │              │
│         └──────────────────┴─────────────────────┘              │
│                            │                                    │
│                            ▼                                    │
│                    SSE / HTTP API                              │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   Chat API   │  │  Upload API  │  │   Review API         │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                  │                     │              │
│         └──────────────────┴─────────────────────┘              │
│                            │                                    │
│                            ▼                                    │
│                    LangGraph Agent                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Planner     │  │  API RAG     │  │   Executor           │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                  │                     │              │
│         └──────────────────┴─────────────────────┘              │
│                            │                                    │
│                            ▼                                    │
│                    PostgreSQL Checkpointer                      │
└─────────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 环境准备

确保已安装：
- Python 3.12+
- Docker (用于 PostgreSQL)
- uv (Python 包管理器)

### 2. 配置环境变量

复制 `.env.example` 到 `.env` 并编辑：

```bash
cp .env.example .env
```

关键配置项：
```bash
# 数据库配置
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/qgis_db

# OpenAI API 配置
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1

# Web UI 配置
WEB_UI_PORT=9000
WEB_UI_HOST=0.0.0.0

# 文件上传配置
UPLOAD_DIR=./uploads
MAX_UPLOAD_SIZE=104857600
```

### 3. 启动服务

**Linux/macOS:**
```bash
bash scripts/start_web_ui.sh
```

**Windows:**
```bash
scripts\start_web_ui.bat
```

或手动启动：
```bash
# 1. 启动数据库
docker-compose up -d postgres

# 2. 安装依赖（首次运行）
uv pip install fastapi python-multipart

# 3. 启动服务
cd ui/backend
python main.py
```

### 4. 访问界面

打开浏览器访问: http://localhost:9000

## API 文档

启动服务后访问:
- Swagger UI: http://localhost:9000/docs
- ReDoc: http://localhost:9000/redoc

### 主要 API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/sessions` | POST | 创建新会话 |
| `/api/sessions` | GET | 获取会话列表 |
| `/api/sessions/{thread_id}/history` | GET | 获取会话历史 |
| `/api/chat/stream` | POST | 流式聊天 (SSE) |
| `/api/review` | POST | 人工审核 |
| `/api/upload` | POST | 文件上传 |
| `/api/files` | GET | 文件列表 |

## 人工审核 (HITL) 流程

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Planner   │────▶│  Draft 生成 │────▶│  暂停等待审核 │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                              │
                   ┌──────────────────────────┘
                   │
                   ▼
            ┌─────────────┐
            │  审核弹窗   │
            │  显示计划   │
            └──────┬──────┘
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
   ┌─────────┐          ┌─────────┐
   │  批准   │          │  拒绝   │
   └────┬────┘          └────┬────┘
        │                    │
        ▼                    ▼
   ┌─────────┐          ┌─────────┐
   │ API RAG │          │ 重新规划 │
   └─────────┘          └─────────┘
```

## 项目结构

```
ui/
├── backend/
│   ├── __init__.py
│   ├── main.py              # FastAPI 主应用
│   └── frontend/
│       └── index.html       # 前端单页面应用
scripts/
├── start_web_ui.sh          # Linux/macOS 启动脚本
└── start_web_ui.bat         # Windows 启动脚本
agent/                       # LangGraph Agent 模块
├── graph.py                 # Graph 定义
├── state.py                 # State 定义
├── nodes/                   # 节点实现
└── tools/                   # 工具模块
```

## 技术栈

### 后端
- **FastAPI**: 异步 Web 框架
- **LangGraph**: Agent 工作流框架
- **PostgreSQL**: 状态持久化
- **SSE**: 服务器推送事件

### 前端
- **原生 HTML5/CSS3/JavaScript**: 无框架依赖
- **SSE**: 实时事件接收
- **暗色主题**: 现代化 UI 设计

## 开发说明

### 添加新节点

1. 在 `agent/nodes/` 创建节点函数
2. 在 `agent/graph.py` 注册节点和边
3. 在前端 `getNodeDisplayName()` 添加显示名称

### 扩展 API

在 `ui/backend/main.py` 添加新路由：

```python
@app.post("/api/your-endpoint")
async def your_endpoint(request: YourRequest):
    # 实现逻辑
    return {"status": "success"}
```

### 自定义前端样式

修改 `ui/backend/frontend/index.html` 中的 CSS 变量：

```css
:root {
    --primary-color: #2563eb;
    --bg-dark: #1e1e2e;
    /* ... */
}
```

## 故障排查

### 数据库连接失败
```bash
# 检查 Docker 容器状态
docker ps | grep qgis_postgres

# 重启数据库
docker-compose restart postgres
```

### 端口被占用
修改 `.env` 中的 `WEB_UI_PORT` 配置。

### Agent 执行卡住
检查 LangSmith 配置和 LLM API 连接。

## License

MIT
