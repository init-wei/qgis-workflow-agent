#!/usr/bin/env python3
"""
数据库工具模块
提供数据库连接和查询功能
"""

import os
import json
from threading import Lock

from agent.tools.execution_guard import block_unknown_task, is_task_active
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

_pool = None
_pool_lock = Lock()


def _get_pool() -> ConnectionPool:
    """Create the application pool only when PostgreSQL storage is requested."""

    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise RuntimeError("DATABASE_URL must point to this project's PostgreSQL database.")
            _pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=10)
    return _pool


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
        with _get_pool().connection() as conn:
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
        with _get_pool().connection() as conn:
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
        with _get_pool().connection() as conn:
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
        with _get_pool().connection() as conn:
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
        with _get_pool().connection() as conn:
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


def save_uploaded_file_record(
    file_id: str,
    filename: str,
    storage_path: str,
    size_bytes: int,
    sha256: str,
    layers: list[dict[str, Any]],
) -> None:
    """Persist the stable file ID and inspected metadata for a controlled upload."""

    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO uploaded_files
                    (file_id, filename, storage_path, size_bytes, sha256, layers)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (file_id, filename, storage_path, size_bytes, sha256, json.dumps(layers)),
            )
            conn.commit()


def get_uploaded_file_record(file_id: str) -> dict[str, Any] | None:
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM uploaded_files WHERE file_id = %s", (file_id,))
            return cur.fetchone()


def list_uploaded_file_records() -> list[dict[str, Any]]:
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM uploaded_files ORDER BY uploaded_at DESC, file_id")
            return list(cur.fetchall())


