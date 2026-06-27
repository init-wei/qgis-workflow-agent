#!/usr/bin/env python3
"""
FastAPI 主应用
QGIS Agent Web UI 后端服务
"""

import os
import sys
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, AsyncGenerator, List
from pathlib import Path

# 添加项目根目录到 Python 路径
_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from fastapi import (
    FastAPI,
    HTTPException,
    UploadFile,
    File,
    BackgroundTasks,
    Depends
)
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dotenv import load_dotenv

# 导入 Agent 相关模块
from agent.graph import get_graph
from agent.state import AgentState, create_initial_state
from agent.tools.database import get_execution_logs, save_execution_log
from agent.tools.mcp_client import get_mcp_client

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 配置
WEB_UI_HOST = os.getenv("WEB_UI_HOST", "0.0.0.0")
WEB_UI_PORT = int(os.getenv("WEB_UI_PORT", "9000"))
SHARED_DIR = _project_root / "shared"
UPLOAD_DIR = Path(
    os.getenv("UPLOAD_DIR", str(SHARED_DIR / "upload"))
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 创建 FastAPI 应用
app = FastAPI(
    title="QGIS Agent Web UI",
    description="LangGraph Agent 的 Web 界面",
    version="0.1.0"
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态资源：共享目录（截图、输出等）
if SHARED_DIR.exists():
    app.mount("/shared", StaticFiles(directory=str(SHARED_DIR)), name="shared")

# 静态资源：前端构建产物（/assets/*）
_frontend_dir = Path(__file__).parent / "frontend"
_frontend_assets_dir = _frontend_dir / "assets"
if _frontend_assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_frontend_assets_dir)), name="frontend-assets")


# ========== 数据模型 ==========


class LLMConfig(BaseModel):
    """LLM 配置（可由前端覆盖）"""
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    thread_id: Optional[str] = None
    files: list[str] = []
    llm_config: Optional[LLMConfig] = None


class ReviewRequest(BaseModel):
    """人工审核请求"""
    thread_id: str
    approved: bool
    advise: Optional[str] = None


class SessionCreateRequest(BaseModel):
    """创建新会话请求"""
    session_name: Optional[str] = None


# ========== 全局状态管理 ==========

# 存储正在运行的 Agent 任务
# key: thread_id, value: {"app": StateGraph, "config": dict}
running_tasks: Dict[str, Dict[str, Any]] = {}

# 存储会话列表
sessions: Dict[str, Dict[str, Any]] = {}

# 复用带 checkpointer 的 Graph，避免重复创建连接池
_app_with_checkpointer: Optional[Any] = None
_app_with_checkpointer_lock = asyncio.Lock()


# ========== 依赖项 ==========


async def get_app_with_checkpointer():
    """
    获取带 Checkpointer 的 Graph 实例

    使用 interrupt_before 和 interrupt_after 实现 HITL：
    - interrupt_before: 在 human_review_node 前中断（计划审核）
    - interrupt_after: 在 executor_node 后中断（结果确认）
    """
    global _app_with_checkpointer
    if _app_with_checkpointer is None:
        async with _app_with_checkpointer_lock:
            if _app_with_checkpointer is None:
                _app_with_checkpointer = await get_graph(
                    with_checkpointer=True,
                    interrupt_before=["human_review_node"],
                    interrupt_after=["executor_node"]
                )
    return _app_with_checkpointer


async def fetch_checkpoint_thread_ids(app_instance) -> List[str]:
    """从 PostgreSQL checkpointer 获取所有 thread_id"""
    checkpointer = app_instance.checkpointer
    conn = getattr(checkpointer, "conn", None)
    if conn is None:
        logger.warning("checkpointer 未暴露 conn，无法读取 checkpoints")
        return []

    query = """
        SELECT DISTINCT thread_id
        FROM checkpoints
        ORDER BY thread_id
    """
    if hasattr(conn, "connection"):
        async with conn.connection() as db_conn:
            async with db_conn.cursor() as cur:
                await cur.execute(query)
                rows = await cur.fetchall()
    else:
        async with conn.cursor() as cur:
            await cur.execute(query)
            rows = await cur.fetchall()

    return [thread_id for (thread_id,) in rows if thread_id]


