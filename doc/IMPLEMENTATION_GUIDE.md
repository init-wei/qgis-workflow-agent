# QGIS Agent 实施步骤文档

> **目标读者**: AI开发助手
> **文档目的**: 提供详细的、可直接执行的步骤指导，便于AI完成项目代码构建，同时便于人类审核
> **项目特点**: 小型项目，采用最简单有效的方式实施

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈与环境](#2-技术栈与环境)
3. [阶段1: 基础设施搭建](#阶段1-基础设施搭建)
4. [Docker管理命令](#docker管理命令)
5. [阶段2: MCP通信层验证](#阶段2-mcp通信层验证)
6. [阶段3: LangGraph核心节点实现](#阶段3-langgraph核心节点实现)
7. [阶段4: State流转与逻辑验证](#阶段4-state流转与逻辑验证)
8. [阶段5: Chainlit UI集成](#阶段5-chainlit-ui集成)
9. [阶段6: 人工测试](#阶段6-人工测试)
10. [附录: 文件结构与命名规范](#附录-文件结构与命名规范)

---

## 1. 项目概述

### 1.1 项目目标

开发一个基于自然语言交互的QGIS智能代理系统，具备以下核心能力：

- **自然语言驱动**: 用户通过自然语言指令让Agent自动调用QGIS的GDAL和PyQGIS API
- **参数级RAG校验**: 解决LLM"知道调什么API但常写错参数"的痛点
- **自我进化能力**: 从成功的历史任务中自动提炼逻辑并扩充案例库
- **人机协作**: 在关键节点引入Human-in-the-loop机制，确保方案安全性

### 1.2 核心工作流

```
用户输入 → [阶段1: 意图识别与规划] → [阶段2: RAG检索增强]
→ [阶段3: Agent执行] → [阶段4: 反思与归档] → 用户反馈
```

### 1.3 已完成组件

- ✅ QGIS MCP插件 (`qgis_mcp_plugin/`)
- ✅ MCP Server (stdio + HTTP/SSE) (`mcp_server.py`)
- ✅ PyQGIS文档提取工具 (`src/qgis_mcp/extract_method_by_name.py`)
- ✅ QGIS操作MCP工具 (`src/qgis_mcp/qgis_mcp_server.py`)
- ✅ 项目基础依赖配置 (`pyproject.toml`)

### 1.4 待开发组件

- ⏳ PostgreSQL数据库设计与GDAL文档入库
- ⏳ `capture_map_canvas` MCP工具
- ⏳ LangGraph Agent核心节点（4个节点）
- ⏳ Chainlit Web UI
- ⏳ 全流程集成与测试

---

## 2. 技术栈与环境

### 2.1 技术栈列表

| 组件 | 技术选型 | 版本要求 |
|------|---------|---------|
| Python环境 | Python 3.12+ | 必须使用uv管理 |
| 前端交互 | Chainlit | Latest |
| 逻辑编排 | LangGraph | Latest |
| 大模型层 | OpenAI SDK | 兼容GPT-4o/DeepSeek-Coder |
| 数据库 | PostgreSQL | 16+ |
| 数据库扩展 | PostGIS, pgvector, pg_trgm | Latest |
| 通信协议 | MCP over SSE | Python Std |
| 文档解析 | BeautifulSoup4 | 4.12+ |

### 2.2 项目目录结构

```
qgis-agentv2/
├── doc/                           # 文档目录
│   ├── PRD.md                     # 产品需求文档
│   └── 技术文档.md                # 技术设计文档
├── qgis_mcp_plugin/               # QGIS插件（已完成）
│   ├── __init__.py
│   ├── metadata.txt
│   └── qgis_mcp_plugin.py
├── src/
│   └── qgis_mcp/                  # MCP核心代码
│       ├── qgis_mcp_server.py     # QGIS操作MCP工具（已完成）
│       ├── extract_method_by_name.py  # PyQGIS文档提取（已完成）
│       └── qgis_socket_client.py  # Socket客户端（已完成）
├── mcp_server.py                  # 统一HTTP/SSE MCP服务器（已完成）
├── shared/                        # 文件系统（待创建）
│   ├── uploads/{sid}/             # 用户上传文件
│   ├── outputs/{sid}/             # 输出文件
│   ├── screenshots/{sid}/         # 截图
│   ├── logs/                      # 日志
│   └── config/                    # 配置
├── agent/                         # Agent核心逻辑（待创建）
│   ├── __init__.py
│   ├── state.py                   # State定义
│   ├── nodes/                     # LangGraph节点
│   │   ├── __init__.py
│   │   ├── planner_node.py        # 规划器
│   │   ├── api_rag_node.py        # API RAG
│   │   ├── executor_node.py       # 执行器
│   │   └── reflector_node.py      # 反思器
│   ├── graph.py                   # LangGraph状态机
│   └── tools/                     # Agent工具
│       ├── __init__.py
│       ├── database.py            # 数据库工具
│       ├── rag.py                 # RAG检索工具
│       └── mcp_client.py         # MCP客户端工具
├── ui/                            # Chainlit UI（待创建）
│   ├── __init__.py
│   └── app.py                     # Chainlit应用
 ├── data/                          # 数据文件（待创建）
 │   └── gdal_docs/                 # GDAL JSON文档
 ├── scripts/                       # 脚本（待创建）
 │   ├── init_extensions.sql         # PostgreSQL扩展初始化
 │   ├── setup_database.py           # 数据库初始化
 │   ├── import_gdal_docs.py        # GDAL文档导入
 │   ├── init_filesystem.py          # 文件系统初始化
 │   ├── test_mcp.py                # MCP通信测试
 │   └── test_agent.py              # Agent测试
 ├── docker-compose.yml             # Docker Compose配置
 ├── pyproject.toml                 # 项目配置（已有）
 └── README.md                      # 项目说明（已有）
```

### 2.3 环境变量配置

复制环境变量模板并修改：

```bash
# 复制模板
cp .env.example .env

# 编辑.env文件
# 根据实际情况修改配置
```

环境变量说明：

```bash
# PostgreSQL配置（Docker部署）
# 默认用户名、密码、数据库都是postgres
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/qgis_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=qgis_db

# OpenAI配置（用于LLM）
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1  # 或使用DeepSeek等兼容接口

# QGIS MCP配置
QGIS_MCP_SERVER_URL=http://localhost:8000

# 文件路径配置
SHARED_ROOT_DIR=./shared

# GDAL文档目录
GDAL_DOCS_DIR=./data/gdal_docs

# Chainlit配置
CHAINLIT_PORT=8500
```

---

## 阶段1: 基础设施搭建

### 目标

- 搭建PostgreSQL数据库环境
- 创建核心数据表并建立索引
- 导入GDAL文档数据
- 初始化文件系统目录结构

### 步骤1.1: 使用Docker部署PostgreSQL

**任务**: 使用Docker Compose部署PostgreSQL 16+并启用必要扩展

**创建文件**: `docker-compose.yml`

```yaml
version: '3.8'

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: qgis_postgres
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: qgis_db
      # 允许所有IP连接（开发环境）
      POSTGRES_HOST_AUTH_METHOD: trust
    ports:
      - "5432:5432"
    volumes:
      # 数据持久化
      - postgres_data:/var/lib/postgresql/data
      # 初始化脚本
      - ./scripts/init_extensions.sql:/docker-entrypoint-initdb.d/init_extensions.sql
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
    driver: local
```

**创建文件**: `scripts/init_extensions.sql`

```sql
-- PostgreSQL扩展初始化脚本
-- 该文件会在容器启动时自动执行

-- 连接到qgis数据库
\c qgis_db;

-- 启用PostGIS扩展（空间数据支持）
CREATE EXTENSION IF NOT EXISTS postgis;

-- 启用pgvector扩展（向量相似度搜索）
CREATE EXTENSION IF NOT EXISTS vector;

-- 启用pg_trgm扩展（三元组文本匹配）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 验证扩展是否安装成功
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('postgis', 'vector', 'pg_trgm');
```

**操作步骤**:

1. **启动Docker Compose**
   ```bash
   # 启动PostgreSQL容器
   docker-compose up -d

   # 查看容器日志
   docker-compose logs -f postgres

   # 查看容器状态
   docker-compose ps
   ```

2. **验证容器是否正常运行**
   ```bash
   # 检查容器状态
   docker ps | grep qgis_postgres

   # 连接到PostgreSQL
   docker exec -it qgis_postgres psql -U postgres -d qgis_db

   # 验证扩展
   \c qgis_db
   \dx
   ```

3. **停止容器（如需要）**
   ```bash
   # 停止容器
   docker-compose down

   # 停止并删除数据卷（慎用！）
   docker-compose down -v
   ```

**验证标准**:
- Docker容器正常运行（`docker ps`可以看到qgis_postgres容器）
- 能够通过`docker exec`连接到数据库
- 三个扩展都已成功安装

**环境变量配置**:

在`.env`文件中配置：

```bash
# PostgreSQL配置（Docker部署）
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/qgis_db
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=qgis_db
```

---

### 步骤1.2: 创建数据库表结构

**任务**: 创建rag_docs、cookbook、execution_logs三张核心表及索引

**创建文件**: `scripts/setup_database.py`

```python
#!/usr/bin/env python3
"""
PostgreSQL数据库初始化脚本
创建rag_docs、cookbook、execution_logs三张核心表
"""

import psycopg
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/qgis_db")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)

def create_tables():
    """创建所有数据表"""

    with pool.connection() as conn:
        with conn.cursor() as cur:

            # 1. 创建rag_docs表（GDAL知识库）
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_docs (
                    id SERIAL PRIMARY KEY,

                    -- 基础元数据
                    library_name VARCHAR(50) DEFAULT 'GDAL',
                    class_name VARCHAR(100),
                    method_name VARCHAR(255),

                    -- 核心内容
                    description TEXT,
                    parameters JSONB,
                    example_code TEXT,

                    -- 检索优化
                    search_vector TSVECTOR,
                    embedding VECTOR(1536),

                    -- 元数据
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );

                -- 创建全文检索索引
                CREATE INDEX IF NOT EXISTS idx_rag_docs_search ON rag_docs USING GIN(search_vector);

                -- 创建向量检索索引
                CREATE INDEX IF NOT EXISTS idx_rag_docs_embedding ON rag_docs USING ivfflat(embedding vector_cosine_ops);

                -- 创建唯一约束，防止重复
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_docs_unique_method
                ON rag_docs (library_name, method_name);
            """)

            # 2. 创建cookbook表（动态案例库）
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cookbook (
                    id SERIAL PRIMARY KEY,

                    -- 核心内容
                    user_intent TEXT NOT NULL,
                    verified_code TEXT NOT NULL,

                    -- 辅助字段
                    tags TEXT[],
                    complexity_score FLOAT DEFAULT 0.5,
                    usage_count INT DEFAULT 1,

                    -- 元数据
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );

                -- 创建全文检索索引
                CREATE INDEX IF NOT EXISTS idx_cookbook_intent ON cookbook USING GIN(to_tsvector('english', user_intent));

                -- 创建标签索引
                CREATE INDEX IF NOT EXISTS idx_cookbook_tags ON cookbook USING GIN(tags);
            """)

            # 3. 创建execution_logs表（执行日志）
            cur.execute("""
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id SERIAL PRIMARY KEY,

                    -- 核心内容
                    session_id VARCHAR(100) NOT NULL,
                    step_id INT,
                    message TEXT NOT NULL,
                    status VARCHAR(20) NOT NULL,  -- 'running' | 'success' | 'failed'

                    -- 元数据
                    timestamp TIMESTAMP DEFAULT NOW()
                );

                -- 创建时间索引
                CREATE INDEX IF NOT EXISTS idx_execution_logs_timestamp ON execution_logs(timestamp DESC);

                -- 创建会话索引
                CREATE INDEX IF NOT EXISTS idx_execution_logs_session ON execution_logs(session_id);
            """)

            conn.commit()
            print("✅ 数据库表创建成功")

def create_update_trigger():
    """创建自动更新updated_at字段的触发器"""

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE OR REPLACE FUNCTION update_updated_at_column()
                RETURNS TRIGGER AS $$
                BEGIN
                    NEW.updated_at = NOW();
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                -- 为rag_docs表添加触发器
                DROP TRIGGER IF EXISTS update_rag_docs_updated_at ON rag_docs;
                CREATE TRIGGER update_rag_docs_updated_at
                BEFORE UPDATE ON rag_docs
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

                -- 为cookbook表添加触发器
                DROP TRIGGER IF EXISTS update_cookbook_updated_at ON cookbook;
                CREATE TRIGGER update_cookbook_updated_at
                BEFORE UPDATE ON cookbook
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
            """)

            conn.commit()
            print("✅ 触发器创建成功")

if __name__ == "__main__":
    print("开始初始化数据库...")
    create_tables()
    create_update_trigger()
    print("数据库初始化完成！")
```

**执行脚本**:

```bash
# 确保已安装依赖
uv add psycopg[binary] psycopg-pool python-dotenv

# 执行初始化脚本
uv run python scripts/setup_database.py
```

**验证标准**:
- 脚本执行无报错
- 连接数据库后能看到3张表：rag_docs、cookbook、execution_logs
- 每张表都有相应的索引

---

### 步骤1.3: 创建文件系统目录结构

**任务**: 创建shared目录及其子目录，实现会话隔离

**创建文件**: `scripts/init_filesystem.py`

```python
#!/usr/bin/env python3
"""
文件系统初始化脚本
创建shared目录结构
"""

import os
from pathlib import Path

# 配置
SHARED_ROOT_DIR = os.getenv("SHARED_ROOT_DIR", "./shared")

# 目录结构
DIRECTORIES = [
    "uploads",        # 上传文件
    "outputs",       # 输出文件
    "screenshots",   # 截图
    "logs",          # 日志
    "config",        # 配置
]

def create_directory_structure():
    """创建目录结构"""

    root = Path(SHARED_ROOT_DIR)

    # 创建根目录
    root.mkdir(parents=True, exist_ok=True)
    print(f"✅ 创建根目录: {root}")

    # 创建子目录
    for dir_name in DIRECTORIES:
        dir_path = root / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"✅ 创建目录: {dir_path}")

    # 创建.gitignore文件
    gitignore_path = root / ".gitignore"
    gitignore_content = """# 忽略上传的文件（示例数据）
uploads/*/original/*
uploads/*/processed/*

# 保留输出文件
!outputs/*/.gitkeep

# 忽略截图文件（可选，如果不想提交）
screenshots/*/*

# 忽略日志文件
logs/*.log

# 忽略临时文件
*.tmp
*.bak
"""
    gitignore_path.write_text(gitignore_content)
    print(f"✅ 创建.gitignore: {gitignore_path}")

    # 创建.gitkeep文件（确保空目录被git跟踪）
    for dir_name in DIRECTORIES:
        dir_path = root / dir_name
        gitkeep_path = dir_path / ".gitkeep"
        gitkeep_path.touch()
        print(f"✅ 创建.gitkeep: {gitkeep_path}")

    print("\n文件系统初始化完成！")
    print(f"根目录: {root}")

if __name__ == "__main__":
    print("开始初始化文件系统...")
    create_directory_structure()
```

**执行脚本**:

```bash
uv run python scripts/init_filesystem.py
```

**验证标准**:
- shared目录下有5个子目录：uploads、outputs、screenshots、logs、config
- 每个子目录下都有.gitkeep文件

---

### 步骤1.4: 导入GDAL文档数据

**任务**: 将现有的GDAL JSON文档导入rag_docs表

**前提条件**:
- 需要准备好GDAL文档的JSON文件（假设在`data/gdal_docs/`目录下）
- 如果没有JSON文件，需要从GDAL官方文档提取

**创建文件**: `scripts/import_gdal_docs.py`

```python
#!/usr/bin/env python3
"""
GDAL文档导入脚本
将GDAL JSON文档导入rag_docs表
"""

import json
import os
from pathlib import Path
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from dotenv import load_dotenv
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/qgis_db")

# GDAL文档目录
GDAL_DOCS_DIR = os.getenv("GDAL_DOCS_DIR", "./data/gdal_docs")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)

def load_gdal_docs(directory):
    """加载GDAL文档JSON文件"""

    docs = []
    docs_path = Path(directory)

    if not docs_path.exists():
        logger.warning(f"GDAL文档目录不存在: {docs_path}")
        return docs

    # 查找所有JSON文件
    json_files = list(docs_path.glob("*.json"))

    if not json_files:
        logger.warning(f"未找到JSON文件: {docs_path}")
        return docs

    # 读取所有JSON文件
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # 如果是数组，展开每个文档
                if isinstance(data, list):
                    docs.extend(data)
                else:
                    docs.append(data)

            logger.info(f"✅ 读取文件: {json_file} ({len(data) if isinstance(data, list) else 1} 条记录)")

        except Exception as e:
            logger.error(f"❌ 读取文件失败 {json_file}: {e}")

    logger.info(f"总共加载 {len(docs)} 条GDAL文档")
    return docs

def import_gdal_docs_to_database(docs):
    """将GDAL文档导入数据库"""

    if not docs:
        logger.warning("没有文档可导入")
        return

    with pool.connection() as conn:
        with conn.cursor() as cur:
            imported_count = 0
            skipped_count = 0

            for doc in docs:
                try:
                    # 提取字段
                    library_name = doc.get("library_name", "GDAL")
                    class_name = doc.get("classname", "")
                    method_name = doc.get("method", "")
                    description = doc.get("description", "")
                    parameters = doc.get("parameters", [])
                    example_code = doc.get("example", "")

                    # 构建全文检索向量
                    # 权重: classname + method_name = A级, description = B级
                    search_terms = f"{class_name} {method_name} {description}"
                    cur.execute("""
                        SELECT to_tsvector('english', %s)
                    """, (search_terms,))
                    search_vector = cur.fetchone()[0]

                    # 插入或更新记录（使用ON CONFLICT处理重复）
                    cur.execute("""
                        INSERT INTO rag_docs (
                            library_name, class_name, method_name,
                            description, parameters, example_code,
                            search_vector
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (library_name, method_name)
                        DO UPDATE SET
                            description = EXCLUDED.description,
                            parameters = EXCLUDED.parameters,
                            example_code = EXCLUDED.example_code,
                            search_vector = EXCLUDED.search_vector,
                            updated_at = NOW()
                    """, (
                        library_name, class_name, method_name,
                        description, json.dumps(parameters), example_code,
                        search_vector
                    ))

                    imported_count += 1

                    if imported_count % 100 == 0:
                        conn.commit()
                        logger.info(f"已导入 {imported_count} 条记录...")

                except Exception as e:
                    logger.error(f"❌ 导入失败 {doc.get('method', 'unknown')}: {e}")
                    skipped_count += 1
                    continue

            conn.commit()
            logger.info(f"✅ 导入完成: {imported_count} 条成功, {skipped_count} 条跳过")

def verify_import():
    """验证导入结果"""

    with pool.connection() as conn:
        with conn.cursor() as cur:
            # 统计记录数
            cur.execute("SELECT COUNT(*) FROM rag_docs")
            total_count = cur.fetchone()[0]

            # 按class统计
            cur.execute("""
                SELECT class_name, COUNT(*) as count
                FROM rag_docs
                GROUP BY class_name
                ORDER BY count DESC
                LIMIT 10
            """)
            class_stats = cur.fetchall()

            # 查看样例记录
            cur.execute("SELECT * FROM rag_docs LIMIT 3")
            sample_docs = cur.fetchall()

            print(f"\n📊 导入统计:")
            print(f"总记录数: {total_count}")
            print(f"\n按类统计:")
            for class_name, count in class_stats:
                print(f"  {class_name}: {count}")

            print(f"\n样例记录:")
            for doc in sample_docs:
                print(f"  - {doc['method_name']}: {doc['description'][:50]}...")

if __name__ == "__main__":
    print("开始导入GDAL文档...")

    # 加载文档
    docs = load_gdal_docs(GDAL_DOCS_DIR)

    # 导入数据库
    import_gdal_docs_to_database(docs)

    # 验证结果
    verify_import()

    print("GDAL文档导入完成！")
```

**执行脚本**:

```bash
# 确保GDAL文档目录存在
mkdir -p data/gdal_docs

# （如果没有JSON文件，需要先提取）
# 这里假设已经有了JSON文件

# 执行导入脚本
uv run python scripts/import_gdal_docs.py
```

**验证标准**:
- 脚本执行无严重错误
- rag_docs表中有记录（>0条）
- 执行verify_import()能看到统计信息

---

### 附录: Docker管理命令

本附录提供了常用的Docker管理命令，用于维护PostgreSQL容器。

#### 查看容器状态

```bash
# 查看运行中的容器
docker ps

# 查看所有容器（包括停止的）
docker ps -a

# 查看容器详细信息
docker inspect qgis_postgres
```

#### 查看日志

```bash
# 查看实时日志
docker-compose logs -f postgres

# 查看最近100行日志
docker-compose logs --tail=100 postgres

# 查看容器内部日志
docker logs qgis_postgres
```

#### 进入容器

```bash
# 进入容器的Shell
docker exec -it qgis_postgres bash

# 直接连接到PostgreSQL
docker exec -it qgis_postgres psql -U postgres -d qgis_db
```

#### 备份与恢复

```bash
# 备份数据库
docker exec qgis_postgres pg_dump -U postgres qgis_db > backup_$(date +%Y%m%d_%H%M%S).sql

# 恢复数据库
docker exec -i qgis_postgres psql -U postgres qgis_db < backup.sql
```

#### 重启容器

```bash
# 重启容器
docker-compose restart postgres

# 停止并启动容器
docker-compose down && docker-compose up -d
```

#### 清理

```bash
# 停止并删除容器
docker-compose down

# 停止并删除容器及数据卷（慎用！）
docker-compose down -v

# 清理未使用的Docker资源
docker system prune
```

#### 故障排查

```bash
# 检查容器健康状态
docker inspect qgis_postgres | grep -A 10 Health

# 检查容器资源使用
docker stats qgis_postgres

# 检查网络连接
docker network inspect qgis-agentv2_default

# 测试数据库连接
docker exec qgis_postgres pg_isready -U postgres
```

---

## 阶段2: MCP通信层验证

### 目标

- 开发`capture_map_canvas` MCP工具
- 验证MCP Client ↔ MCP Server通信（SSE连接）
- 确保Agent能成功驱动QGIS执行代码并获取反馈

---

### 步骤2.1: 开发capture_map_canvas MCP工具

**任务**: 在QGIS MCP插件中添加截图工具

**修改文件**: `qgis_mcp_plugin/qgis_mcp_plugin.py`

在现有的工具列表中添加：

```python
@qgis_mcp.tool()
def capture_map_canvas(ctx: Context, path: str, width: int = 800, height: int = 600) -> str:
    """
    Capture the current QGIS map canvas to an image file.

    Args:
        path: Full path to save the screenshot image (e.g., '/path/to/screenshot.png')
        width: Image width in pixels (default: 800)
        height: Image height in pixels (default: 600)

    Returns:
        JSON string with success status and file path
    """
    try:
        # 获取当前画布
        canvas = iface.mapCanvas()

        # 设置尺寸
        if width > 0 and height > 0:
            canvas.resize(width, height)

        # 保存截图
        canvas.saveAsImage(path)

        return json.dumps({
            "success": True,
            "path": path,
            "message": f"Screenshot saved to {path}"
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e)
        }, indent=2)
```

**说明**:
- 使用iface.mapCanvas()获取当前画布
- 调用saveAsImage()保存截图
- 返回JSON格式的结果

**验证标准**:
- 重新加载QGIS插件后，MCP Server能识别该工具
- 调用工具能成功保存截图

---

### 步骤2.2: 在MCP Server中注册capture_map_canvas工具

**任务**: 将capture_map_canvas工具注册到统一MCP Server

**修改文件**: `mcp_server.py`

在`@mcp.tool()`装饰器部分添加：

```python
@mcp.tool()
def capture_map_canvas(ctx: Context, path: str, width: int = 800, height: int = 600) -> str:
    """
    Capture the current QGIS map canvas to an image file.

    Args:
        path: Full path to save the screenshot image (e.g., 'C:/shared/screenshots/session_id/map_canvas_1234567890.png')
        width: Image width in pixels (default: 800)
        height: Image height in pixels (default: 600)

    Returns:
        JSON string with success status and file path
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("capture_map_canvas", {
        "path": path,
        "width": width,
        "height": height
    })
    return json.dumps(result, indent=2)
```

**验证标准**:
- 重新启动MCP Server后，工具列表中包含capture_map_canvas
- 通过HTTP/SSE接口能调用该工具

---

### 步骤2.3: 编写MCP通信测试脚本

**任务**: 验证MCP Client ↔ MCP Server通信

**创建文件**: `scripts/test_mcp.py`

```python
#!/usr/bin/env python3
"""
MCP通信测试脚本
验证MCP Client与Server的SSE通信
"""

import asyncio
import json
import logging
from typing import Dict, Any

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MCPTestClient:
    """简单的MCP客户端，用于测试"""

    def __init__(self, server_url: str = "http://localhost:8000"):
        self.server_url = server_url
        self.session_id = None
        self.message_queue = asyncio.Queue()

    async def connect_sse(self):
        """建立SSE连接"""

        import aiohttp

        url = f"{self.server_url}/sse"
        logger.info(f"正在连接SSE: {url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"SSE连接失败: {response.status}")

                # 读取SSE事件
                async for line in response.content:
                    if line:
                        line_str = line.decode('utf-8').strip()

                        # 解析SSE事件
                        if line_str.startswith("event:"):
                            event_type = line_str[6:].strip()
                        elif line_str.startswith("data:"):
                            data_str = line_str[5:].strip()

                            # 处理endpoint事件（获取session_id）
                            if event_type == "endpoint":
                                self.session_id = data_str.split("=")[1]
                                logger.info(f"获取到session_id: {self.session_id}")

                            # 处理message事件
                            elif event_type == "message":
                                try:
                                    data = json.loads(data_str)
                                    await self.message_queue.put(data)
                                    logger.info(f"收到消息: {data.get('method', 'unknown')}")
                                except json.JSONDecodeError:
                                    logger.warning(f"无法解析消息: {data_str}")

    async def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """调用MCP工具"""

        import aiohttp

        if not self.session_id:
            raise Exception("未建立SSE连接")

        url = f"{self.server_url}/messages?sessionId={self.session_id}"
        logger.info(f"调用工具: {tool_name}, 参数: {params}")

        # 构建JSON-RPC请求
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": params
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=request) as response:
                if response.status != 200:
                    raise Exception(f"工具调用失败: {response.status}")

                result = await response.json()
                return result

    async def wait_for_response(self, timeout: int = 30) -> Dict[str, Any]:
        """等待工具响应"""

        try:
            result = await asyncio.wait_for(self.message_queue.get(), timeout=timeout)
            return result
        except asyncio.TimeoutError:
            raise Exception(f"等待响应超时 ({timeout}秒)")

async def test_ping():
    """测试ping工具"""

    logger.info("=== 测试1: Ping ===")
    client = MCPTestClient()

    # 启动SSE连接（后台）
    sse_task = asyncio.create_task(client.connect_sse())

    # 等待session_id
    await asyncio.sleep(2)

    # 调用ping工具
    result = await client.call_tool("ping", {})

    logger.info(f"Ping结果: {result}")

    # 等待响应
    response = await client.wait_for_response()
    logger.info(f"响应: {response}")

    # 关闭连接
    sse_task.cancel()

async def test_execute_code():
    """测试execute_code工具"""

    logger.info("=== 测试2: Execute Code ===")
    client = MCPTestClient()

    # 启动SSE连接（后台）
    sse_task = asyncio.create_task(client.connect_sse())

    # 等待session_id
    await asyncio.sleep(2)

    # 调用execute_code工具
    code = """
    print("Hello from QGIS!")
    print(f"QGIS版本: {Qgis.versionString()}")
    """
    result = await client.call_tool("execute_code", {"code": code})

    logger.info(f"Execute Code结果: {result}")

    # 等待响应
    response = await client.wait_for_response(timeout=60)
    logger.info(f"响应: {response}")

    # 关闭连接
    sse_task.cancel()

async def test_capture_map_canvas():
    """测试capture_map_canvas工具"""

    logger.info("=== 测试3: Capture Map Canvas ===")
    client = MCPTestClient()

    # 启动SSE连接（后台）
    sse_task = asyncio.create_task(client.connect_sse())

    # 等待session_id
    await asyncio.sleep(2)

    # 调用capture_map_canvas工具
    import os
    screenshot_path = os.path.join(os.getcwd(), "shared/screenshots/test_capture.png")

    result = await client.call_tool("capture_map_canvas", {
        "path": screenshot_path,
        "width": 800,
        "height": 600
    })

    logger.info(f"Capture Map Canvas结果: {result}")

    # 等待响应
    response = await client.wait_for_response(timeout=30)
    logger.info(f"响应: {response}")

    # 检查文件是否存在
    if os.path.exists(screenshot_path):
        logger.info(f"✅ 截图文件已创建: {screenshot_path}")
    else:
        logger.warning(f"⚠️ 截图文件未创建: {screenshot_path}")

    # 关闭连接
    sse_task.cancel()

async def main():
    """运行所有测试"""

    logger.info("开始MCP通信测试...")

    # 测试1: Ping
    await test_ping()

    await asyncio.sleep(2)

    # 测试2: Execute Code
    await test_execute_code()

    await asyncio.sleep(2)

    # 测试3: Capture Map Canvas
    await test_capture_map_canvas()

    logger.info("MCP通信测试完成！")

if __name__ == "__main__":
    asyncio.run(main())
```

**执行测试**:

```bash
# 安装额外依赖
uv add aiohttp

# 确保QGIS MCP插件已启动
# 确保统一MCP Server已运行：uv run python mcp_server.py

# 执行测试脚本
uv run python scripts/test_mcp.py
```

**验证标准**:
- 测试1（ping）成功
- 测试2（execute_code）能输出QGIS版本信息
- 测试3（capture_map_canvas）能成功保存截图文件

---

## 阶段3: LangGraph核心节点实现

### 目标

- 实现Planner Node（规划器）
- 实现API RAG Node（API RAG检索）
- 实现Executor Node（执行器）
- 实现Reflector Node（反思器）

---

### 步骤3.1: 定义LangGraph State Schema

**任务**: 定义完整的State数据结构

**创建文件**: `agent/state.py`

```python
#!/usr/bin/env python3
"""
LangGraph State定义
包含所有节点共享的状态字段
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class Step(BaseModel):
    """执行步骤"""

    step_id: int = Field(description="步骤ID")
    description: str = Field(description="步骤描述")
    gdal_api: List[str] = Field(default_factory=list, description="涉及的GDAL API")
    pyqgis_api: List[str] = Field(default_factory=list, description="涉及的PyQGIS API")


class Plan(BaseModel):
    """执行计划"""

    task: str = Field(description="任务简述")
    steps: List[Step] = Field(default_factory=list, description="执行步骤列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class RelevantDoc(BaseModel):
    """相关API文档"""

    api_name: str = Field(description="API名称")
    library: str = Field(description="库类型: GDAL or PyQGIS")
    content: str = Field(description="API文档完整内容")


class StepContext(BaseModel):
    """步骤上下文（按步骤分组的API文档）"""

    step_id: int = Field(description="步骤ID")
    relevant_docs: List[RelevantDoc] = Field(default_factory=list, description="相关文档列表")


class AgentState(TypedDict):
    """
    LangGraph State定义
    所有节点共享的状态字段
    """

    # ========== 基础输入 ==========
    input_query: str  # 用户的原始地理处理需求
    session_id: str   # 会话ID（用于数据隔离）

    # ========== Planner Node 字段 ==========
    log_summary: Optional[str]        # 之前轮次的任务执行总结
    advise: Optional[str]             # 用户在审核阶段提出的修改意见
    example: Optional[Dict[str, Any]] # 从Cookbook检索到的相似案例
    draft: Optional[Plan]             # 模型当前生成的草稿方案
    plan: Optional[Plan]              # 最终确认执行的方案
    status: bool                      # 方案审核状态（False=待审核，True=已批准）
    retry_count: int                  # 人工审查/修改的循环次数

    # ========== API RAG Node 字段 ==========
    gdal_doc: List[Dict[str, Any]]   # 检索到的GDAL API文档
    pyqgis_doc: List[Dict[str, Any]] # 检索到的PyQGIS API文档
    api_context_structured: List[StepContext]  # 按步骤分段的结构化API文档
    missing_deps: List[str]           # LLM反思发现的缺失依赖API

    # ========== Executor Node 字段 ==========
    messages: Annotated[list, add_messages]  # 包含AI思考、代码输出及执行结果
    current_step_id: int              # 当前正在执行的步骤索引
    execution_logs: List[Dict[str, Any]]  # 记录每步的成功/失败状态
    screenshot_path: Optional[str]    # QGIS渲染生成的最新截图路径

    # ========== Reflector Node 字段 ==========
    verified_code: Optional[str]      # 经过验证执行成功的核心代码
    is_completed: bool                # 用户认定的任务完成状态
    quality_score: float              # LLM对本次任务的价值评分


# State初始化函数
def create_initial_state(session_id: str, input_query: str) -> AgentState:
    """
    创建初始State

    Args:
        session_id: 会话ID
        input_query: 用户输入

    Returns:
        初始化的AgentState
    """

    return {
        "input_query": input_query,
        "session_id": session_id,

        # Planner Node
        "log_summary": None,
        "advise": None,
        "example": None,
        "draft": None,
        "plan": None,
        "status": False,
        "retry_count": 0,

        # API RAG Node
        "gdal_doc": [],
        "pyqgis_doc": [],
        "api_context_structured": [],
        "missing_deps": [],

        # Executor Node
        "messages": [],
        "current_step_id": 0,
        "execution_logs": [],
        "screenshot_path": None,

        # Reflector Node
        "verified_code": None,
        "is_completed": False,
        "quality_score": 0.0,
    }
```

---

### 步骤3.2: 创建Agent工具模块

**任务**: 创建数据库、RAG、MCP客户端等工具

#### 3.2.1 创建数据库工具

**创建文件**: `agent/tools/database.py`

```python
#!/usr/bin/env python3
"""
数据库工具模块
提供数据库连接和查询功能
"""

import os
from typing import List, Dict, Any, Optional
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from dotenv import load_dotenv
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/qgis_db")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)


def save_execution_log(session_id: str, step_id: Optional[int], message: str, status: str):
    """
    保存执行日志

    Args:
        session_id: 会话ID
        step_id: 步骤ID（可选）
        message: 日志消息
        status: 状态 ('running' | 'success' | 'failed')
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO execution_logs (session_id, step_id, message, status)
                    VALUES (%s, %s, %s, %s)
                """, (session_id, step_id, message, status))
                conn.commit()

                logger.info(f"✅ 保存执行日志: session={session_id}, step={step_id}, status={status}")

    except Exception as e:
        logger.error(f"❌ 保存执行日志失败: {e}")


def get_execution_logs(session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    获取执行日志

    Args:
        session_id: 会话ID
        limit: 最多返回的日志条数

    Returns:
        日志列表
    """

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    SELECT * FROM execution_logs
                    WHERE session_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (session_id, limit))

                logs = cur.fetchall()
                return logs

    except Exception as e:
        logger.error(f"❌ 获取执行日志失败: {e}")
        return []


def save_cookbook_entry(user_intent: str, verified_code: str, tags: List[str] = None,
                       complexity_score: float = 0.5):
    """
    保存Cookbook案例

    Args:
        user_intent: 用户意图
        verified_code: 验证通过的代码
        tags: 标签列表
        complexity_score: 复杂度评分（0.0-1.0）
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cookbook (user_intent, verified_code, tags, complexity_score)
                    VALUES (%s, %s, %s, %s)
                """, (user_intent, verified_code, tags or [], complexity_score))
                conn.commit()

                logger.info(f"✅ 保存Cookbook案例: {user_intent[:50]}...")

    except Exception as e:
        logger.error(f"❌ 保存Cookbook案例失败: {e}")
```

#### 3.2.2 创建RAG检索工具

**创建文件**: `agent/tools/rag.py`

```python
#!/usr/bin/env python3
"""
RAG检索工具模块
提供GDAL和Cookbook的检索功能
"""

import os
import json
from typing import List, Dict, Any, Optional
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from dotenv import load_dotenv
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/qgis_db")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)


def search_gdal_docs(method_names: List[str]) -> List[Dict[str, Any]]:
    """
    搜索GDAL API文档

    Args:
        method_names: 方法名列表

    Returns:
        GDAL文档列表
    """

    if not method_names:
        return []

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 构建查询
                placeholders = ', '.join(['%s'] * len(method_names))
                cur.execute(f"""
                    SELECT * FROM rag_docs
                    WHERE library_name = 'GDAL'
                    AND method_name = ANY(ARRAY[{placeholders}])
                """, method_names)

                docs = cur.fetchall()
                logger.info(f"✅ 检索到 {len(docs)} 条GDAL文档")
                return docs

    except Exception as e:
        logger.error(f"❌ 搜索GDAL文档失败: {e}")
        return []


def search_cookbook(user_intent: str, similarity_threshold: float = 0.8, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    搜索Cookbook相似案例

    Args:
        user_intent: 用户意图
        similarity_threshold: 相似度阈值
        top_k: 最多返回的案例数

    Returns:
        相似案例列表
    """

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 使用全文检索（BM25）
                cur.execute("""
                    SELECT *,
                           ts_rank_cd(to_tsvector('english', user_intent), plainto_tsquery('english', %s)) as rank
                    FROM cookbook
                    WHERE to_tsvector('english', user_intent) @@ plainto_tsquery('english', %s)
                    ORDER BY rank DESC
                    LIMIT %s
                """, (user_intent, user_intent, top_k))

                cases = cur.fetchall()

                # 过滤相似度（简单版：只看是否匹配）
                # 更精确的相似度计算需要向量化+余弦相似度
                filtered_cases = [c for c in cases if c['rank'] > 0.1]

                logger.info(f"✅ 检索到 {len(filtered_cases)} 条Cookbook案例")
                return filtered_cases

    except Exception as e:
        logger.error(f"❌ 搜索Cookbook失败: {e}")
        return []


def search_pyqgis_docs(method_names: List[str]) -> List[Dict[str, Any]]:
    """
    搜索PyQGIS API文档（通过MCP工具）

    Args:
        method_names: 方法名列表

    Returns:
        PyQGIS文档列表
    """

    # 注意：这里需要通过MCP客户端调用QGIS MCP Server的extract_qgis_method工具
    # 简化实现：返回空列表，实际调用在executor_node中实现
    logger.info(f"⚠️ PyQGIS文档检索需要MCP客户端，暂未实现")
    return []
```

#### 3.2.3 创建MCP客户端工具

**创建文件**: `agent/tools/mcp_client.py`

```python
#!/usr/bin/env python3
"""
MCP客户端工具模块
提供与QGIS MCP Server的通信功能
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List
import aiohttp
import asyncio
from dotenv import load_dotenv

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# QGIS MCP Server URL
QGIS_MCP_SERVER_URL = os.getenv("QGIS_MCP_SERVER_URL", "http://localhost:8000")


class QGISMCPClient:
    """QGIS MCP客户端"""

    def __init__(self, server_url: str = QGIS_MCP_SERVER_URL):
        self.server_url = server_url
        self.session_id = None
        self.message_queue = asyncio.Queue()
        self.http_session = None

    async def connect_sse(self) -> str:
        """建立SSE连接，返回session_id"""

        url = f"{self.server_url}/sse"
        logger.info(f"正在连接SSE: {url}")

        self.http_session = aiohttp.ClientSession()

        async with self.http_session.get(url) as response:
            if response.status != 200:
                raise Exception(f"SSE连接失败: {response.status}")

            # 读取SSE事件
            async for line in response.content:
                if line:
                    line_str = line.decode('utf-8').strip()

                    # 解析SSE事件
                    if line_str.startswith("event:"):
                        event_type = line_str[6:].strip()
                    elif line_str.startswith("data:"):
                        data_str = line_str[5:].strip()

                        # 处理endpoint事件（获取session_id）
                        if event_type == "endpoint":
                            self.session_id = data_str.split("=")[1]
                            logger.info(f"获取到session_id: {self.session_id}")
                            return self.session_id

                        # 处理message事件
                        elif event_type == "message":
                            try:
                                data = json.loads(data_str)
                                await self.message_queue.put(data)
                                logger.info(f"收到消息: {data.get('method', 'unknown')}")
                            except json.JSONDecodeError:
                                logger.warning(f"无法解析消息: {data_str}")

    async def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用MCP工具

        Args:
            tool_name: 工具名称
            params: 工具参数

        Returns:
            工具调用结果
        """

        if not self.session_id:
            raise Exception("未建立SSE连接，请先调用connect_sse()")

        url = f"{self.server_url}/messages?sessionId={self.session_id}"
        logger.info(f"调用工具: {tool_name}, 参数: {params}")

        # 构建JSON-RPC请求
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": params
            }
        }

        async with self.http_session.post(url, json=request) as response:
            if response.status != 200:
                raise Exception(f"工具调用失败: {response.status}")

            result = await response.json()
            return result

    async def wait_for_response(self, timeout: int = 30) -> Dict[str, Any]:
        """
        等待工具响应

        Args:
            timeout: 超时时间（秒）

        Returns:
            响应数据
        """

        try:
            result = await asyncio.wait_for(self.message_queue.get(), timeout=timeout)
            return result
        except asyncio.TimeoutError:
            raise Exception(f"等待响应超时 ({timeout}秒)")

    async def close(self):
        """关闭连接"""

        if self.http_session:
            await self.http_session.close()
            logger.info("MCP客户端已关闭")


# 全局MCP客户端实例（简化版）
_mcp_client: Optional[QGISMCPClient] = None


async def get_mcp_client() -> QGISMCPClient:
    """
    获取全局MCP客户端（单例模式）

    Returns:
        QGISMCPClient实例
    """

    global _mcp_client

    if _mcp_client is None:
        _mcp_client = QGISMCPClient()
        await _mcp_client.connect_sse()

    return _mcp_client
```

---

### 步骤3.3: 实现Planner Node

**任务**: 实现意图识别与规划节点

**创建文件**: `agent/nodes/planner_node.py`

```python
#!/usr/bin/env python3
"""
Planner Node - 规划器节点
负责意图识别、Cookbook检索、生成执行计划
"""

from typing import Dict, Any
import logging

from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI

from agent.state import AgentState, Plan, Step
from agent.tools.rag import search_cookbook
from agent.tools.database import save_execution_log

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建LLM实例
llm = ChatOpenAI(
    model="gpt-4o",  # 或使用deepseek等兼容模型
    temperature=0.1
)


def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    Planner Node逻辑
    1. 意图解析与查询重写
    2. 混合检索（Cookbook）
    3. 草稿生成
    4. 返回草稿，等待人工审核

    Args:
        state: 当前状态

    Returns:
        更新后的状态
    """

    input_query = state['input_query']
    log_summary = state.get('log_summary')
    advise = state.get('advise')
    retry_count = state.get('retry_count', 0)
    session_id = state['session_id']

    logger.info(f"=== Planner Node === (retry_count={retry_count})")
    logger.info(f"用户输入: {input_query}")

    # 步骤1: 混合检索（Cookbook）
    example = search_cookbook(input_query, similarity_threshold=0.8, top_k=3)

    if example:
        logger.info(f"检索到 {len(example)} 个相似案例")
    else:
        logger.info("未检索到相似案例，依赖LLM自主规划")

    # 步骤2: 构建提示词
    # 包含：用户输入 + 之前总结 + 修改意见 + 相似案例
    context_parts = []

    context_parts.append(f"用户需求: {input_query}")

    if log_summary:
        context_parts.append(f"\n之前的任务总结:\n{log_summary}")

    if advise:
        context_parts.append(f"\n用户修改意见:\n{advise}")

    if example:
        context_parts.append("\n相似案例参考:")
        for idx, case in enumerate(example[:3], 1):
            context_parts.append(f"\n案例{idx}:")
            context_parts.append(f"用户意图: {case['user_intent']}")
            context_parts.append(f"解决方案代码:\n{case['verified_code']}")

    system_prompt = """
你是一个专业的QGIS地理数据处理专家。你的任务是将用户的自然语言需求转化为可执行的QGIS操作步骤。

## 输出格式
请以JSON格式输出执行计划，包含以下字段：

{
  "task": "任务简述（例如: Align raster and vector layers）",
  "steps": [
    {
      "step_id": 1,
      "description": "步骤描述（例如: Reproject raster）",
      "gdal_api": ["gdal.Warp", ...],
      "pyqgis_api": ["QgsCoordinateReferenceSystem", ...]
    },
    ...
  ],
  "metadata": {
    "iteration": 迭代次数,
    "has_example_reference": 是否有案例参考
  }
}

## 注意事项
1. 步骤不要拆分太细，每个步骤应该是中低复杂度的目标
2. gdal_api和pyqgis_api列出所有可能用到的API名称（不需要参数细节）
3. 参考相似案例可以提高准确性
4. 如果有相似案例，尽量借鉴其代码逻辑
"""

    user_prompt = "\n".join(context_parts)

    # 步骤3: 调用LLM生成计划
    try:
        response = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )

        # 解析JSON响应
        import json
        plan_json = json.loads(response.content)

        # 转换为Plan对象
        plan = Plan(
            task=plan_json['task'],
            steps=[Step(**step) for step in plan_json['steps']],
            metadata=plan_json.get('metadata', {})
        )

        logger.info(f"生成计划: {plan.task}")
        for step in plan.steps:
            logger.info(f"  Step {step.step_id}: {step.description}")

        # 保存草稿
        save_execution_log(session_id, None, f"生成执行计划草稿: {plan.task}", "running")

        return {
            "draft": plan,
            "example": example[0] if example else None,
            "messages": [
                AIMessage(content=f"已生成执行计划:\n\n{plan_json}\n\n请审核该计划，如果同意请继续，如有修改意见请提出。")
            ]
        }

    except Exception as e:
        logger.error(f"生成计划失败: {e}")
        save_execution_log(session_id, None, f"生成计划失败: {str(e)}", "failed")

        return {
            "messages": [
                AIMessage(content=f"生成执行计划失败: {str(e)}")
            ]
        }
```

---

### 步骤3.4: 实现API RAG Node

**任务**: 实现API RAG检索节点

**创建文件**: `agent/nodes/api_rag_node.py`

```python
#!/usr/bin/env python3
"""
API RAG Node - API RAG检索节点
负责GDAL/PyQGIS API文档检索与依赖补充
"""

from typing import Dict, Any, List
import logging

from langchain_openai import ChatOpenAI

from agent.state import AgentState, RelevantDoc, StepContext
from agent.tools.rag import search_gdal_docs, search_pyqgis_docs
from agent.tools.database import save_execution_log

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建LLM实例
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.1
)


def api_rag_node(state: AgentState) -> Dict[str, Any]:
    """
    API RAG Node逻辑
    1. 从plan中提取API列表
    2. 并行检索GDAL和PyQGIS文档
    3. 依赖反思与二次补充
    4. 组装聚合上下文

    Args:
        state: 当前状态

    Returns:
        更新后的状态
    """

    plan = state.get('plan')
    session_id = state['session_id']

    if not plan:
        logger.warning("没有可用的plan")
        return {}

    logger.info(f"=== API RAG Node ===")

    # 步骤1: 提取API列表
    gdal_api_list = []
    pyqgis_api_list = []

    for step in plan.steps:
        gdal_api_list.extend(step.gdal_api)
        pyqgis_api_list.extend(step.pyqgis_api)

    # 去重
    gdal_api_list = list(set(gdal_api_list))
    pyqgis_api_list = list(set(pyqgis_api_list))

    logger.info(f"需要检索的GDAL API: {gdal_api_list}")
    logger.info(f"需要检索的PyQGIS API: {pyqgis_api_list}")

    # 步骤2: 并行检索
    gdal_docs = search_gdal_docs(gdal_api_list)
    pyqgis_docs = search_pyqgis_docs(pyqgis_api_list)

    # 步骤3: 依赖反思（简化版：直接跳过二次检索）
    # 实际实现可以使用LLM分析是否需要补充依赖
    missing_deps = []

    # 步骤4: 组装聚合上下文
    api_context_structured = []

    for step in plan.steps:
        step_context = StepContext(step_id=step.step_id, relevant_docs=[])

        # 查找该步骤相关的GDAL文档
        for api_name in step.gdal_api:
            matching_docs = [doc for doc in gdal_docs if doc['method_name'] == api_name]
            for doc in matching_docs:
                step_context.relevant_docs.append(
                    RelevantDoc(
                        api_name=api_name,
                        library="GDAL",
                        content=f"{doc['description']}\n\nParameters: {doc['parameters']}\n\nExample: {doc.get('example_code', '')}"
                    )
                )

        # 查找该步骤相关的PyQGIS文档
        for api_name in step.pyqgis_api:
            matching_docs = [doc for doc in pyqgis_docs if doc['api_name'] == api_name]
            for doc in matching_docs:
                step_context.relevant_docs.append(
                    RelevantDoc(
                        api_name=api_name,
                        library="PyQGIS",
                        content=doc['content']
                    )
                )

        api_context_structured.append(step_context)

    logger.info(f"✅ API RAG完成，组装了 {len(api_context_structured)} 个步骤的上下文")

    save_execution_log(session_id, None, f"API RAG检索完成: {len(gdal_docs)} 条GDAL, {len(pyqgis_docs)} 条PyQGIS", "success")

    return {
        "gdal_doc": gdal_docs,
        "pyqgis_doc": pyqgis_docs,
        "api_context_structured": [ctx.model_dump() for ctx in api_context_structured],
        "missing_deps": missing_deps
    }
```

---

### 步骤3.5: 实现Executor Node

**任务**: 实现执行器节点

**创建文件**: `agent/nodes/executor_node.py`

```python
#!/usr/bin/env python3
"""
Executor Node - 执行器节点
负责代码生成、执行与自主纠错
"""

from typing import Dict, Any
import logging

from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI

from agent.state import AgentState
from agent.tools.database import save_execution_log
from agent.tools.mcp_client import get_mcp_client

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建LLM实例（使用思考模型，如deepseek-reasoner）
llm = ChatOpenAI(
    model="deepseek-reasoner",  # 或gpt-4o
    temperature=0.1
)


async def executor_node(state: AgentState) -> Dict[str, Any]:
    """
    Executor Node逻辑
    1. 上下文装配
    2. 推理与调用循环
    3. 代码执行与观察
    4. 自主纠错机制
    5. 截图生成

    Args:
        state: 当前状态

    Returns:
        更新后的状态
    """

    plan = state.get('plan')
    api_context_structured = state.get('api_context_structured', [])
    current_step_id = state.get('current_step_id', 0)
    session_id = state['session_id']

    if not plan or not api_context_structured:
        logger.warning("没有可用的plan或api_context")
        return {}

    logger.info(f"=== Executor Node === (step_id={current_step_id})")

    # 获取当前步骤
    if current_step_id >= len(plan.steps):
        logger.info("所有步骤已完成")
        return {
            "current_step_id": current_step_id,
            "messages": [
                AIMessage(content="所有执行步骤已完成！")
            ]
        }

    step = plan.steps[current_step_id]
    step_context = api_context_structured[current_step_id]

    logger.info(f"执行步骤 {step.step_id}: {step.description}")

    # 步骤1: 上下文装配
    # 构建API文档上下文
    api_context_parts = []
    for doc in step_context.get('relevant_docs', []):
        api_context_parts.append(f"\n### {doc['library']}: {doc['api_name']}\n{doc['content']}")

    system_prompt = f"""
你是一个专业的QGIS Python开发专家。你的任务是根据提供的API文档和任务描述，生成可执行的PyQGIS/GDAL代码。

## 当前任务
步骤ID: {step.step_id}
任务描述: {step.description}

## 可用的API文档
{''.join(api_context_parts) if api_context_parts else '未提供API文档'}

## 代码生成要求
1. **严格参考API文档**: 参数名称、类型必须与文档完全一致，不要臆造参数
2. **防御性编程**: 在执行前检查文件是否存在、图层是否已加载等
3. **错误处理**: 使用try-except捕获异常，并打印清晰的错误信息
4. **单步专注**: 只完成当前步骤的目标，不要执行后续步骤
5. **文件路径**: 确保文件路径符合Host OS格式（Windows用反斜杠，Linux/Mac用正斜杠）

## 输出格式
只输出Python代码，不要包含任何解释文字。
"""

    # 步骤2: 调用LLM生成代码
    try:
        response = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请生成完成以下任务的代码: {step.description}"}
            ]
        )

        code = response.content.strip()

        # 移除可能的代码块标记
        if code.startswith("```python"):
            code = code[9:]
        elif code.startswith("```"):
            code = code[3:]
        if code.endswith("```"):
            code = code[:-3]

        code = code.strip()

        logger.info(f"生成的代码:\n{code}")

        # 步骤3: 执行代码
        mcp_client = await get_mcp_client()
        result = await mcp_client.call_tool("execute_code", {"code": code})

        # 等待响应
        response = await mcp_client.wait_for_response(timeout=120)

        logger.info(f"执行结果: {response}")

        # 步骤4: 记录执行日志
        if response.get('result', {}).get('success', False):
            save_execution_log(session_id, step.step_id, f"步骤执行成功: {step.description}", "success")

            # 步骤5: 截图
            import os
            screenshot_dir = os.path.join(os.getenv("SHARED_ROOT_DIR", "./shared"), "screenshots", session_id)
            os.makedirs(screenshot_dir, exist_ok=True)
            screenshot_path = os.path.join(screenshot_dir, f"map_canvas_{step.step_id}.png")

            try:
                screenshot_result = await mcp_client.call_tool("capture_map_canvas", {
                    "path": screenshot_path,
                    "width": 800,
                    "height": 600
                })
                logger.info(f"截图已保存: {screenshot_path}")
            except Exception as e:
                logger.warning(f"截图失败: {e}")

            return {
                "current_step_id": current_step_id + 1,
                "screenshot_path": screenshot_path,
                "messages": [
                    AIMessage(content=f"✅ 步骤 {step.step_id} 执行成功: {step.description}\n\n执行结果:\n{response}")
                ]
            }

        else:
            save_execution_log(session_id, step.step_id, f"步骤执行失败: {step.description}", "failed")

            return {
                "messages": [
                    AIMessage(content=f"❌ 步骤 {step.step_id} 执行失败: {step.description}\n\n错误信息:\n{response}")
                ]
            }

    except Exception as e:
        logger.error(f"执行失败: {e}")
        save_execution_log(session_id, step.step_id, f"步骤执行异常: {str(e)}", "failed")

        return {
            "messages": [
                AIMessage(content=f"❌ 步骤执行异常: {str(e)}")
            ]
        }
```

---

### 步骤3.6: 实现Reflector Node

**任务**: 实现反思与归档节点

**创建文件**: `agent/nodes/reflector_node.py`

```python
#!/usr/bin/env python3
"""
Reflector Node - 反思与归档节点
负责执行结果总结、价值判断与归档、状态清理
"""

from typing import Dict, Any
import logging

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from agent.state import AgentState
from agent.tools.database import save_execution_log, save_cookbook_entry

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建LLM实例
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.1
)


def reflector_node(state: AgentState) -> Dict[str, Any]:
    """
    Reflector Node逻辑
    1. 执行结果总结
    2. 价值判断与归档（如果成功）
    3. 状态清理

    Args:
        state: 当前状态

    Returns:
        更新后的状态
    """

    messages = state.get('messages', [])
    plan = state.get('plan')
    is_completed = state.get('is_completed', False)
    session_id = state['session_id']

    logger.info(f"=== Reflector Node === (is_completed={is_completed})")

    # 步骤1: 执行结果总结
    summary_parts = []

    summary_parts.append(f"任务: {state['input_query']}")

    if plan:
        summary_parts.append(f"\n执行计划:")
        summary_parts.append(f"  总任务: {plan.task}")
        summary_parts.append(f"  步骤数: {len(plan.steps)}")

    # 提取关键信息
    success_count = 0
    failed_count = 0

    for msg in messages:
        if isinstance(msg, AIMessage):
            content = msg.content
            if "✅" in content:
                success_count += 1
            elif "❌" in content:
                failed_count += 1

    summary_parts.append(f"\n执行结果:")
    summary_parts.append(f"  成功步骤: {success_count}")
    summary_parts.append(f"  失败步骤: {failed_count}")

    if is_completed:
        summary_parts.append("\n状态: ✅ 任务成功完成")
    else:
        summary_parts.append("\n状态: ❌ 任务未完成")

    log_summary = "\n".join(summary_parts)

    logger.info(log_summary)

    save_execution_log(session_id, None, f"任务完成: {log_summary}", "success" if is_completed else "failed")

    # 步骤2: 价值判断与归档（仅在成功时）
    if is_completed:
        # 提取验证通过的代码（简化版：从messages中提取）
        # 更精确的实现需要标记哪些代码是验证通过的
        verified_code = ""
        for msg in messages:
            if isinstance(msg, AIMessage) and "```python" in msg.content:
                # 提取第一个代码块
                start = msg.content.find("```python")
                end = msg.content.find("```", start + 9)
                if end != -1:
                    verified_code = msg.content[start+9:end].strip()
                    break

        if verified_code:
            # 评估质量（简化版：直接设置为0.8）
            quality_score = 0.8

            if quality_score > 0.6:
                save_cookbook_entry(
                    user_intent=state['input_query'],
                    verified_code=verified_code,
                    tags=[],
                    complexity_score=quality_score
                )
                logger.info("✅ 案例已归档到Cookbook")

    # 步骤3: 状态清理
    # 清空中间字段，保留log_summary和screenshot_path
    cleaned_state = {
        "log_summary": log_summary,
        "screenshot_path": state.get('screenshot_path'),
        "is_completed": is_completed,
        "plan": None,  # 清空plan
        "draft": None,
        "example": None,
        "advise": None,
        "retry_count": 0,
        "status": False,
        "gdal_doc": [],
        "pyqgis_doc": [],
        "api_context_structured": [],
        "missing_deps": [],
        "current_step_id": 0,
        "execution_logs": [],
        "verified_code": None,
        "quality_score": 0.0,
    }

    logger.info("状态清理完成")

    return {
        **cleaned_state,
        "messages": [
            AIMessage(content=f"任务完成总结:\n\n{log_summary}")
        ]
    }
```

---

## 阶段4: State流转与逻辑验证

### 目标

- 配置LangGraph状态机
- 定义节点间的流转规则
- 验证完整流程

---

### 步骤4.1: 构建LangGraph状态机

**任务**: 创建LangGraph StateGraph并配置节点和边

**创建文件**: `agent/graph.py`

```python
#!/usr/bin/env python3
"""
LangGraph状态机定义
配置节点、边和条件路由
"""

import logging
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from agent.state import AgentState, create_initial_state
from agent.nodes.planner_node import planner_node
from agent.nodes.api_rag_node import api_rag_node
from agent.nodes.executor_node import executor_node
from agent.nodes.reflector_node import reflector_node

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def should_continue_planning(state: AgentState) -> Literal["api_rag", "planner"]:
    """
    判断是否继续规划

    Args:
        state: 当前状态

    Returns:
        "api_rag" 如果计划已批准，否则 "planner"
    """

    if state.get('status', False):
        # 计划已批准，进入API RAG
        return "api_rag"
    elif state.get('retry_count', 0) >= 3:
        # 达到重试上限，强制进入API RAG
        logger.warning("已达到重试上限，强制进入执行阶段")
        # 将draft复制给plan
        return "api_rag"
    else:
        # 继续规划
        return "planner"


def should_continue_execution(state: AgentState) -> Literal["executor", "reflector"]:
    """
    判断是否继续执行

    Args:
        state: 当前状态

    Returns:
        "executor" 如果还有步骤未执行，否则 "reflector"
    """

    plan = state.get('plan')
    current_step_id = state.get('current_step_id', 0)

    if plan and current_step_id < len(plan.steps):
        # 还有步骤未执行
        return "executor"
    else:
        # 所有步骤完成，进入反思
        return "reflector"


def build_graph(checkpointer: PostgresSaver = None) -> StateGraph:
    """
    构建LangGraph状态机

    Args:
        checkpointer: 状态持久化器（可选）

    Returns:
        配置好的StateGraph
    """

    # 创建状态机
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("planner", planner_node)
    workflow.add_node("api_rag", api_rag_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("reflector", reflector_node)

    # 设置入口点
    workflow.set_entry_point("planner")

    # 配置边（Edges）
    # 1. Planner -> [API RAG 或 Planner]
    workflow.add_conditional_edges(
        "planner",
        should_continue_planning,
        {
            "api_rag": "api_rag",
            "planner": "planner"
        }
    )

    # 2. API RAG -> Executor
    workflow.add_edge("api_rag", "executor")

    # 3. Executor -> [Executor 或 Reflector]
    workflow.add_conditional_edges(
        "executor",
        should_continue_execution,
        {
            "executor": "executor",
            "reflector": "reflector"
        }
    )

    # 4. Reflector -> END
    workflow.add_edge("reflector", END)

    # 编译状态机
    if checkpointer:
        graph = workflow.compile(checkpointer=checkpointer)
    else:
        graph = workflow.compile()

    logger.info("✅ LangGraph状态机构建完成")

    return graph


def main():
    """测试状态机"""

    import os
    from dotenv import load_dotenv

    # 加载环境变量
    load_dotenv()

    # 创建状态持久化器
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/qgis_db")
    pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()

    # 构建状态机
    graph = build_graph(checkpointer)

    # 创建初始状态
    initial_state = create_initial_state(
        session_id="test_session",
        input_query="帮我加载 /path/to/data.shp 这个矢量图层"
    )

    # 运行状态机
    logger.info("开始运行状态机...")

    config = {"configurable": {"thread_id": "test_thread"}}

    for event in graph.stream(initial_state, config):
        logger.info(f"事件: {event}")

    logger.info("状态机运行完成")

    # 获取最终状态
    final_state = graph.get_state(config).values
    logger.info(f"最终状态: {final_state}")


if __name__ == "__main__":
    main()
```

---

### 步骤4.2: 创建节点__init__.py

**任务**: 创建节点模块的初始化文件

**创建文件**: `agent/nodes/__init__.py`

```python
#!/usr/bin/env python3
"""
LangGraph节点模块
"""

from agent.nodes.planner_node import planner_node
from agent.nodes.api_rag_node import api_rag_node
from agent.nodes.executor_node import executor_node
from agent.nodes.reflector_node import reflector_node

__all__ = [
    "planner_node",
    "api_rag_node",
    "executor_node",
    "reflector_node"
]
```

---

### 步骤4.3: 创建Agent模块__init__.py

**任务**: 创建Agent模块的初始化文件

**创建文件**: `agent/__init__.py`

```python
#!/usr/bin/env python3
"""
QGIS Agent模块
"""

from agent.state import AgentState, create_initial_state
from agent.graph import build_graph

__all__ = [
    "AgentState",
    "create_initial_state",
    "build_graph"
]
```

---

### 步骤4.4: 更新pyproject.toml依赖

**任务**: 添加LangGraph、Chainlit等依赖

**修改文件**: `pyproject.toml`

```toml
[project]
name = "qgis-agentv2"
version = "0.1.0"
description = "QGISMCP connects QGIS to AI agents through the Model Context Protocol (MCP)."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    # 现有依赖
    "mcp>=1.0.0",
    "uvicorn>=0.30.0",
    "starlette>=0.37.0",
    "sse-starlette>=2.1.0",
    "requests>=2.32.0",
    "beautifulsoup4>=4.12.0",
    "anyio>=4.4.0",

    # 新增依赖
    "langgraph>=0.2.0",
    "langchain>=0.3.0",
    "langchain-openai>=0.2.0",
    "langchain-core>=0.3.0",
    "chainlit>=1.3.0",

    # 数据库
    "psycopg[binary]>=3.2.0",
    "psycopg-pool>=3.2.0",
    "python-dotenv>=1.0.0",

    # 其他
    "pydantic>=2.0.0",
    "aiohttp>=3.10.0",
]

# 配置 uv 使用清华及其他国内镜像源
[[tool.uv.index]]
name = "tsinghua"
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true
```

**安装依赖**:

```bash
uv sync
```

---

### 步骤4.5: 测试完整流程

**任务**: 使用控制台模拟用户输入，验证完整流程

**创建文件**: `scripts/test_agent.py`

```python
#!/usr/bin/env python3
"""
Agent完整流程测试
验证"规划→检索→执行→反思"循环
"""

import asyncio
import os
import logging
from dotenv import load_dotenv

from agent.graph import build_graph
from agent.state import create_initial_state
from agent.tools.database import save_execution_log

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_simple_task():
    """测试简单任务"""

    logger.info("=== 测试简单任务 ===")

    # 构建状态机（不使用checkpointer，简化测试）
    graph = build_graph(checkpointer=None)

    # 创建初始状态
    initial_state = create_initial_state(
        session_id="test_simple",
        input_query="帮我加载 /path/to/data.shp 这个矢量图层"
    )

    # 模拟人工审核：自动批准计划
    initial_state['status'] = True

    # 运行状态机
    logger.info("开始运行状态机...")

    config = {"configurable": {"thread_id": "test_thread_simple"}}

    for event in graph.stream(initial_state, config):
        logger.info(f"事件: {event}")

    # 获取最终状态
    final_state = graph.get_state(config).values

    logger.info("状态机运行完成")
    logger.info(f"log_summary: {final_state.get('log_summary')}")


async def test_complex_task():
    """测试复杂任务"""

    logger.info("=== 测试复杂任务 ===")

    # 构建状态机
    graph = build_graph(checkpointer=None)

    # 创建初始状态
    initial_state = create_initial_state(
        session_id="test_complex",
        input_query="帮我加载 /path/to/data.shp 矢量图层，并将其投影转换为 EPSG:4326，然后根据 POPULATION 字段进行分级渲染"
    )

    # 模拟人工审核：自动批准计划
    initial_state['status'] = True

    # 运行状态机
    logger.info("开始运行状态机...")

    config = {"configurable": {"thread_id": "test_thread_complex"}}

    for event in graph.stream(initial_state, config):
        logger.info(f"事件: {event}")

    # 获取最终状态
    final_state = graph.get_state(config).values

    logger.info("状态机运行完成")
    logger.info(f"log_summary: {final_state.get('log_summary')}")


async def main():
    """运行所有测试"""

    # 加载环境变量
    load_dotenv()

    logger.info("开始Agent完整流程测试...")

    # 测试1: 简单任务
    await test_simple_task()

    await asyncio.sleep(2)

    # 测试2: 复杂任务
    await test_complex_task()

    logger.info("Agent完整流程测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
```

**执行测试**:

```bash
# 确保QGIS MCP插件已启动
# 确保统一MCP Server已运行

# 执行测试脚本
uv run python scripts/test_agent.py
```

**验证标准**:
- 状态机能够顺利流转
- Planner Node生成计划
- API RAG Node检索文档
- Executor Node执行代码
- Reflector Node生成总结

---

## 阶段5: Chainlit UI集成

### 目标

- 实现Chainlit最小UI契约
- 处理HITL interrupt交互
- 对接LangGraph thread管理

---

### 步骤5.1: 创建Chainlit应用

**任务**: 创建Chainlit Web应用

**创建文件**: `ui/app.py`

```python
#!/usr/bin/env python3
"""
Chainlit Web应用
提供用户交互界面
"""

import os
import asyncio
from typing import Dict, Any
from dotenv import load_dotenv

import chainlit as cl

from agent.graph import build_graph
from agent.state import create_initial_state
from agent.tools.database import save_execution_log

# 加载环境变量
load_dotenv()

# 配置Chainlit
@cl.on_chat_start
async def on_chat_start():
    """
    聊天会话开始时的初始化
    """

    # 发送欢迎消息
    await cl.Message(
        content="""
        👋 欢迎使用QGIS Agent！

        我可以帮助你通过自然语言完成QGIS地理数据处理任务，例如：

        - **加载图层**: "帮我加载 /path/to/data.shp 这个矢量图层"
        - **投影转换**: "将图层投影转换为 EPSG:4326"
        - **分级渲染**: "根据 POPULATION 字段进行分级渲染"
        - **空间分析**: "对图层进行缓冲区分析，距离1000米"

        请告诉我你的需求！
        """
    ).send()

    # 初始化会话状态
    cl.user_session.set("session_id", cl.user_session.get("id", "default"))
    cl.user_session.set("messages", [])
    cl.user_session.set("plan_approved", False)


@cl.on_message
async def on_message(message: cl.Message):
    """
    处理用户消息

    Args:
        message: 用户消息
    """

    session_id = cl.user_session.get("session_id", "default")
    user_input = message.content

    # 检查是否是plan审核阶段
    plan_approved = cl.user_session.get("plan_approved", False)

    if plan_approved:
        # 用户已经批准了计划，可能是修改意见
        # 这里简化处理：直接启动新的任务
        await run_agent_task(user_input, session_id)
    else:
        # 启动新任务
        await run_agent_task(user_input, session_id)


async def run_agent_task(user_input: str, session_id: str):
    """
    运行Agent任务

    Args:
        user_input: 用户输入
        session_id: 会话ID
    """

    # 显示加载状态
    loading_msg = cl.Message(content="🤖 正在思考...")
    await loading_msg.send()

    try:
        # 构建状态机
        graph = build_graph(checkpointer=None)

        # 创建初始状态
        initial_state = create_initial_state(
            session_id=session_id,
            input_query=user_input
        )

        # 模拟人工审核：直接批准（简化版）
        # 实际实现需要HITL交互
        initial_state['status'] = True

        # 运行状态机
        config = {"configurable": {"thread_id": session_id}}

        last_node = None
        final_log_summary = None

        # 流式执行，实时显示进度
        async for event in graph.astream(initial_state, config):
            # 解析事件
            for node_name, node_output in event.items():
                logger.info(f"节点 {node_name} 完成")

                # 更新UI
                await update_ui_for_node(node_name, node_output)

                last_node = node_name

                # 保存log_summary
                if 'log_summary' in node_output:
                    final_log_summary = node_output['log_summary']

        # 更新加载消息
        await loading_msg.remove()

        # 显示最终结果
        if final_log_summary:
            await cl.Message(content=f"✅ 任务完成！\n\n{final_log_summary}").send()

    except Exception as e:
        await loading_msg.remove()
        await cl.Message(content=f"❌ 任务执行失败: {str(e)}").send()


async def update_ui_for_node(node_name: str, node_output: Dict[str, Any]):
    """
    根据节点输出更新UI

    Args:
        node_name: 节点名称
        node_output: 节点输出
    """

    if node_name == "planner":
        # 显示生成的计划
        if 'draft' in node_output and node_output['draft']:
            draft = node_output['draft']
            plan_text = f"""
📋 **执行计划**

**任务**: {draft.task}

**步骤**:
"""
            for step in draft.steps:
                plan_text += f"\n{step.step_id}. {step.description}\n"
                if step.gdal_api:
                    plan_text += f"   - GDAL API: {', '.join(step.gdal_api)}\n"
                if step.pyqgis_api:
                    plan_text += f"   - PyQGIS API: {', '.join(step.pyqgis_api)}\n"

            await cl.Message(content=plan_text).send()

    elif node_name == "api_rag":
        # 显示API文档检索结果
        gdal_doc_count = len(node_output.get('gdal_doc', []))
        pyqgis_doc_count = len(node_output.get('pyqgis_doc', []))

        await cl.Message(content=f"🔍 已检索 {gdal_doc_count} 条GDAL文档，{pyqgis_doc_count} 条PyQGIS文档").send()

    elif node_name == "executor":
        # 显示执行进度
        current_step = node_output.get('current_step_id', 0)
        await cl.Message(content=f"⚙️ 正在执行步骤 {current_step}...").send()

        # 显示截图
        if 'screenshot_path' in node_output and node_output['screenshot_path']:
            screenshot_path = node_output['screenshot_path']
            if os.path.exists(screenshot_path):
                # 读取图片并显示
                image = cl.Image(path=screenshot_path)
                await cl.Message(content="📸 地图截图:", elements=[image]).send()


if __name__ == "__main__":
    # 启动Chainlit应用
    chainlit run ui/app.py --port 8500
```

---

### 步骤5.2: 创建UI模块__init__.py

**任务**: 创建UI模块的初始化文件

**创建文件**: `ui/__init__.py`

```python
#!/usr/bin/env python3
"""
UI模块
"""

__all__ = []
```

---

### 步骤5.3: 启动Chainlit应用

**任务**: 启动Chainlit Web服务

**命令**:

```bash
uv run chainlit run ui/app.py --port 8500
```

**验证标准**:
- 浏览器能访问 http://localhost:8500
- 能看到欢迎消息
- 能输入任务并看到响应

---

## 阶段6: 人工测试

### 目标

- 使用真实GIS任务进行全流程验证
- 完善错误处理
- 优化性能

---

### 步骤6.1: 准备测试数据

**任务**: 准备真实的GIS测试数据

**操作**:

1. 下载示例数据
2. 将数据放入 `shared/uploads/` 目录
3. 记录文件路径

---

### 步骤6.2: 执行真实任务测试

**任务**: 使用真实GIS任务测试完整流程

**测试用例**:

1. **基础任务**: 加载矢量图层
2. **中等任务**: 投影转换 + 分级渲染
3. **复杂任务**: 缓冲区分析 + 叠加分析

**操作**:

1. 启动QGIS MCP插件
2. 启动统一MCP Server
3. 启动Chainlit应用
4. 在浏览器中输入任务
5. 观察执行流程和结果

---

### 步骤6.3: 完善错误处理

**任务**: 根据测试结果完善错误处理

**检查项**:

- Executor节点是否正确处理执行失败？
- 失败后是否进入自我纠错？
- 是否有死循环风险？
- 截图生成是否可靠？

---

### 步骤6.4: 优化性能

**任务**: 优化系统性能

**优化方向**:

- RAG检索速度
- 代码生成质量
- 截图生成速度
- 数据库查询性能

---

## 附录: 文件结构与命名规范

### A.1 文件命名规范

**Python文件**:
- 全小写 + 下划线
- 示例: `planner_node.py`, `api_rag_node.py`

**配置文件**:
- 全小写
- 示例: `pyproject.toml`, `.env`

**数据文件**:
- 小写 + 下划线 + 时间戳
- 示例: `data_1234567890.shp`, `map_canvas_1234567890.png`

### A.2 目录结构

```
qgis-agentv2/
├── agent/                         # Agent核心逻辑
│   ├── __init__.py
│   ├── state.py                   # State定义
│   ├── graph.py                   # LangGraph状态机
│   ├── nodes/                     # LangGraph节点
│   └── tools/                     # Agent工具
├── ui/                            # Chainlit UI
├── scripts/                       # 脚本
├── shared/                        # 文件系统
└── data/                          # 数据文件
```

### A.3 日志规范

**日志级别**:
- `INFO`: 正常流程
- `WARNING`: 非致命问题
- `ERROR`: 错误

**日志格式**:
```
[时间戳] - [级别] - [消息]
```

### A.4 环境变量

**必需环境变量**:
- `DATABASE_URL`: PostgreSQL连接字符串
- `OPENAI_API_KEY`: OpenAI API密钥
- `QGIS_MCP_SERVER_URL`: QGIS MCP Server URL
- `SHARED_ROOT_DIR`: 文件系统根目录

**可选环境变量**:
- `CHAINLIT_PORT`: Chainlit端口（默认: 8500）
- `OPENAI_BASE_URL`: OpenAI API基础URL

---

## 总结

本实施步骤文档按照技术文档第6章节的6个阶段展开，提供了详细的、可直接执行的步骤指导。

**关键要点**:

1. **简单有效**: 采用最小化设计，避免过度工程
2. **AI友好**: 每个步骤都有明确的输入输出和验证标准
3. **便于审核**: 代码结构清晰，注释完整
4. **分阶段实施**: 每个阶段都可以独立测试验证

**下一步行动**:

1. 按照本文档依次实施6个阶段
2. 每个阶段完成后进行验证
3. 记录遇到的问题和解决方案
4. 根据实际情况调整实施计划

**注意事项**:

- 所有代码都使用uv环境运行
- 确保 PostgreSQL、QGIS MCP Server、MCP Server 都已正确启动
- 测试时使用真实的GIS数据
- 保持代码风格一致性

祝项目顺利实施！🚀
