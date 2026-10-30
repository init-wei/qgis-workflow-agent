#!/usr/bin/env python3
"""
FastAPI 主应用
QGIS Agent Web UI 后端服务
"""
from __future__ import annotations

import os
import sys
import asyncio
import json
import logging
import uuid
import hashlib
import subprocess
from contextlib import asynccontextmanager
from functools import wraps
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
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from langchain.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from dotenv import load_dotenv

# 导入 Agent 相关模块
from agent.graph import get_graph
from agent.state import AgentState, create_initial_state
from agent.planning.plan_schema import TaskPlan
from agent.planning.plan_validator import PlanValidator
from agent.tools.execution_guard import ExecutionBusy, ExecutionSlot, ThreadRun
from agent.tools.database import (
    confirm_task_execution,
    get_task_execution_record,
    get_uploaded_file_record,
    list_task_executions_for_session,
    list_uploaded_file_records,
    save_task_execution_record,
    save_uploaded_file_record,
)
from agent.tools.demo_cases import (
    load_demo_case,
    load_demo_cases,
    replay_demo_case,
    summarize_demo_case,
)
from agent.tools.mcp_client import get_mcp_client
from agent.tools.tool_contracts import HumanFeedback, RiskReviewStatus, ToolCallPlan

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
DEFAULT_UPLOAD_DIR = SHARED_DIR / "uploads"
UPLOAD_DIR = Path(
    os.getenv("UPLOAD_DIR", str(DEFAULT_UPLOAD_DIR))
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_VECTOR_UPLOAD_BYTES = int(os.getenv("MAX_VECTOR_UPLOAD_BYTES", str(50 * 1024 * 1024)))
SUPPORTED_VECTOR_EXTENSIONS = {".geojson", ".json", ".gpkg"}
VECTOR_INSPECTOR = _project_root / "scripts" / "inspect_vector.py"


def inspect_uploaded_vector(path: Path) -> list[dict[str, Any]]:
    """Read vector metadata with the QGIS distribution's system Python/GDAL."""

    try:
        result = subprocess.run(
            ["/usr/bin/python3", str(VECTOR_INSPECTOR), str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Vector inspection exceeded 20 seconds.") from exc
    except OSError as exc:
        raise RuntimeError("The system QGIS Python inspector is unavailable.") from exc

    if result.returncode != 0:
        raise ValueError("Could not inspect a local GeoJSON or GeoPackage input.")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Vector inspector returned invalid metadata.") from exc
    layers = payload.get("layers") if isinstance(payload, dict) else None
    if not isinstance(layers, list) or not layers:
        raise ValueError("Vector inspector returned no vector layers.")
    return layers


def serialize_uploaded_file_record(record: dict[str, Any]) -> dict[str, Any]:
    filename = record["filename"]
    layers = [
        {**layer, "source": filename}
        for layer in record.get("layers", [])
    ]
    return {
        "file_id": record["file_id"],
        "filename": filename,
        "size": record["size_bytes"],
        "sha256": record["sha256"].strip(),
        "layer_info": layers[0] if len(layers) == 1 else None,
        "layers": layers,
        "created_at": record["uploaded_at"],
    }


def resolve_selected_files(file_references: list[str | FileReference]) -> list[dict[str, Any]]:
    """Resolve browser file IDs to database-backed files and explicit vector layers."""

    selected_files = []
    for raw_reference in file_references:
        reference = (
            FileReference(file_id=raw_reference)
            if isinstance(raw_reference, str)
            else raw_reference
        )
        record = get_uploaded_file_record(reference.file_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Selected upload was not found.")

        layers = record.get("layers") or []
        if reference.layer_name:
            matches = [layer for layer in layers if layer.get("layer_name") == reference.layer_name]
            if len(matches) != 1:
                raise HTTPException(status_code=422, detail="Selected layer was not found in this file.")
            layer = matches[0]
        elif len(layers) == 1:
            layer = layers[0]
        elif len(layers) > 1:
            raise HTTPException(status_code=422, detail="Choose a layer from this multi-layer GeoPackage.")
        else:
            raise HTTPException(status_code=422, detail="Selected file has no vector layers.")

        if not layer.get("is_2d", False):
            raise HTTPException(status_code=422, detail="The selected vector layer must contain only 2D geometries.")

        storage_path = Path(record["storage_path"])
        if storage_path.name != str(storage_path) or storage_path.is_absolute():
            raise HTTPException(status_code=500, detail="Stored upload path is invalid.")
        file_path = (UPLOAD_DIR / storage_path).resolve()
        try:
            file_path.relative_to(UPLOAD_DIR.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="Stored upload path is invalid.") from exc
        if not file_path.is_file():
            raise HTTPException(status_code=410, detail="Selected upload is no longer available.")

        qgis_path = str(file_path)
        if file_path.suffix.lower() == ".gpkg":
            qgis_path = f"{qgis_path}|layername={layer['layer_name']}"
        selected_files.append(
            {
                "file_id": record["file_id"],
                "filename": record["filename"],
                "qgis_path": qgis_path,
                "sha256": record["sha256"].strip() if record.get("sha256") else None,
                "layer_name": layer["layer_name"],
                "layer_info": {**layer, "source": record["filename"]},
            }
        )
    return selected_files


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await preload_sessions_from_db()
    try:
        yield
    finally:
        await close_checkpointer_pool()


# 创建 FastAPI 应用
app = FastAPI(
    title="QGIS Agent Web UI",
    description="LangGraph Agent 的 Web 界面",
    version="0.1.0",
    lifespan=lifespan,
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
FRONTEND_DIR = _project_root / "ui" / "frontend" / "dist"
_frontend_assets_dir = FRONTEND_DIR / "assets"
if _frontend_assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_frontend_assets_dir)), name="frontend-assets")


# ========== 数据模型 ==========


class LLMConfig(BaseModel):
    """Non-secret model tuning; credentials and API base stay server-side."""

    model_config = ConfigDict(extra="forbid")

    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class FileReference(BaseModel):
    """Reference to a server-owned upload and optionally selected layer."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    layer_name: Optional[str] = None


class ChatRequest(BaseModel):
    """聊天请求"""

    model_config = ConfigDict(extra="forbid")

    message: str
    thread_id: Optional[str] = None
    files: list[str | FileReference] = []
    llm_config: Optional[LLMConfig] = None


class ReviewRequest(BaseModel):
    """人工审核请求"""
    thread_id: str
    task_id: str
    revision: int
    approved: bool
    advise: Optional[str] = None


class RiskReviewDecisionRequest(BaseModel):
    """High-risk tool call review decision."""

    thread_id: str
    review_id: str
    status: RiskReviewStatus
    comment: Optional[str] = None
    modified_arguments: Optional[dict[str, Any]] = None
    reviewer: Optional[str] = None
    tool_call: Optional[ToolCallPlan] = None


class RiskReviewDecisionResponse(BaseModel):
    """Normalized high-risk tool call review response."""

    thread_id: str
    review_id: str
    status: RiskReviewStatus
    feedback: HumanFeedback


class SessionCreateRequest(BaseModel):
    """创建新会话请求"""
    session_name: Optional[str] = None


class DemoCaseListResponse(BaseModel):
    """Replayable demo case summaries."""

    cases: list[dict[str, Any]]


class DemoCaseDetailResponse(BaseModel):
    """Replayable demo case with deterministic replay summary."""

    case: dict[str, Any]
    replay: dict[str, Any]


class DemoCaseTraceResponse(BaseModel):
    """Raw demo trace payload."""

    case_id: str
    trace: dict[str, Any]


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
    - interrupt_before: 在 risk_review_node 前中断（高风险工具审核）
    - interrupt_after: 在 executor_node 后中断（结果确认）
    """
    global _app_with_checkpointer
    if _app_with_checkpointer is None:
        async with _app_with_checkpointer_lock:
            if _app_with_checkpointer is None:
                _app_with_checkpointer = await get_graph(
                    with_checkpointer=True,
                    interrupt_before=["human_review_node", "risk_review_node"],
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


def build_plan_review_update(
    state_values: Dict[str, Any],
    approved: bool,
    advise: Optional[str] = None,
    task_id: str | None = None,
    revision: int | None = None,
) -> Dict[str, Any]:
    """Approve or reject the exact current TaskPlan revision."""

    raw_plan = state_values.get("task_plan")
    if raw_plan is None:
        raise ValueError("没有待审核的 TaskPlan")
    task_plan = raw_plan if isinstance(raw_plan, TaskPlan) else TaskPlan.model_validate(raw_plan)
    if task_id != task_plan.task_id or revision != task_plan.revision:
        raise HTTPException(status_code=409, detail="stale TaskPlan review decision")
    if task_plan.review_status != "pending_review":
        raise HTTPException(status_code=409, detail="TaskPlan is not awaiting review")

    validation_report = PlanValidator().validate(task_plan)
    if not validation_report.is_valid:
        raise ValueError(f"TaskPlan 校验失败，不能审核：{validation_report.summary}")

    if approved:
        return {
            "task_plan": task_plan.model_copy(update={"review_status": "approved"}),
            "plan_validation": validation_report,
            "planning_error": None,
            "advise": None,
        }

    revision_advice = (advise or "").strip()
    if not revision_advice:
        raise ValueError("拒绝计划时必须填写修改意见")
    return {
        "task_plan": task_plan.model_copy(update={"review_status": "rejected"}),
        "advise": revision_advice,
        "planning_error": None,
    }


def build_risk_review_feedback(
    request: RiskReviewDecisionRequest,
    tool_call: ToolCallPlan,
) -> HumanFeedback:
    """Build normalized human feedback for a high-risk tool call."""

    return HumanFeedback(
        review_id=request.review_id,
        status=request.status,
        tool_call=tool_call,
        comment=request.comment,
        modified_arguments=request.modified_arguments,
        reviewer=request.reviewer,
        metadata={"thread_id": request.thread_id},
    )


def normalize_human_feedback(feedback: HumanFeedback | dict[str, Any]) -> HumanFeedback:
    """Accept model or checkpoint-restored dict feedback."""

    if isinstance(feedback, HumanFeedback):
        return feedback
    return HumanFeedback.model_validate(feedback)


def resolve_risk_review_tool_call(
    request: RiskReviewDecisionRequest,
    pending_risk_review: HumanFeedback | dict[str, Any] | None,
) -> ToolCallPlan | None:
    """Resolve the reviewed tool call and reject stale pending-review decisions."""

    if pending_risk_review is None:
        raise HTTPException(status_code=409, detail="No risk review is pending.")

    pending_feedback = normalize_human_feedback(pending_risk_review)
    if pending_feedback.status != "pending":
        raise HTTPException(status_code=409, detail="Risk review is no longer pending.")
    if request.review_id != pending_feedback.review_id:
        raise HTTPException(
            status_code=409,
            detail="stale risk review decision: review_id does not match pending review",
        )
    return pending_feedback.tool_call


def serialize_risk_review_required(
    feedback: HumanFeedback | dict[str, Any]
) -> dict[str, Any]:
    """Build the SSE payload for a pending high-risk tool call review."""

    feedback = normalize_human_feedback(feedback)
    return {
        "review_id": feedback.review_id,
        "status": feedback.status,
        "reason": feedback.reason,
        "comment": feedback.comment,
        "tool_call": feedback.tool_call.model_dump(mode="json"),
        "metadata": feedback.metadata,
        "timestamp": datetime.now().isoformat(),
    }


def build_chat_input_state(
    request: ChatRequest,
    thread_id: str,
    existing_values: Dict[str, Any] | None,
    uploaded_files: list[dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build graph input for a chat request.

    A new checkpoint thread needs the full AgentState because planner/executor
    nodes read required keys such as session_id. Existing threads can receive a
    small delta and let the checkpointer preserve accumulated state.
    """

    llm_config = (
        request.llm_config.model_dump(exclude_none=True)
        if request.llm_config
        else None
    )
    new_task_id = str(uuid.uuid4())
    if not existing_values:
        initial_state = create_initial_state(
            session_id=thread_id,
            input_query=request.message,
            llm_config=llm_config,
            task_id=new_task_id,
            uploaded_files=uploaded_files or [],
        )
        return dict(initial_state)

    return {
        "input_query": request.message,
        "task_id": new_task_id,
        "uploaded_files": uploaded_files or [],
        "llm_config": llm_config,
        "task_plan": None,
        "plan_validation": None,
        "planning_error": None,
        "retry_count": 0,
        "advise": None,
        "execution_trace": None,
        "execution_cursor": None,
        "pending_risk_review": None,
        "risk_review_feedback": None,
        "is_completed": None,
        "user_feedback": None,
        "final_summary": None,
        "screenshot_path": None,
        "quality_score": 0.0,
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
    }


async def stream_agent_execution(
    app,
    initial_state: Dict[str, Any] | None,
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
            "message": "继续执行任务" if initial_state is None else "开始执行任务"
        })

        # 用于跟踪节点状态
        current_node = None
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

        raw_task_plan = state_values.get("task_plan")
        task_plan = (
            raw_task_plan
            if isinstance(raw_task_plan, TaskPlan)
            else TaskPlan.model_validate(raw_task_plan)
            if raw_task_plan is not None
            else None
        )
        messages = state_values.get("messages", [])
        is_completed = state_values.get("is_completed")
        pending_risk_review = state_values.get("pending_risk_review")

        if state_values.get("planning_error"):
            yield format_sse_event("error", {
                "message": state_values["planning_error"],
                "timestamp": datetime.now().isoformat(),
            })
            return

        if task_plan and task_plan.review_status == "pending_review":
            validation_report = PlanValidator().validate(task_plan)
            if not validation_report.is_valid:
                yield format_sse_event("error", {
                    "message": validation_report.summary,
                    "timestamp": datetime.now().isoformat(),
                })
                return
            yield format_sse_event("human_review_required", {
                "task_plan": task_plan.model_dump(mode="json"),
                "timestamp": datetime.now().isoformat(),
            })
            return

        # 情况 2：Executor → 风险审核
        if pending_risk_review:
            logger.info("[SSE] CLI判定：需要风险审核")
            yield format_sse_event(
                "risk_review_required",
                serialize_risk_review_required(pending_risk_review),
            )
            return

        if messages and is_completed is False and task_plan and task_plan.review_status == "approved":
            yield format_sse_event("error", {
                "message": str(getattr(messages[-1], "content", "执行未完成，请核查。")),
                "timestamp": datetime.now().isoformat(),
            })
            return

        # 情况 3：Executor → 结果确认
        if messages and is_completed is None:
            logger.info("[SSE] CLI判定：需要确认执行结果")

            execution_summary = build_execution_summary(messages, max_items=1)

            yield format_sse_event("result_review_required", {
                "execution_summary": execution_summary or "执行完成",
                "messages_count": len(messages),
                "timestamp": datetime.now().isoformat()
            })
            return

        # 情况 4：最终完成
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
    """Project the checkpoint's TaskPlan and PostgreSQL business records."""
    app_instance = await get_app_with_checkpointer()
    current_state = await app_instance.aget_state({"configurable": {"thread_id": thread_id}})
    if not current_state or not current_state.values:
        raise HTTPException(status_code=404, detail="会话不存在。")

    values = current_state.values
    plan = values.get("task_plan")
    if isinstance(plan, TaskPlan):
        plan = plan.model_dump(mode="json")
    history = values.get("history") or []
    chat_messages = []
    for entry in history:
        if entry.get("input_query"):
            chat_messages.append({"role": "user", "content": entry["input_query"]})
        if entry.get("executor_last_message"):
            chat_messages.append({"role": "assistant", "content": entry["executor_last_message"]})
        if entry.get("final_summary"):
            chat_messages.append({"role": "assistant", "content": entry["final_summary"]})
    input_query = values.get("input_query") or ""
    if input_query:
        chat_messages.append({"role": "user", "content": input_query})
    if plan:
        chat_messages.append({"role": "assistant", "content": plan.get("task_summary", "")})

    try:
        executions = await asyncio.to_thread(list_task_executions_for_session, thread_id)
    except Exception as exc:
        logger.error("Could not query session business records: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="无法查询会话业务记录。") from exc
    return {
        "thread_id": thread_id,
        "input_query": input_query,
        "chat_messages": chat_messages,
        "task_plan": plan,
        "executions": executions,
        "final_summary": values.get("final_summary"),
        "screenshot_path": values.get("screenshot_path"),
        "log_summary": values.get("log_summary"),
        "history": history,
    }


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
    """Store, hash, inspect, and register one small GeoJSON or GeoPackage."""

    original_filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(original_filename).suffix.lower()
    if not original_filename or extension not in SUPPORTED_VECTOR_EXTENSIONS:
        raise HTTPException(status_code=415, detail="仅支持 GeoJSON 和 GeoPackage 矢量文件。")

    file_id = str(uuid.uuid4())
    storage_path = f"{file_id}{extension}"
    file_path = UPLOAD_DIR / storage_path
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with file_path.open("xb") as destination:
            while chunk := await file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > MAX_VECTOR_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="矢量文件超过上传大小限制。")
                digest.update(chunk)
                destination.write(chunk)

        layers = await asyncio.to_thread(inspect_uploaded_vector, file_path)
        sha256 = digest.hexdigest()
        await asyncio.to_thread(
            save_uploaded_file_record,
            file_id,
            original_filename,
            storage_path,
            size_bytes,
            sha256,
            layers,
        )
        logger.info("Registered vector upload %s (%d bytes)", file_id, size_bytes)
        return serialize_uploaded_file_record(
            {
                "file_id": file_id,
                "filename": original_filename,
                "storage_path": storage_path,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "layers": layers,
                "uploaded_at": datetime.now().isoformat(),
            }
        )
    except HTTPException:
        file_path.unlink(missing_ok=True)
        raise
    except ValueError as exc:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as e:
        file_path.unlink(missing_ok=True)
        logger.error("File upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail="文件保存或元数据注册失败。") from e
    finally:
        await file.close()


@app.get("/api/files")
async def list_files():
    """List registered uploads with inspected layer metadata, never local paths."""
    try:
        records = await asyncio.to_thread(list_uploaded_file_records)
        return {"files": [serialize_uploaded_file_record(record) for record in records]}

    except Exception as e:
        logger.error("Listing uploaded files failed: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail="无法查询项目文件记录。") from e


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
        uploaded_files = await asyncio.to_thread(resolve_selected_files, request.files)
        # 获取 Graph 实例
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": thread_id}}

        state_snapshot = await app_instance.aget_state(config)
        input_state = build_chat_input_state(
            request=request,
            thread_id=thread_id,
            existing_values=state_snapshot.values if state_snapshot else {},
            uploaded_files=uploaded_files,
        )

        logger.info(f"[Chat] 开始新一轮对话, thread_id: {thread_id}")

        # 返回 SSE 流
        return start_owned_stream(
            app_instance, input_state, thread_id, config,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"聊天执行失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"执行失败: {str(e)}")


_checkpoint_decisions: set[asyncio.Task] = set()


def _checkpoint_decision_finished(task: asyncio.Task) -> None:
    _checkpoint_decisions.discard(task)
    # Retrieve failures even when the original HTTP subscriber was cancelled.
    # Handlers already report persistence failures and HTTP validation errors.
    if not task.cancelled():
        task.exception()


def own_checkpoint_decision(handler):
    """Own a complete decision independently of the HTTP request subscriber."""
    @wraps(handler)
    async def decide(request):
        try:
            run = ThreadRun(request.thread_id)
        except ExecutionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        async def apply_decision():
            try:
                # Both durable save and checkpoint update belong to this owner.
                return await handler(request)
            finally:
                run.release()

        owned_task = asyncio.create_task(apply_decision())
        _checkpoint_decisions.add(owned_task)
        owned_task.add_done_callback(_checkpoint_decision_finished)
        return await asyncio.shield(owned_task)
    return decide


@app.post("/api/review")
@own_checkpoint_decision
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

        try:
            update_values = build_plan_review_update(
                state_values,
                approved=request.approved,
                advise=request.advise,
                task_id=request.task_id,
                revision=request.revision,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        approved_plan = update_values["task_plan"]
        try:
            await asyncio.to_thread(
                save_task_execution_record,
                task_id=approved_plan.task_id,
                session_id=state_values.get("session_id", request.thread_id),
                plan_revision=approved_plan.revision,
                status="approved" if request.approved else "rejected",
                plan=approved_plan.model_dump(mode="json"),
                attempts=[],
            )
        except Exception as exc:
            logger.error("Could not persist TaskPlan review: %s", exc, exc_info=True)
            raise HTTPException(status_code=503, detail="无法保存任务审核记录。") from exc

        # 更新状态
        await app_instance.aupdate_state(config, update_values)

        logger.info(f"审核结果: {'批准' if request.approved else '拒绝'} - {request.thread_id}")

        return {
            "status": "success",
            "approved": request.approved,
            "message": "审核完成，Agent 将继续执行"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"审核失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"审核失败: {str(e)}")


@app.get("/api/executions/{task_id}")
async def get_execution(task_id: str):
    """Read the PostgreSQL business record for one task and its step attempts."""

    try:
        record = await asyncio.to_thread(get_task_execution_record, task_id)
    except Exception as exc:
        logger.error("Execution record query failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="无法查询任务业务记录。") from exc
    if record is None:
        raise HTTPException(status_code=404, detail="任务记录不存在。")

    execution = record["execution"]
    return {
        "execution": {
            "task_id": execution["task_id"],
            "session_id": execution["session_id"],
            "plan_revision": execution["plan_revision"],
            "status": execution["status"],
            "inflight_call": execution.get("inflight_call"),
            "plan": execution["plan"],
            "attempts": record["attempts"],
            "artifacts": record["artifacts"],
            "confirmed": execution["confirmed"],
            "summary": execution["summary"],
        }
    }


@app.post("/api/risk-review", response_model=RiskReviewDecisionResponse)
@own_checkpoint_decision
async def review_risky_tool_call(request: RiskReviewDecisionRequest):
    """Normalize a high-risk tool call review decision for resume flows."""

    if request.status == "modified":
        raise HTTPException(
            status_code=409,
            detail="修改调用参数需要生成并审核新版本 TaskPlan。",
        )
    if request.status not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="请选择批准或拒绝。")

    app_instance = await get_app_with_checkpointer()
    config = {"configurable": {"thread_id": request.thread_id}}
    current_state = await app_instance.aget_state(config)
    state_values = current_state.values if current_state else {}
    pending_risk_review = state_values.get("pending_risk_review")

    tool_call = resolve_risk_review_tool_call(request, pending_risk_review)
    if tool_call is None:
        raise HTTPException(
            status_code=400,
            detail="tool_call is required to build risk review feedback",
        )

    feedback = build_risk_review_feedback(request, tool_call)
    update_values = {
        "risk_review_feedback": feedback,
        "pending_risk_review": None,
    }
    try:
        await app_instance.aupdate_state(
            config,
            update_values,
            as_node="risk_review_node",
        )
    except TypeError:
        await app_instance.aupdate_state(config, update_values)

    return RiskReviewDecisionResponse(
        thread_id=request.thread_id,
        review_id=request.review_id,
        status=request.status,
        feedback=feedback,
    )


@app.get("/api/demo-cases", response_model=DemoCaseListResponse)
async def list_demo_cases():
    """List static replayable demo cases for trace inspection."""

    return DemoCaseListResponse(
        cases=[summarize_demo_case(case) for case in load_demo_cases()]
    )


@app.get("/api/demo-cases/{case_id}", response_model=DemoCaseDetailResponse)
async def get_demo_case(case_id: str):
    """Return one demo case plus a deterministic replay summary."""

    try:
        case = load_demo_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return DemoCaseDetailResponse(
        case=summarize_demo_case(case),
        replay=replay_demo_case(case).model_dump(mode="json"),
    )


@app.get("/api/demo-cases/{case_id}/trace", response_model=DemoCaseTraceResponse)
async def get_demo_case_trace(case_id: str):
    """Return the raw static ExecutionTrace fixture for one demo case."""

    try:
        case = load_demo_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return DemoCaseTraceResponse(
        case_id=case.case_id,
        trace=case.trace.model_dump(mode="json"),
    )


class ResultConfirmRequest(BaseModel):
    """执行结果确认请求"""
    thread_id: str
    is_completed: bool  # True=成功, False=失败
    user_feedback: Optional[str] = None


@app.post("/api/confirm-result")
@own_checkpoint_decision
async def confirm_result(request: ResultConfirmRequest):
    """
    确认执行结果

    用户在 Executor 执行完成后确认任务是否成功完成
    """
    try:
        # 获取 Graph 实例
        app_instance = await get_app_with_checkpointer()
        config = {"configurable": {"thread_id": request.thread_id}}

        snapshot = await app_instance.aget_state(config)
        values = snapshot.values if snapshot else {}
        task_plan = values.get("task_plan")
        task_plan = (
            task_plan if isinstance(task_plan, TaskPlan)
            else TaskPlan.model_validate(task_plan) if task_plan is not None
            else None
        )
        if (
            task_plan is None
            or values.get("execution_trace") is None
            or values.get("pending_risk_review") is not None
            or values.get("is_completed") is not None
        ):
            raise HTTPException(status_code=409, detail="当前任务没有待确认的执行结果。")

        feedback = None
        if not request.is_completed:
            feedback = (request.user_feedback or "").strip() or None
        if not await asyncio.to_thread(
            confirm_task_execution, task_plan.task_id, request.is_completed, feedback
        ):
            raise HTTPException(status_code=404, detail="任务业务记录不存在。")

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"结果确认失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"结果确认失败: {str(e)}")


_graph_producers: set[asyncio.Task] = set()


class ExecutionStreamingResponse(StreamingResponse):
    """A disposable SSE subscriber; the graph producer owns execution resources."""

    def __init__(self, *args, disconnected, **kwargs):
        super().__init__(*args, **kwargs)
        self.disconnected = disconnected

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.disconnected.set()


def _resume_enters_gis(snapshot) -> bool:
    next_nodes = set(snapshot.next or ())
    if 'executor_node' in next_nodes or 'risk_review_node' in next_nodes:
        return True
    plan = snapshot.values.get('task_plan')
    status = plan.get('review_status') if isinstance(plan, dict) else getattr(plan, 'review_status', None)
    return 'human_review_node' in next_nodes and status == 'approved'


def start_owned_stream(app_instance, initial_state, thread_id, config, *, slot=None, **kwargs):
    try:
        run = ThreadRun(thread_id)
    except ExecutionBusy as exc:
        if slot is not None:
            slot.release()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    disconnected = asyncio.Event()
    backlog = asyncio.Queue(maxsize=128)
    terminal = object()

    async def publish(event):
        if disconnected.is_set():
            return
        try:
            backlog.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        # Preserve every event for a connected reader. A disconnect releases
        # this transport wait without cancelling the graph or GIS operation.
        pending_put = asyncio.create_task(backlog.put(event))
        pending_disconnect = asyncio.create_task(disconnected.wait())
        try:
            await asyncio.wait({pending_put, pending_disconnect}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pending in (pending_put, pending_disconnect):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(pending_put, pending_disconnect, return_exceptions=True)

    async def produce():
        try:
            if slot is None:
                async for event in stream_agent_execution(app_instance, initial_state, thread_id, config):
                    await publish(event)
            else:
                with slot.activate():
                    async for event in stream_agent_execution(app_instance, initial_state, thread_id, config):
                        await publish(event)
        except Exception as exc:
            logger.error('Graph producer failed for thread %s: %s', thread_id, exc)
            await publish(format_sse_event('error', {'message': str(exc)}))
        finally:
            if slot is not None:
                slot.release()
            run.release()
            await publish(terminal)

    async def read_events():
        try:
            while True:
                event = await backlog.get()
                if event is terminal:
                    break
                yield event
        finally:
            disconnected.set()

    producer = asyncio.create_task(produce())
    _graph_producers.add(producer)
    producer.add_done_callback(_graph_producers.discard)
    return ExecutionStreamingResponse(read_events(), disconnected=disconnected, **kwargs)


@app.post("/api/resume/{thread_id}")
async def resume_execution(thread_id: str):
    """Start one owned graph producer and attach its SSE transport subscriber."""
    app_instance = await get_app_with_checkpointer()
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await app_instance.aget_state(config)
    slot = None
    if _resume_enters_gis(snapshot):
        plan = snapshot.values.get('task_plan')
        task_id = plan.get('task_id') if isinstance(plan, dict) else getattr(plan, 'task_id', None)
        try:
            slot = ExecutionSlot(task_id)
        except ExecutionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return start_owned_stream(
        app_instance, None, thread_id, config, slot=slot,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
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
    return serve_spa_index()


def serve_spa_index():
    """Serve built frontend index.html for Vue history-mode routes."""

    html_file = FRONTEND_DIR / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return {"message": "UI not found"}


@app.get("/{full_path:path}")
async def serve_spa_fallback(full_path: str):
    """Return index.html for frontend routes when the built SPA is served by FastAPI."""

    if full_path.startswith(("api/", "assets/", "shared/")):
        raise HTTPException(status_code=404, detail="Not found")
    return serve_spa_index()


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
