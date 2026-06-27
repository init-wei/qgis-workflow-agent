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
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/qgis_db")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)


def create_tables():
    """创建所有数据表"""
    
    with pool.connection() as conn:
        with conn.cursor() as cur:
            
            # 1. 创建 rag_docs 表（API文档存储）
            logger.info("创建 rag_docs 表...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_docs (
                    id SERIAL PRIMARY KEY,
                    doc_type VARCHAR(50) NOT NULL,  -- 'gdal' 或 'pyqgis'
                    api_name VARCHAR(255) NOT NULL, -- API名称（如 'gdal.Warp'）
                    category VARCHAR(100),          -- 分类（如 'raster_processing'）
                    signature TEXT,                 -- 函数签名
                    params TEXT,                    -- 参数详情（纯文本格式，每行一个参数）
                    returns TEXT,                   -- 返回值说明
                    description TEXT,               -- 功能描述
                    example_code TEXT,              -- 示例代码
                    source_url TEXT,                -- 来源URL
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(doc_type, api_name)      -- 防止重复
                );
            """)
            
            # 创建索引：按 api_name 查询
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_docs_api_name 
                ON rag_docs(api_name);
            """)
            
            # 创建索引：按 doc_type 查询
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_docs_doc_type 
                ON rag_docs(doc_type);
            """)
            
            # 创建索引：按 category 查询
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_docs_category 
                ON rag_docs(category);
            """)
            
            # 创建 pg_trgm 索引用于模糊搜索 params
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_docs_params_trgm 
                ON rag_docs USING GIN(params gin_trgm_ops);
            """)
            
            # 创建 pg_trgm 索引用于模糊搜索 API 名称
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_docs_api_name_trgm 
                ON rag_docs USING GIN(api_name gin_trgm_ops);
            """)
            
            logger.info("✅ rag_docs 表创建成功")
            
            # 2. 创建 cookbook 表（成功案例存储）
            logger.info("创建 cookbook 表...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cookbook (
                    id SERIAL PRIMARY KEY,
                    user_intent TEXT NOT NULL,         -- 用户原始意图
                    verified_code TEXT NOT NULL,       -- 验证通过的代码
                    steps JSONB,                       -- 执行步骤（JSON数组）
                    tags TEXT[],                       -- 标签数组
                    complexity_score FLOAT DEFAULT 0.5, -- 复杂度评分（0-1）
                    quality_score FLOAT DEFAULT 0.5,   -- 质量评分（0-1）
                    usage_count INT DEFAULT 0,         -- 被引用次数
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # 创建 GIN 索引用于标签数组搜索
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cookbook_tags 
                ON cookbook USING GIN(tags);
            """)
            
            # 创建 pg_trgm 索引用于模糊搜索用户意图
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cookbook_user_intent_trgm 
                ON cookbook USING GIN(user_intent gin_trgm_ops);
            """)
            
            # 创建索引：按复杂度评分排序
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cookbook_complexity_score 
                ON cookbook(complexity_score);
            """)
            
            # 创建索引：按使用次数排序
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_cookbook_usage_count 
                ON cookbook(usage_count DESC);
            """)
            
            logger.info("✅ cookbook 表创建成功")
            
            # 3. 创建 execution_logs 表（执行日志）
            logger.info("创建 execution_logs 表...")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(255) NOT NULL,  -- 会话ID
                    step_id INT,                       -- 步骤ID（可为NULL）
                    log_level VARCHAR(20) DEFAULT 'INFO', -- 日志级别
                    message TEXT NOT NULL,             -- 日志消息
                    status VARCHAR(50),                -- 状态（success/error/warning）
                    metadata JSONB,                    -- 元数据（JSON格式）
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # 创建索引：按 session_id 查询
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_execution_logs_session_id 
                ON execution_logs(session_id);
            """)
            
            # 创建索引：按时间排序
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_execution_logs_created_at 
                ON execution_logs(created_at DESC);
            """)
            
            # 创建索引：按日志级别查询
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_execution_logs_log_level 
                ON execution_logs(log_level);
            """)
            
            logger.info("✅ execution_logs 表创建成功")
            
            # 提交事务
            conn.commit()
            logger.info("✅ 所有表创建成功并提交")


def create_update_trigger():
    """创建自动更新updated_at字段的触发器"""
    
    with pool.connection() as conn:
        with conn.cursor() as cur:
            
            # 创建更新时间戳的函数
            logger.info("创建 update_updated_at_column 函数...")
            cur.execute("""
                CREATE OR REPLACE FUNCTION update_updated_at_column()
                RETURNS TRIGGER AS $$
                BEGIN
                    NEW.updated_at = CURRENT_TIMESTAMP;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """)
            
            # 为 rag_docs 表创建触发器
            cur.execute("""
                DROP TRIGGER IF EXISTS update_rag_docs_updated_at ON rag_docs;
                CREATE TRIGGER update_rag_docs_updated_at
                BEFORE UPDATE ON rag_docs
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """)
            
            # 为 cookbook 表创建触发器
            cur.execute("""
                DROP TRIGGER IF EXISTS update_cookbook_updated_at ON cookbook;
                CREATE TRIGGER update_cookbook_updated_at
                BEFORE UPDATE ON cookbook
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """)
            
            # 提交事务
            conn.commit()
            logger.info("✅ 触发器创建成功")


def verify_tables():
    """验证表是否创建成功"""
    
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            
            # 查询所有表
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE'
                ORDER BY table_name;
            """)
            
            tables = cur.fetchall()
            logger.info(f"\n当前数据库中的表：")
            for table in tables:
                logger.info(f"  - {table['table_name']}")
            
            # 查询所有索引
            cur.execute("""
                SELECT tablename, indexname 
                FROM pg_indexes 
                WHERE schemaname = 'public'
                ORDER BY tablename, indexname;
            """)
            
            indexes = cur.fetchall()
            logger.info(f"\n当前数据库中的索引：")
            current_table = None
            for index in indexes:
                if index['tablename'] != current_table:
                    current_table = index['tablename']
                    logger.info(f"  {current_table}:")
                logger.info(f"    - {index['indexname']}")
            
            # 验证扩展
            cur.execute("""
                SELECT extname, extversion 
                FROM pg_extension 
                WHERE extname IN ('postgis', 'vector', 'pg_trgm')
                ORDER BY extname;
            """)
            
            extensions = cur.fetchall()
            logger.info(f"\n已安装的扩展：")
            for ext in extensions:
                logger.info(f"  - {ext['extname']} (版本: {ext['extversion']})")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("开始初始化数据库...")
    logger.info("=" * 60)
    
    try:
        create_tables()
        create_update_trigger()
        verify_tables()
        
        logger.info("=" * 60)
        logger.info("✅ 数据库初始化完成！")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        raise
    finally:
        pool.close()
