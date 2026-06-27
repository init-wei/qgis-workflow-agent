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
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/qgis_db")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)


def save_execution_log(
    session_id: str, 
    step_id: Optional[int], 
    message: str, 
    status: str,
    error_detail: Optional[str] = None
) -> bool:
    """
    保存执行日志到数据库
    
    Args:
        session_id: 会话ID
        step_id: 步骤ID（可选）
        message: 日志消息
        status: 执行状态 (success, error, warning, info)
        error_detail: 错误详情（可选）
        
    Returns:
        是否保存成功
    """
    try:
        import json
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # 将error_detail放入metadata JSON字段
                metadata = {"error_detail": error_detail} if error_detail else None
                
                cur.execute(
                    """
                    INSERT INTO execution_logs 
                    (session_id, step_id, message, status, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (session_id, step_id, message, status, json.dumps(metadata) if metadata else None)
                )
                conn.commit()
                logger.debug(f"保存执行日志: session={session_id}, step={step_id}, status={status}")
                return True
    except Exception as e:
        logger.error(f"保存执行日志失败: {e}")
        return False


def get_execution_logs(session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    获取指定会话的执行日志
    
    Args:
        session_id: 会话ID
        limit: 返回记录数限制
        
    Returns:
        执行日志列表
    """
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT * FROM execution_logs
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (session_id, limit)
                )
                logs = cur.fetchall()
                logger.debug(f"获取执行日志: session={session_id}, count={len(logs)}")
                return logs
    except Exception as e:
        logger.error(f"获取执行日志失败: {e}")
        return []


def save_cookbook_entry(
    user_intent: str,
    verified_code: str,
    tags: Optional[List[str]] = None,
    complexity_score: float = 0.5,
    steps: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """
    保存成功案例到Cookbook
    
    Args:
        user_intent: 用户意图描述
        verified_code: 已验证的代码
        tags: 标签列表（可选）
        complexity_score: 复杂度评分 (0.0-1.0)
        steps: 执行步骤（可选，JSON格式）
        
    Returns:
        插入记录的ID，失败返回None
    """
    try:
        import json
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cookbook 
                    (user_intent, verified_code, tags, complexity_score, steps)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user_intent, verified_code, tags or [], complexity_score, 
                     json.dumps(steps) if steps else None)
                )
                result = cur.fetchone()
                conn.commit()
                entry_id = result[0] if result else None
                logger.info(f"保存Cookbook条目成功: id={entry_id}")
                return entry_id
    except Exception as e:
        logger.error(f"保存Cookbook条目失败: {e}")
        return None


def update_cookbook_usage(entry_id: int) -> bool:
    """
    更新Cookbook条目的使用次数
    
    Args:
        entry_id: Cookbook条目ID
        
    Returns:
        是否更新成功
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cookbook 
                    SET usage_count = usage_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (entry_id,)
                )
                conn.commit()
                logger.debug(f"更新Cookbook使用次数: id={entry_id}")
                return True
    except Exception as e:
        logger.error(f"更新Cookbook使用次数失败: {e}")
        return False


def get_cookbook_by_id(entry_id: int) -> Optional[Dict[str, Any]]:
    """
    根据ID获取Cookbook条目
    
    Args:
        entry_id: Cookbook条目ID
        
    Returns:
        Cookbook条目，不存在返回None
    """
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT * FROM cookbook
                    WHERE id = %s
                    """,
                    (entry_id,)
                )
                result = cur.fetchone()
                return result
    except Exception as e:
        logger.error(f"获取Cookbook条目失败: {e}")
        return None
