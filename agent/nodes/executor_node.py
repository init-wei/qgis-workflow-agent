#!/usr/bin/env python3
"""Controlled executor node: TaskPlan -> ToolCallPlan -> ExecutionLoop."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage

from agent.planning.legacy_adapter import legacy_plan_to_task_plan
from agent.state import AgentState
from agent.tools.execution_loop import ExecutionLoop
from agent.tools.mcp_client import get_mcp_client
from agent.tools.tool_call_planner import compile_task_plan_to_tool_calls
from agent.tools.tool_contracts import ExecutionTrace, LoopResult
from agent.tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


async def executor_node(state: AgentState) -> dict[str, Any]:
    """Execute the reviewed TaskPlan through the controlled loop runtime."""

    session_id = state["session_id"]
    task_plan = state.get("task_plan")
    if task_plan is None and state.get("plan") is not None:
        task_plan = legacy_plan_to_task_plan(
            state["plan"], user_query=state.get("input_query")
        )

    if task_plan is None:
        return {
            "messages": [AIMessage(content="受控执行未开始: 没有已批准的 TaskPlan。")],
            "is_completed": False,
        }

    tool_calls = compile_task_plan_to_tool_calls(task_plan)
    if not tool_calls:
        trace = ExecutionTrace(
            trace_id=f"trace-{uuid4()}",
            task_id=task_plan.task_id,
            status="failed",
            metadata={"session_id": session_id, "reason": "no_tool_calls"},
        )
        return {
            "execution_trace": trace,
            "messages": [
                AIMessage(
                    content=(
                        "受控执行未开始: TaskPlan 没有可执行的 tool_calls。"
                        "请在计划 metadata.tool_calls 中提供明确工具调用。"
                    )
                )
            ],
            "is_completed": False,
        }

    mcp_client = await get_mcp_client()
    registry = ToolRegistry.from_mcp_tools(await mcp_client.list_tools())
    loop = ExecutionLoop(
        mcp_client=mcp_client,
        registry=registry,
        workspace_root=Path.cwd(),
    )
    trace = state.get("execution_trace") or ExecutionTrace(
        trace_id=f"trace-{uuid4()}",
        task_id=task_plan.task_id,
        metadata={
            "session_id": session_id,
            "task_summary": task_plan.task_summary,
            "runtime": "controlled_execution_loop",
        },
    )

    allowed_tools = state.get("tool_selection")
    start_index = state.get("execution_cursor") or 0
    risk_review_feedback = state.get("risk_review_feedback")
    loop_results: list[LoopResult] = []
    for call_index, call in enumerate(tool_calls[start_index:], start=start_index):
        logger.info("Controlled executor calling %s for step %s", call.tool_name, call.step_id)
        human_feedback = risk_review_feedback if call_index == start_index else None
        result = await loop.run(
            call,
            trace,
            allowed_tools=allowed_tools,
            approved=call.requires_review is False,
            human_feedback=human_feedback,
        )
        risk_review_feedback = None
        loop_results.append(result)
        if result.status != "success":
            if result.status == "needs_review":
                return _risk_review_response(trace, loop_results, call_index)
            return _failure_response(trace, loop_results)

    trace.status = "success"
    return {
        "execution_trace": trace,
        "messages": [_success_message(trace, loop_results)],
        "screenshot_path": _latest_artifact_path(trace, ".png"),
        "pending_risk_review": None,
        "risk_review_feedback": None,
        "execution_cursor": None,
        "is_completed": None,
    }


def _success_message(trace: ExecutionTrace, loop_results: list[LoopResult]) -> AIMessage:
    artifact_paths = [
        artifact.get("path")
        for attempt in trace.attempts
        for artifact in attempt.artifacts
        if artifact.get("path")
    ]
    artifact_text = (
        "\n输出文件:\n" + "\n".join(f"- {path}" for path in artifact_paths)
        if artifact_paths
        else ""
    )
    return AIMessage(
        content=(
            f"受控执行完成: {len(loop_results)} 个工具调用成功，"
            f"记录 {len(trace.attempts)} 次 attempt。{artifact_text}"
        )
    )


def _failure_response(
    trace: ExecutionTrace, loop_results: list[LoopResult]
) -> dict[str, Any]:
    final_result = loop_results[-1]
    return {
        "execution_trace": trace,
        "messages": [
            AIMessage(
                content=(
                    "受控执行停止: "
                    f"{final_result.status}; {final_result.decision.reason}; "
                    f"{final_result.decision.message or ''}"
                )
            )
        ],
        "pending_risk_review": None,
        "risk_review_feedback": None,
        "execution_cursor": None,
        "is_completed": False,
    }


def _risk_review_response(
    trace: ExecutionTrace, loop_results: list[LoopResult], call_index: int
) -> dict[str, Any]:
    latest_attempt = trace.latest_attempt()
    risk_review = None
    if latest_attempt is not None:
        risk_review_data = latest_attempt.metadata.get("risk_review")
        if isinstance(risk_review_data, dict):
            from agent.tools.tool_contracts import HumanFeedback

            risk_review = HumanFeedback.model_validate(risk_review_data)
    final_result = loop_results[-1]
    return {
        "execution_trace": trace,
        "messages": [
            AIMessage(
                content=(
                    "风险审核待处理: "
                    f"{final_result.decision.reason}; "
                    f"{final_result.decision.message or ''}"
                )
            )
        ],
        "pending_risk_review": risk_review,
        "risk_review_feedback": None,
        "execution_cursor": call_index,
        "is_completed": None,
    }


def _latest_artifact_path(trace: ExecutionTrace, suffix: str) -> str | None:
    for attempt in reversed(trace.attempts):
        for artifact in reversed(attempt.artifacts):
            path = artifact.get("path")
            if isinstance(path, str) and path.lower().endswith(suffix):
                return path
    return None