@app.on_event("startup")
async def preload_sessions_from_db() -> None:
    """启动时从数据库预加载会话列表"""
    try:
        app_instance = await get_app_with_checkpointer()
        thread_ids = await fetch_checkpoint_thread_ids(app_instance)

        for thread_id in thread_ids:
            if thread_id in sessions:
                continue
            try:
                config = {"configurable": {"thread_id": thread_id}}
                state = await app_instance.aget_state(config)
                if state and state.values:
                    input_query = state.values.get("input_query") or ""
                    name = (input_query[:50] + "...") if len(input_query) > 50 else input_query
                    if not name:
                        name = f"Session {thread_id[:8]}"
                    created_at = state.metadata.get("created_at") or datetime.now().isoformat()
                    sessions[thread_id] = {
                        "thread_id": thread_id,
                        "name": name,
                        "created_at": created_at
                    }
            except Exception as e:
                logger.warning(f"预加载会话 {thread_id} 失败: {e}")
    except Exception as e:
        logger.warning(f"启动预加载会话失败: {e}")


@app.on_event("shutdown")
async def close_checkpointer_pool() -> None:
    """关闭 AsyncConnectionPool，避免残留后台任务"""
    global _app_with_checkpointer
    app_instance = _app_with_checkpointer
    if not app_instance:
        return
    try:
        checkpointer = getattr(app_instance, "checkpointer", None)
        conn = getattr(checkpointer, "conn", None)
        if conn is not None:
            await conn.close()
    except Exception as e:
        logger.warning(f"关闭 checkpointer 连接池失败: {e}")
    finally:
        _app_with_checkpointer = None


# ========== 工具函数 ==========


def generate_thread_id() -> str:
    """生成新的 thread_id"""
    return str(uuid.uuid4())


def format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 事件"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


THINK_OPEN_TAGS = ("<think", "<thought")
_MAX_OPEN_PREFIX = max(len(tag) for tag in THINK_OPEN_TAGS) - 1


def init_thinking_parse_state() -> Dict[str, Any]:
    """初始化思考标签解析状态"""
    return {
        "buffer": "",
        "in_think": False,
        "think_tag": None
    }


def split_thinking_from_content(
    content: str,
    state: Dict[str, Any]
) -> tuple[list[str], list[str]]:
    """从流式 content 中拆分思考内容与正式输出，并维护跨 chunk 状态"""
    if not content:
        return [], []

    outputs: list[str] = []
    thinkings: list[str] = []

    buffer = f"{state.get('buffer', '')}{content}"
    in_think = bool(state.get("in_think", False))
    think_tag = state.get("think_tag")

    while buffer:
        if in_think:
            close_tag = f"</{think_tag}>" if think_tag else "</think>"
            close_index = buffer.find(close_tag)
            if close_index != -1:
                if close_index:
                    thinkings.append(buffer[:close_index])
                buffer = buffer[close_index + len(close_tag):]
                in_think = False
                think_tag = None
                continue

            tail_len = max(len(close_tag) - 1, 1)
            if len(buffer) > tail_len:
                thinkings.append(buffer[:-tail_len])
                buffer = buffer[-tail_len:]
            break

        pos_think = buffer.find("<think")
        pos_thought = buffer.find("<thought")
        positions = [pos for pos in (pos_think, pos_thought) if pos != -1]
        if not positions:
            if len(buffer) > _MAX_OPEN_PREFIX:
                outputs.append(buffer[:-_MAX_OPEN_PREFIX])
                buffer = buffer[-_MAX_OPEN_PREFIX:]
            break

        pos = min(positions)
        if pos > 0:
            outputs.append(buffer[:pos])

        buffer = buffer[pos:]
        tag_end = buffer.find(">")
        if tag_end == -1:
            break

        tag_text = buffer[:tag_end + 1]
        if tag_text.startswith("<thought"):
            think_tag = "thought"
        else:
            think_tag = "think"

        in_think = True
        buffer = buffer[tag_end + 1:]

    state["buffer"] = buffer
    state["in_think"] = in_think
    state["think_tag"] = think_tag
    return outputs, thinkings


def flush_thinking_buffer(state: Dict[str, Any]) -> tuple[list[str], list[str]]:
    """在流结束时输出残留内容"""
    buffer = state.get("buffer") or ""
    if not buffer:
        return [], []

    if state.get("in_think"):
        state["buffer"] = ""
        return [], [buffer]

    state["buffer"] = ""
    return [buffer], []