def save_task_execution_record(
    *,
    task_id: str,
    session_id: str,
    plan_revision: int,
    status: str,
    plan: dict[str, Any],
    attempts: list[dict[str, Any]],
    confirmed: bool | None = None,
    summary: str | None = None,
    claim_execution: bool = False,
    inflight_call: dict[str, Any] | None = None,
    resume_state: dict[str, Any] | None = None,
    checkpoint_resume: dict[str, Any] | None = None,
) -> None:
    """Upsert one task fact and replace its recorded step attempts."""

    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            if claim_execution:
                # A previous process can leave an unresolved remote write. The
                # one-process GIS slot cannot establish its outcome after restart.
                cur.execute(
                    """SELECT task_id FROM task_executions
                       WHERE status IN ('running', 'needs_attention') AND task_id <> %s
                       LIMIT 1""", (task_id,),
                )
                unresolved = cur.fetchone()
                if unresolved is not None:
                    block_unknown_task(unresolved[0])
                    raise RuntimeError('GIS execution needs attention: an earlier operation outcome is unknown.')
                if checkpoint_resume is not None:
                    cur.execute(
                        """SELECT attempt_id, step_id, tool_name, tool_arguments, status, observation, artifacts
                           FROM task_step_attempts WHERE task_id = %s""", (task_id,),
                    )
                    durable_attempts = {row[0]: row[1:] for row in cur.fetchall()}
                    expected_attempts = {}
                    for attempt in attempts:
                        call = attempt['tool_call']
                        observation = dict(attempt['observation'])
                        observation.pop('raw_response', None)
                        expected_attempts[attempt['attempt_id']] = (
                            attempt['step_id'], call['tool_name'], call['arguments'],
                            attempt['status'], observation, attempt.get('artifacts', []),
                        )
                    if durable_attempts != expected_attempts:
                        cur.execute(
                            """UPDATE task_executions SET status = 'needs_attention', updated_at = CURRENT_TIMESTAMP
                               WHERE task_id = %s""", (task_id,),
                        )
                        conn.commit()
                        block_unknown_task(task_id)
                        raise RuntimeError('Checkpoint attempts disagree with durable facts; needs attention.')
                initial_claim = checkpoint_resume is None and not attempts
                cur.execute(
                    """UPDATE task_executions SET status = 'running', updated_at = CURRENT_TIMESTAMP
                       WHERE task_id = %s AND session_id = %s AND plan_revision = %s
                         AND plan = %s::jsonb
                         AND ((status = 'approved' AND %s)
                           OR (status = 'needs_review' AND resume_state = %s::jsonb))""",
                    (task_id, session_id, plan_revision, json.dumps(plan, ensure_ascii=False),
                     initial_claim, json.dumps(checkpoint_resume, ensure_ascii=False)),
                )
                if cur.rowcount != 1:
                    cur.execute(
                        """UPDATE task_executions SET status = 'needs_attention', updated_at = CURRENT_TIMESTAMP
                           WHERE task_id = %s AND status IN ('running', 'needs_review', 'approved')""", (task_id,),
                    )
                    conn.commit()
                    block_unknown_task(task_id)
                    raise RuntimeError('Execution not started: checkpoint and durable facts disagree, completed, or unknown outcome; needs attention.')
            cur.execute(
                """
                INSERT INTO task_executions
                    (task_id, session_id, plan_revision, status, plan, confirmed, summary, inflight_call, resume_state)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (task_id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    plan_revision = EXCLUDED.plan_revision,
                    status = EXCLUDED.status,
                    plan = EXCLUDED.plan,
                    inflight_call = EXCLUDED.inflight_call,
                    resume_state = EXCLUDED.resume_state,
                    confirmed = COALESCE(EXCLUDED.confirmed, task_executions.confirmed),
                    summary = COALESCE(EXCLUDED.summary, task_executions.summary),
                    updated_at = CURRENT_TIMESTAMP
                WHERE EXCLUDED.status NOT IN ('approved', 'rejected')
                   OR task_executions.status IN ('approved', 'rejected')
                """,
                (
                    task_id,
                    session_id,
                    plan_revision,
                    status,
                    json.dumps(plan, ensure_ascii=False),
                    confirmed,
                    summary,
                    json.dumps(inflight_call, ensure_ascii=False) if inflight_call else None,
                    json.dumps(resume_state, ensure_ascii=False) if resume_state else None,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError('Task has already started; a stale review cannot authorize replay.')
            cur.execute("DELETE FROM task_step_attempts WHERE task_id = %s", (task_id,))
            for attempt in attempts:
                tool_call = attempt.get("tool_call") or {}
                observation = dict(attempt.get("observation") or {})
                observation.pop("raw_response", None)
                cur.execute(
                    """
                    INSERT INTO task_step_attempts
                        (attempt_id, task_id, step_id, tool_name, tool_arguments,
                         status, observation, artifacts, started_at, finished_at, duration_ms)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb,
                            %s, %s, %s)
                    """,
                    (
                        attempt["attempt_id"],
                        task_id,
                        attempt["step_id"],
                        tool_call.get("tool_name", ""),
                        json.dumps(tool_call.get("arguments", {}), ensure_ascii=False),
                        attempt["status"],
                        json.dumps(observation, ensure_ascii=False),
                        json.dumps(attempt.get("artifacts", []), ensure_ascii=False),
                        attempt["started_at"],
                        attempt["finished_at"],
                        attempt["duration_ms"],
                    ),
                )
            conn.commit()


def _project_orphaned_execution(cur, execution: dict[str, Any]) -> None:
    """Project a locked durable row using this process's actual GIS owner."""
    task_id = execution['task_id']
    if execution['status'] == 'running' and not is_task_active(task_id):
        cur.execute(
            """UPDATE task_executions SET status = 'needs_attention', updated_at = CURRENT_TIMESTAMP
               WHERE task_id = %s AND status = 'running'""", (task_id,),
        )
        execution['status'] = 'needs_attention'
        block_unknown_task(task_id)
    elif execution['status'] == 'needs_attention':
        block_unknown_task(task_id)


def get_task_execution_record(task_id: str) -> dict[str, Any] | None:
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM task_executions WHERE task_id = %s FOR UPDATE", (task_id,))
            execution = cur.fetchone()
            if execution is None:
                return None
            _project_orphaned_execution(cur, execution)
            cur.execute(
                """
                SELECT attempt_id, step_id, tool_name, tool_arguments, status,
                       observation, artifacts, started_at, finished_at, duration_ms
                FROM task_step_attempts
                WHERE task_id = %s
                ORDER BY started_at, attempt_id
                """,
                (task_id,),
            )
            attempts = list(cur.fetchall())
            conn.commit()
    artifacts = [artifact for attempt in attempts for artifact in attempt["artifacts"]]
    return {"execution": execution, "attempts": attempts, "artifacts": artifacts}


def confirm_task_execution(task_id: str, confirmed: bool, summary: str | None = None) -> bool:
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE task_executions
                SET confirmed = %s,
                    summary = COALESCE(%s, summary),
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_id = %s
                """,
                (confirmed, summary, task_id),
            )
            conn.commit()
            return cur.rowcount == 1


def list_task_executions_for_session(session_id: str) -> list[dict[str, Any]]:
    with _get_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT task_id, plan_revision, status, confirmed, summary, created_at, updated_at
                FROM task_executions
                WHERE session_id = %s
                ORDER BY created_at, task_id FOR UPDATE
                """,
                (session_id,),
            )
            executions = list(cur.fetchall())
            for execution in executions:
                _project_orphaned_execution(cur, execution)
            conn.commit()
            return executions
