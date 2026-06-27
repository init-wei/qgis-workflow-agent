# QGIS Agent 实施指南

本文档提供了详细的实施步骤，便于AI完成项目代码构建，同时便于人类审核。

## 快速开始

1. **阅读PRD和技术文档**
   - `doc/PRD.md` - 产品需求文档
   - `doc/技术文档.md` - 技术设计文档

2. **查看实施指南**
   - `IMPLEMENTATION_GUIDE.md` - 详细实施步骤文档（本文档）

3. **按阶段实施**
   - 阶段1: 基础设施搭建
   - 阶段2: MCP通信层验证
   - 阶段3: LangGraph核心节点实现
   - 阶段4: State流转与逻辑验证
   - 阶段5: Chainlit UI集成
   - 阶段6: 人工测试

## 项目结构

```
qgis-agentv2/
├── doc/                           # 文档
│   ├── PRD.md
│   └── 技术文档.md
├── IMPLEMENTATION_GUIDE.md         # 实施指南（主要文档）
├── agent/                         # Agent核心逻辑
│   ├── __init__.py
│   ├── state.py                   # State定义
│   ├── graph.py                   # LangGraph状态机
│   ├── nodes/                     # LangGraph节点
│   │   ├── __init__.py
│   │   ├── planner_node.py
│   │   ├── api_rag_node.py
│   │   ├── executor_node.py
│   │   └── reflector_node.py
│   └── tools/                     # Agent工具
│       ├── __init__.py
│       ├── database.py
│       ├── rag.py
│       └── mcp_client.py
├── ui/                            # Chainlit UI
│   ├── __init__.py
│   └── app.py
├── scripts/                       # 脚本
│   ├── setup_database.py
│   ├── import_gdal_docs.py
│   ├── init_filesystem.py
│   ├── test_mcp.py
│   └── test_agent.py
├── shared/                        # 文件系统
│   ├── uploads/
│   ├── outputs/
│   ├── screenshots/
│   ├── logs/
│   └── config/
├── data/                          # 数据文件
│   └── gdal_docs/
├── qgis_mcp_plugin/               # QGIS插件（已完成）
├── src/qgis_mcp/                  # MCP核心代码（已完成）
├── mcp_server.py                  # 统一MCP Server（已完成）
├── pyproject.toml                 # 项目配置
└── README.md                      # 本文件
```

## 前置条件

- Python 3.12+
- Docker 和 Docker Compose
- QGIS 3.X（已安装qgis_mcp_plugin）
- uv包管理器

## 环境配置

1. **安装依赖**
   ```bash
   uv sync
   ```

2. **配置环境变量**
   - 复制环境变量模板：`cp .env.example .env`
   - 根据实际情况修改`.env`文件

3. **启动PostgreSQL（Docker）**
   ```bash
   # 启动PostgreSQL容器
   docker-compose up -d

   # 查看容器日志
   docker-compose logs -f postgres

   # 验证容器状态
   docker-compose ps
   ```

4. **初始化数据库**
   ```bash
   # 创建数据表
   uv run python scripts/setup_database.py
   ```

5. **启动其他服务**
   - QGIS MCP插件（在QGIS中启动）
   - 统一MCP Server: `uv run python mcp_server.py`

## 实施要点

### 1. 简单有效

- 采用最小化设计，避免过度工程
- 优先实现核心功能，后续可迭代优化

### 2. AI友好

- 每个步骤都有明确的输入输出
- 提供完整的代码示例
- 详细的验证标准

### 3. 便于审核

- 代码结构清晰
- 注释完整
- 每个阶段都可以独立测试

## 实施顺序

建议按照IMPLEMENTATION_GUIDE.md中的顺序依次实施：

1. ✅ **阶段1: 基础设施搭建**
   - 使用Docker部署PostgreSQL数据库
   - 创建核心数据表
   - 导入GDAL文档数据
   - 初始化文件系统

2. ✅ **阶段2: MCP通信层验证**
   - 开发capture_map_canvas工具
   - 验证MCP通信

3. ⏳ **阶段3: LangGraph核心节点实现**
   - Planner Node
   - API RAG Node
   - Executor Node
   - Reflector Node

4. ⏳ **阶段4: State流转与逻辑验证**
   - 构建LangGraph状态机
   - 验证完整流程

5. ⏳ **阶段5: Chainlit UI集成**
   - 实现最小UI契约
   - 处理HITL交互

6. ⏳ **阶段6: 人工测试**
   - 真实GIS任务测试
   - 完善错误处理
   - 优化性能

## 常见问题

### Q: 如何处理GDAL文档没有JSON文件的情况？

A: 需要从GDAL官方文档提取。可以参考`src/qgis_mcp/extract_method_by_name.py`的实现逻辑。

### Q: 如何选择LLM模型？

A: 可以根据需求选择：
- `gpt-4o`: 通用任务
- `deepseek-reasoner`: 复杂推理（适合Executor Node）

### Q: 如何调试LangGraph节点？

A: 使用`scripts/test_agent.py`进行测试，查看详细日志输出。

## 贡献指南

1. 按照IMPLEMENTATION_GUIDE.md的步骤实施
2. 每个阶段完成后进行验证
3. 记录遇到的问题和解决方案
4. 更新本文档

## 许可证

MIT License

---

**注意**: 本文档是为AI辅助开发设计的，包含详细的步骤指导和代码示例。人类开发者也可以参考此文档进行项目实施。