def build_execution_summary(messages: list, max_items: int = 1) -> str:
    """构建去重后的执行摘要"""
    summary_lines = []
    seen = set()

    for msg in messages[-max_items:]:
        content = getattr(msg, "content", "")
        normalized = str(content).strip()

        if normalized.startswith("任务完成:"):
            normalized = normalized[len("任务完成:"):].strip()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        summary_lines.append(f"- {content}")

    return "\n".join(summary_lines)


async def stream_agent_execution(
    app,
    initial_state: Dict[str, Any],
    thread_id: str,
    config: Dict[str, Any]
) -> AsyncGenerator[str, None]:
    """
    流式执行 Agent 并推送事件

    Args:
        app: LangGraph 应用实例
        initial_state: 初始状态
        thread_id: 线程 ID
        config: 配置字典

    Yields:
        SSE 格式的事件字符串
    """
    try:
        # 发送开始事件
        yield format_sse_event("start", {
            "thread_id": thread_id,
            "message": "开始执行任务"
        })

        # 用于跟踪节点状态
        current_node = None
        nodes_completed = []
        thinking_state = init_thinking_parse_state()

        # 执行 Agent 并流式推送事件
        async for event in app.astream_events(
            initial_state,
            config=config,
            version="v2"
        ):
            # 解析事件
            event_type = event.get("event", "")
            event_data = event.get("data", {})
            event_metadata = event.get("metadata", {})

            # 过滤高频事件，只记录重要事件
            if event_type not in ["on_chat_model_stream", "on_chain_start", "on_chain_end"]:
                logger.info(f"[SSE] 事件: {event_type}")

            # 节点开始执行
            if event_type == "on_chain_start" and "node" in event_metadata:
                current_node = event_metadata.get("node", "")
                yield format_sse_event("node_start", {
                    "node": current_node,
                    "timestamp": datetime.now().isoformat()
                })

            # 节点执行结束
            elif event_type == "on_chain_end" and "node" in event_metadata:
                node_name = event_metadata.get("node", "")
                nodes_completed.append(node_name)
                yield format_sse_event("node_end", {
                    "node": node_name,
                    "timestamp": datetime.now().isoformat()
                })

            # 消息事件 - 支持 thinking 和 output
            elif event_type == "on_chat_model_stream":
                chunk = event_data.get("chunk", {})
                
                # 处理思考内容（reasoning_content）- 某些模型（如 o1 系列）支持
                reasoning = chunk.reasoning_content if hasattr(chunk, 'reasoning_content') else None
                if reasoning:
                    yield format_sse_event("thinking", {
                        "content": reasoning,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # 处理正式输出
                content = chunk.content if hasattr(chunk, 'content') else ""
                if content:
                    outputs, thinkings = split_thinking_from_content(content, thinking_state)
                    if reasoning:
                        thinkings = []

                    for thinking in thinkings:
                        if not thinking:
                            continue
                        yield format_sse_event("thinking", {
                            "content": thinking,
                            "timestamp": datetime.now().isoformat()
                        })

                    for output in outputs:
                        if not output:
                            continue
                        yield format_sse_event("output", {
                            "content": output,
                            "timestamp": datetime.now().isoformat()
                        })

            # 检查是否有错误
            elif event_type == "on_chain_error":
                error = event_data.get("error", "")
                logger.error(f"[SSE] 链执行错误: {error}")
                yield format_sse_event("error", {
                    "message": str(error),
                    "timestamp": datetime.now().isoformat()
                })

        # ================= CLI 同款中断判定 =================
        logger.info("[SSE] 流结束，使用状态驱动判定是否中断")

        remaining_outputs, remaining_thinkings = flush_thinking_buffer(thinking_state)
        for thinking in remaining_thinkings:
            if not thinking:
                continue
            yield format_sse_event("thinking", {
                "content": thinking,
                "timestamp": datetime.now().isoformat()
            })
        for output in remaining_outputs:
            if not output:
                continue
            yield format_sse_event("output", {
                "content": output,
                "timestamp": datetime.now().isoformat()
            })

        final_state = await app.aget_state(config)
        state_values = final_state.values

        draft = state_values.get("draft")
        status = state_values.get("status", False)
        messages = state_values.get("messages", [])
        is_completed = state_values.get("is_completed", False)

        # 情况 1：Planner → 人工审核
        if draft and not status:
            logger.info("[SSE] CLI判定：需要人工审核计划")
            yield format_sse_event("human_review_required", {
                "draft": {
                    "task": draft.task,
                    "steps": [
                        {
                            "step_id": s.step_id,
                            "description": s.description,
                            "gdal_api": s.gdal_api,
                            "pyqgis_api": s.pyqgis_api
                        }
                        for s in draft.steps
                    ],
                    "metadata": draft.metadata
                },
                "timestamp": datetime.now().isoformat()
            })
            return

        # 情况 2：Executor → 结果确认
        if messages and not is_completed:
            logger.info("[SSE] CLI判定：需要确认执行结果")

            execution_summary = build_execution_summary(messages, max_items=1)

            yield format_sse_event("result_review_required", {
                "execution_summary": execution_summary or "执行完成",
                "messages_count": len(messages),
                "timestamp": datetime.now().isoformat()
            })
            return

        # 情况 3：最终完成
        if state_values.get("final_summary"):
            yield format_sse_event("complete", {
                "summary": state_values.get("final_summary"),
                "screenshot_path": state_values.get("screenshot_path"),
                "timestamp": datetime.now().isoformat()
            })
        else:
            yield format_sse_event("paused", {
                "message": "执行暂停，等待操作",
                "timestamp": datetime.now().isoformat()
            })
        # ====================================================

    except Exception as e:
        logger.error(f"执行 Agent 时出错: {e}", exc_info=True)
        yield format_sse_event("error", {
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        })


# ========== API 路由 ==========


@app.get("/api/info")
async def api_info():
    """API 信息"""
    return {
        "name": "QGIS Agent Web UI",
        "version": "0.1.0",
        "status": "running"
    }


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


@app.get("/api/qgis/status")
async def qgis_plugin_status():
    """QGIS 插件状态检查"""
    try:
        client = await get_mcp_client()
        tools = await asyncio.wait_for(client.list_tools(), timeout=60)
        return {
            "status": "running",
            "tools_count": len(tools),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.warning(f"QGIS 插件状态检查失败: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }
        )


@app.post("/api/sessions", response_model=Dict[str, Any])
async def create_session(request: SessionCreateRequest):
    """
    创建新会话

    返回一个新的 thread_id，用于标识会话
    """
    thread_id = generate_thread_id()
    session_name = request.session_name or f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    sessions[thread_id] = {
        "thread_id": thread_id,
        "name": session_name,
        "created_at": datetime.now().isoformat()
    }

    # 初始化空的 checkpoint 在数据库中
    try:
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": thread_id}}

        # 创建一个初始空状态，这样会话就会在数据库中存在
        initial_state = create_initial_state(
            session_id=thread_id,
            input_query=""
        )

        # 更新状态以保存到数据库
        await app_instance.aupdate_state(config, initial_state)

        logger.info(f"创建新会话并初始化数据库: {thread_id} - {session_name}")

    except Exception as e:
        logger.warning(f"初始化会话数据库记录失败: {e}")

    return {
        "thread_id": thread_id,
        "name": session_name,
        "created_at": sessions[thread_id]["created_at"]
    }


@app.get("/api/sessions", response_model=List[Dict[str, Any]])
async def list_sessions():
    """
    获取所有会话列表

    从 PostgreSQL checkpointer 获取所有线程
    """
    try:
        # 获取 Graph 实例
        app_instance = await get_app_with_checkpointer()

        # 从数据库获取会话列表
        all_sessions: Dict[str, Dict[str, Any]] = {}
        try:
            thread_ids = await fetch_checkpoint_thread_ids(app_instance)
            for thread_id in thread_ids:
                try:
                    config = {"configurable": {"thread_id": thread_id}}
                    state = await app_instance.aget_state(config)

                    if state and state.values:
                        input_query = state.values.get("input_query") or ""
                        name = (input_query[:50] + "...") if len(input_query) > 50 else input_query
                        if not name:
                            name = f"Session {thread_id[:8]}"

                        created_at = state.metadata.get("created_at") or datetime.now().isoformat()
                        all_sessions[thread_id] = {
                            "thread_id": thread_id,
                            "name": name,
                            "created_at": created_at
                        }
                except Exception as e:
                    logger.warning(f"获取会话 {thread_id} 详情失败: {e}")
                    all_sessions[thread_id] = {
                        "thread_id": thread_id,
                        "name": f"Session {thread_id[:8]}",
                        "created_at": datetime.now().isoformat()
                    }
        except Exception as e:
            logger.warning(f"从数据库获取会话列表失败: {e}")

        # 合并内存会话（兜底）
        for thread_id, data in sessions.items():
            if thread_id not in all_sessions:
                all_sessions[thread_id] = {
                    "thread_id": thread_id,
                    "name": data.get("name", f"Session {thread_id[:8]}"),
                    "created_at": data.get("created_at", datetime.now().isoformat())
                }

        # 转换为列表并按创建时间排序（最新的在前）
        all_sessions_list = list(all_sessions.values())
        all_sessions_list.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return all_sessions_list

    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        # 返回内存中的会话作为后备
        return [
            {
                "thread_id": thread_id,
                "name": data["name"],
                "created_at": data["created_at"]
            }
            for thread_id, data in sessions.items()
        ]


@app.get("/api/sessions/{thread_id}/history", response_model=Dict[str, Any])
async def get_session_history(thread_id: str):
    """
    获取会话历史记录

    从数据库和 LangGraph checkpointer 获取完整历史
    """
    try:
        # 获取 Graph 实例
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": thread_id}}

        # 获取当前状态
        current_state = await app_instance.aget_state(config)

        if not current_state or not current_state.values:
            raise HTTPException(status_code=404, detail=f"会话不存在: {thread_id}")

        state_values = current_state.values

        # 构建可读的历史记录
        chat_messages = []

        # 获取输入查询
        input_query = state_values.get("input_query", "")

        # 获取规划内容（draft 或 plan）
        draft = state_values.get("draft")
        plan = state_values.get("plan")
        plan_to_show = draft or plan

        # 获取执行日志摘要
        log_summary = state_values.get("log_summary", "")
        messages = state_values.get("messages", [])

        # 获取最终总结
        final_summary = state_values.get("final_summary")

        # 获取截图路径
        screenshot_path = state_values.get("screenshot_path")

        # 构建聊天消息列表
        # 1. 用户输入
        if input_query:
            chat_messages.append({
                "role": "user",
                "content": input_query
            })

        # 2. 规划内容
        if plan_to_show:
            plan_text = f"📋 **执行计划**\n\n{plan_to_show.task}\n\n**执行步骤**:\n\n"
            if hasattr(plan_to_show, 'steps') and plan_to_show.steps:
                for step in plan_to_show.steps:
                    plan_text += f"{step.step_id}. {step.description}\n"
                    if hasattr(step, 'gdal_api') and step.gdal_api:
                        plan_text += f"   - GDAL API: {', '.join(step.gdal_api)}\n"
                    if hasattr(step, 'pyqgis_api') and step.pyqgis_api:
                        plan_text += f"   - PyQGIS API: {', '.join(step.pyqgis_api)}\n"
            chat_messages.append({
                "role": "assistant",
                "content": plan_text
            })

        # 3. 执行结果（从 log_summary 或 messages 提取）
        if log_summary:
            chat_messages.append({
                "role": "assistant",
                "content": f"⚙️ **执行结果**\n\n{log_summary}"
            })

        # 4. 最终总结
        if final_summary:
            chat_messages.append({
                "role": "assistant",
                "content": f"🎉 任务完成！\n\n{final_summary}"
            })

        # 获取执行日志
        logs = await asyncio.to_thread(get_execution_logs, thread_id, 50)

        history = state_values.get("history", [])

        return {
            "thread_id": thread_id,
            "input_query": input_query,
            "chat_messages": chat_messages,
            "final_summary": final_summary,
            "screenshot_path": screenshot_path,
            "logs": logs,
            "log_summary": log_summary,
            "history": history
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取会话历史失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取历史失败: {str(e)}")


@app.delete("/api/sessions/{thread_id}")
async def delete_session(thread_id: str):
    """
    删除会话
    """
    try:
        app_instance = await get_app_with_checkpointer()
        if hasattr(app_instance, "checkpointer") and hasattr(app_instance.checkpointer, "adelete_thread"):
            await app_instance.checkpointer.adelete_thread(thread_id)
    except Exception as e:
        logger.warning(f"删除会话 {thread_id} 的数据库记录失败: {e}")

    if thread_id in sessions:
        del sessions[thread_id]

    logger.info(f"删除会话: {thread_id}")
    return {"message": "会话已删除"}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件

    将文件保存到 uploads/ 目录，并返回文件路径
    """
    try:
        # 生成安全的文件名
        file_id = str(uuid.uuid4())
        file_ext = Path(file.filename).suffix or ""
        safe_filename = f"{file_id}{file_ext}"
        file_path = UPLOAD_DIR / safe_filename

        # 保存文件
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        logger.info(f"文件上传成功: {safe_filename} ({len(content)} bytes)")

        return {
            "filename": file.filename,
            "file_path": str(file_path),
            "size": len(content)
        }

    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")


@app.get("/api/files")
async def list_files():
    """
    列出已上传的文件
    """
    try:
        files = []
        for file_path in UPLOAD_DIR.iterdir():
            if file_path.is_file():
                stat = file_path.stat()
                files.append({
                    "filename": file_path.name,
                    "file_path": str(file_path),
                    "size": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat()
                })

        return {"files": files}

    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取文件列表失败")


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    流式聊天接口
    """
    thread_id = request.thread_id or generate_thread_id()

    # 初始化会话记录（内存部分）
    if thread_id not in sessions:
        sessions[thread_id] = {
            "thread_id": thread_id,
            "name": f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "created_at": datetime.now().isoformat()
        }

    try:
        # 获取 Graph 实例
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": thread_id}}

        # ----------------------------------------------------------------------
        # 【修正点 1】: 准备输入数据 (Input Delta)
        # 不需要读取 previous_state 并复制，只需要构建本次的新输入
        # ----------------------------------------------------------------------
        
        # 默认只传入 input_query
        input_delta = {
            "input_query": request.message
        }

        # LLM 配置覆盖（前端传入则优先使用）
        if request.llm_config:
            input_delta["llm_config"] = request.llm_config.model_dump(exclude_none=True)

        # 【修正点 2】: 处理“脏状态” (可选)
        # 如果你担心上一轮任务崩溃导致状态没清理干净 (Reflector没跑)，
        # 可以在开始新任务前，强制重置某些覆盖型字段。
        # 注意：不要在此处重置 messages 或 history (追加型字段)
        
        # 检查是否需要强制重置状态 (防御性编程)
        state_snapshot = await app_instance.aget_state(config)
        if state_snapshot and state_snapshot.values:
            # 如果上一轮是非正常结束（比如 draft 还在，或者 status=False），
            # 可以在这里显式修复状态，或者信任 Graph 的 reducer 会处理覆盖
            pass 

        # 如果有文件上传逻辑，添加到 delta
        if request.files:
            # input_delta["files"] = request.files
            pass

        logger.info(f"[Chat] 开始新一轮对话, thread_id: {thread_id}")

        # 返回 SSE 流
        # 注意：这里传入的是 input_delta，而不是完整的 initial_state
        return StreamingResponse(
            stream_agent_execution(app_instance, input_delta, thread_id, config),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except Exception as e:
        logger.error(f"聊天执行失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"执行失败: {str(e)}")


@app.post("/api/review")
async def review_plan(request: ReviewRequest):
    """
    人工审核接口

    批准或拒绝计划，并恢复 Agent 执行
    """
    try:
        # 获取 Graph 实例
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": request.thread_id}}

        # 获取当前状态
        current_state = await app_instance.aget_state(config)
        state_values = current_state.values

        if request.approved:
            # 批准：将 draft 提升为 plan
            draft = state_values.get("draft")
            if draft:
                update_values = {
                    "plan": draft,
                    "status": True
                }
            else:
                raise HTTPException(status_code=400, detail="没有可批准的计划")
        else:
            # 拒绝：设置修改意见
            update_values = {
                "status": False,
                "advise": request.advise or "请重新规划"
            }

        # 更新状态
        await app_instance.aupdate_state(config, update_values)

        logger.info(f"审核结果: {'批准' if request.approved else '拒绝'} - {request.thread_id}")

        return {
            "status": "success",
            "approved": request.approved,
            "message": "审核完成，Agent 将继续执行"
        }

    except Exception as e:
        logger.error(f"审核失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"审核失败: {str(e)}")


class ResultConfirmRequest(BaseModel):
    """执行结果确认请求"""
    thread_id: str
    is_completed: bool  # True=成功, False=失败
    user_feedback: Optional[str] = None


@app.post("/api/confirm-result")
async def confirm_result(request: ResultConfirmRequest):
    """
    确认执行结果

    用户在 Executor 执行完成后确认任务是否成功完成
    """
    try:
        # 获取 Graph 实例
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": request.thread_id}}

        feedback = None
        if not request.is_completed:
            feedback = (request.user_feedback or "").strip() or None

        # 更新状态，设置 is_completed 与 user_feedback
        await app_instance.aupdate_state(
            config,
            {
                "is_completed": request.is_completed,
                "user_feedback": feedback
            }
        )

        logger.info(
            f"结果确认: {'成功' if request.is_completed else '失败'} - {request.thread_id}"
        )

        return {
            "status": "success",
            "is_completed": request.is_completed,
            "message": "结果已确认，Agent 将继续执行 Reflector"
        }

    except Exception as e:
        logger.error(f"结果确认失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"结果确认失败: {str(e)}")


@app.post("/api/resume/{thread_id}")
async def resume_execution(thread_id: str):
    """
    恢复执行

    在审核后继续执行 Agent
    """
    async def event_generator():
        try:
            # 获取 Graph 实例
            app_instance = await get_app_with_checkpointer()
            config = {"configurable": {"thread_id": thread_id}}

            logger.info(f"[Resume] 开始恢复执行, thread_id: {thread_id}")

            # 发送开始事件
            yield format_sse_event("start", {
                "thread_id": thread_id,
                "message": "恢复执行任务"
            })

            # 继续执行并流式推送事件
            thinking_state = init_thinking_parse_state()
            async for event in app_instance.astream_events(
                None,  # None 表示从当前状态继续
                config=config,
                version="v2"
            ):
                event_type = event.get("event", "")
                event_data = event.get("data", {})
                event_metadata = event.get("metadata", {})

                # 过滤高频事件，只记录重要事件
                if event_type not in ["on_chat_model_stream", "on_chain_start", "on_chain_end"]:
                    logger.info(f"[Resume] 事件: {event_type}")

                # 节点开始执行
                if event_type == "on_chain_start" and "node" in event_metadata:
                    node_name = event_metadata.get("node", "")
                    yield format_sse_event("node_start", {
                        "node": node_name,
                        "timestamp": datetime.now().isoformat()
                    })

                # 节点执行结束
                elif event_type == "on_chain_end" and "node" in event_metadata:
                    node_name = event_metadata.get("node", "")
                    yield format_sse_event("node_end", {
                        "node": node_name,
                        "timestamp": datetime.now().isoformat()
                    })

                # 消息事件 - 支持 thinking 和 output
                elif event_type == "on_chat_model_stream":
                    chunk = event_data.get("chunk", {})
                    
                    # 处理思考内容（reasoning_content）- 如果模型支持
                    reasoning = chunk.reasoning_content if hasattr(chunk, 'reasoning_content') else None
                    if reasoning:
                        yield format_sse_event("thinking", {
                            "content": reasoning,
                            "timestamp": datetime.now().isoformat()
                        })
                    
                    # 处理正式输出
                    content = chunk.content if hasattr(chunk, 'content') else ""
                    if content:
                        outputs, thinkings = split_thinking_from_content(content, thinking_state)
                        if reasoning:
                            thinkings = []

                        for thinking in thinkings:
                            if not thinking:
                                continue
                            yield format_sse_event("thinking", {
                                "content": thinking,
                                "timestamp": datetime.now().isoformat()
                            })

                        for output in outputs:
                            if not output:
                                continue
                            yield format_sse_event("output", {
                                "content": output,
                                "timestamp": datetime.now().isoformat()
                            })

                # 检查是否有错误
                elif event_type == "on_chain_error":
                    error = event_data.get("error", "")
                    logger.error(f"[Resume] 链执行错误: {error}")
                    yield format_sse_event("error", {
                        "message": str(error),
                        "timestamp": datetime.now().isoformat()
                    })

            # ================= CLI 同款中断判定 =================
            logger.info("[Resume] 流结束，使用状态驱动判定是否中断")

            remaining_outputs, remaining_thinkings = flush_thinking_buffer(thinking_state)
            for thinking in remaining_thinkings:
                if not thinking:
                    continue
                yield format_sse_event("thinking", {
                    "content": thinking,
                    "timestamp": datetime.now().isoformat()
                })
            for output in remaining_outputs:
                if not output:
                    continue
                yield format_sse_event("output", {
                    "content": output,
                    "timestamp": datetime.now().isoformat()
                })

            final_state = await app_instance.aget_state(config)
            state_values = final_state.values

            draft = state_values.get("draft")
            status = state_values.get("status", False)
            messages = state_values.get("messages", [])
            is_completed = state_values.get("is_completed", False)

            # 情况 1：Planner → 人工审核
            if draft and not status:
                logger.info("[Resume] CLI判定：需要人工审核计划")
                yield format_sse_event("human_review_required", {
                    "draft": {
                        "task": draft.task,
                        "steps": [
                            {
                                "step_id": s.step_id,
                                "description": s.description,
                                "gdal_api": s.gdal_api,
                                "pyqgis_api": s.pyqgis_api
                            }
                            for s in draft.steps
                        ],
                        "metadata": draft.metadata
                    },
                    "timestamp": datetime.now().isoformat()
                })
                return

            # 情况 2：Executor → 结果确认
            if messages and not is_completed:
                logger.info("[Resume] CLI判定：需要确认执行结果")

                execution_summary = build_execution_summary(messages, max_items=5)

                yield format_sse_event("result_review_required", {
                    "execution_summary": execution_summary or "执行完成",
                    "messages_count": len(messages),
                    "timestamp": datetime.now().isoformat()
                })
                return

            # 情况 3：最终完成
            if state_values.get("final_summary"):
                yield format_sse_event("complete", {
                    "summary": state_values.get("final_summary"),
                    "screenshot_path": state_values.get("screenshot_path"),
                    "timestamp": datetime.now().isoformat()
                })
            else:
                yield format_sse_event("paused", {
                    "message": "执行暂停，等待操作",
                    "timestamp": datetime.now().isoformat()
                })
            # ====================================================

        except Exception as e:
            logger.error(f"恢复执行失败: {e}", exc_info=True)

            # 返回错误事件
            yield format_sse_event("error", {
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/state/{thread_id}")
async def get_current_state(thread_id: str):
    """
    获取当前状态

    返回指定 thread_id 的当前 Agent 状态
    """
    try:
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": thread_id}}

        state = await app_instance.aget_state(config)
        state_values = state.values

        # 序列化 Pydantic 模型
        serialized = {}
        for key, value in state_values.items():
            if hasattr(value, "model_dump"):
                serialized[key] = value.model_dump()
            elif isinstance(value, list):
                serialized[key] = [
                    v.model_dump() if hasattr(v, "model_dump") else v
                    for v in value
                ]
            else:
                serialized[key] = value

        return {
            "thread_id": thread_id,
            "state": serialized,
            "next": state.next,
            "metadata": state.metadata
        }

    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        raise HTTPException(status_code=404, detail=f"会话不存在: {thread_id}")


# ========== 启动服务 ==========


# 单独的根路由返回 HTML（不使用 mount 避免路由冲突）
@app.get("/")
async def serve_html():
    """返回前端 HTML 页面"""
    ui_dir = Path(__file__).parent / "frontend"
    html_file = ui_dir / "index.html"

    from fastapi.responses import FileResponse
    if html_file.exists():
        return FileResponse(html_file)
    return {"message": "UI not found"}


if __name__ == "__main__":
    import uvicorn

    # 打印所有注册的路由（调试用）
    logger.info("已注册的路由:")
    for route in app.routes:
        if hasattr(route, 'path') and hasattr(route, 'methods'):
            logger.info(f"  {route.methods} {route.path}")
        elif hasattr(route, 'path'):
            logger.info(f"  MOUNT {route.path}")

    logger.info(f"启动 QGIS Agent Web UI 服务...")
    logger.info(f"地址: http://{WEB_UI_HOST}:{WEB_UI_PORT}")

    uvicorn.run(
        "main:app",
        host=WEB_UI_HOST,
        port=WEB_UI_PORT,
        reload=True,
        log_level="info"
    )
