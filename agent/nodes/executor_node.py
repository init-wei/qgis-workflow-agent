#!/usr/bin/env python3
"""Controlled executor node: TaskPlan -> ToolCallPlan -> ExecutionLoop."""

from __future__ import annotations

import logging
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage

from agent.planning.plan_schema import TaskPlan
from agent.planning.plan_validator import PlanValidator
from agent.planning.processing_preflight import validate_original_inputs, validate_processing_calls
from agent.state import AgentState
from agent.tools.execution_loop import ExecutionLoop
from agent.tools.mcp_client import get_mcp_client
from agent.tools.tool_call_planner import compile_task_plan_to_tool_calls
from agent.tools.tool_contracts import ExecutionTrace, LoopResult
from agent.tools.database import save_task_execution_record
from agent.tools.execution_guard import block_unknown_task, execution_slot
from agent.tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


async def executor_node(state: AgentState) -> dict[str, Any]:
    """Execute the reviewed TaskPlan through the controlled loop runtime."""
    return await controlled_executor_node(state)


async def controlled_executor_node(state: AgentState) -> dict[str, Any]:
    """Execute the reviewed TaskPlan through the one non-queued process slot."""
    task_plan = state.get("task_plan")
    task_id = task_plan.get("task_id") if isinstance(task_plan, dict) else getattr(task_plan, "task_id", None)
    with execution_slot(task_id):
        return await _execute_reviewed_plan(state)


async def _execute_reviewed_plan(state: AgentState) -> dict[str, Any]:
    session_id = state["session_id"]
    task_plan = state.get("task_plan")
    if task_plan is None:
        return {
            "messages": [AIMessage(content="受控执行未开始：没有可执行的 TaskPlan。")],
            "is_completed": None,
        }

    if isinstance(task_plan, dict):
        task_plan = TaskPlan.model_validate(task_plan)

    if task_plan.review_status != "approved":
        return {
            "messages": [AIMessage(content="受控执行未开始：当前 TaskPlan 未批准。")],
            "is_completed": None,
        }

    tool_calls = compile_task_plan_to_tool_calls(task_plan)
    trace = state.get("execution_trace")
    if isinstance(trace, dict):
        trace = ExecutionTrace.model_validate(trace)
    trace = trace or ExecutionTrace(
        trace_id=f"trace-{uuid4()}",
        task_id=task_plan.task_id,
        metadata={"session_id": session_id, "task_summary": task_plan.task_summary,
                  "runtime": "controlled_execution_loop"},
    )
    start_index = state.get("execution_cursor") or 0
    feedback = state.get("risk_review_feedback") or state.get("pending_risk_review")
    checkpoint_resume = None
    completed_step_ids = frozenset()
    if trace.attempts or start_index or feedback is not None:
        checkpoint_resume = _resume_proof(trace, start_index, feedback)

    # Durable facts only veto a checkpoint's requested position; they never
    # supply a cursor or advance the graph. Match before trusting old outputs.
    await _persist_execution_record(
        task_plan, session_id, trace, "running", claim_execution=True,
        checkpoint_resume=checkpoint_resume,
    )
    if checkpoint_resume is not None:
        completed_step_ids = _completed_prefix(task_plan, trace, start_index, feedback)
    validation_report = PlanValidator().validate(task_plan, completed_step_ids=completed_step_ids)
    if not validation_report.is_valid:
        await _persist_execution_record(task_plan, session_id, trace, "failed")
        trace.status = 'failed'
        return {
            "execution_trace": trace,
            "messages": [AIMessage(content="受控执行未开始：当前 TaskPlan 校验失败。 " + validation_report.summary)],
            "is_completed": None,
        }
    if not tool_calls:
        raise RuntimeError("Validated TaskPlan unexpectedly contains no tool calls.")

    try:
        await asyncio.to_thread(validate_original_inputs, task_plan)
        mcp_client = await get_mcp_client()
        await validate_processing_calls(task_plan, mcp_client)
        tools = await mcp_client.list_tools()
        _validate_available_tools(task_plan, tools)
        registry = ToolRegistry.from_mcp_tools(tools)
        loop = ExecutionLoop(mcp_client=mcp_client, registry=registry,
                             workspace_root=Path.cwd(), max_attempts=1)
    except Exception:
        await _persist_execution_record(task_plan, session_id, trace, "failed")
        raise

    allowed_tools = sorted(
        {tool_name for step in task_plan.steps for tool_name in step.allowed_tools}
    )
    risk_review_feedback = state.get("risk_review_feedback")
    loop_results: list[LoopResult] = []
    for call_index, call in enumerate(tool_calls[start_index:], start=start_index):
        logger.info("Controlled executor calling %s for step %s", call.tool_name, call.step_id)
        human_feedback = risk_review_feedback if call_index == start_index else None
        await _persist_execution_record(
            task_plan, session_id, trace, "running",
            inflight_call=call.model_dump(mode="json"),
        )
        try:
            result = await loop.run(
                call, trace, allowed_tools=allowed_tools,
                approved=call.requires_review is False, human_feedback=human_feedback,
            )
        except BaseException:
            block_unknown_task(task_plan.task_id)
            try:
                await _persist_execution_record(task_plan, session_id, trace, "needs_attention",
                                                inflight_call=call.model_dump(mode='json'))
            except Exception:
                logger.error('Could not persist interrupted operation outcome for task %s', task_plan.task_id)
            raise
        risk_review_feedback = None
        loop_results.append(result)
        latest_attempt = trace.latest_attempt()
        unknown_outcome = (
            latest_attempt is not None and latest_attempt.observation.outcome == "unknown"
        )
        risk_review_response = (
            _risk_review_response(trace, loop_results, call_index)
            if result.status == "needs_review"
            else None
        )
        record_status = (
            "needs_attention"
            if unknown_outcome
            else "needs_review"
            if risk_review_response is not None
            else "failed"
            if result.status != "success"
            else "running"
        )
        if unknown_outcome:
            block_unknown_task(task_plan.task_id)
        try:
            await _persist_execution_record(
                task_plan, session_id, trace, record_status,
                resume_state=_resume_proof(trace, call_index, risk_review_response['pending_risk_review'])
                if risk_review_response is not None else None,
            )
        except BaseException:
            # A dispatched call with an unsaved result cannot be treated as a
            # released, safely retryable remote operation.
            if result.status != "needs_review":
                block_unknown_task(task_plan.task_id)
            raise
        if result.status != "success":
            if risk_review_response is not None:
                return risk_review_response
            return _failure_response(trace, loop_results)

    trace.status = "success"
    await _persist_execution_record(task_plan, session_id, trace, "success")
    return {
        "execution_trace": trace,
        "messages": [_success_message(trace, loop_results)],
        "screenshot_path": _latest_artifact_path(trace, ".png"),
        "pending_risk_review": None,
        "risk_review_feedback": None,
        "execution_cursor": None,
        "is_completed": None,
    }


def _resume_proof(trace: ExecutionTrace, cursor: int, feedback) -> dict:
    """Compact comparison evidence, derived exclusively from checkpoint values."""
    if hasattr(feedback, 'model_dump'):
        feedback = feedback.model_dump(mode='json')
    feedback = feedback or {}
    attempts = [attempt.model_dump(mode='json') for attempt in trace.attempts]
    for attempt in attempts:
        attempt['observation'].pop('raw_response', None)
    digest = hashlib.sha256(json.dumps(attempts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'cursor': cursor, 'trace_id': trace.trace_id,
            'review_id': feedback.get('review_id'), 'tool_call': feedback.get('tool_call'),
            'attempt_ids': [attempt.attempt_id for attempt in trace.attempts],
            'attempt_digest': digest}


def _completed_prefix(plan: TaskPlan, trace: ExecutionTrace, cursor: int, feedback) -> frozenset[int]:
    """Validate the checkpoint's ordered successes and current risk pause."""
    if trace.task_id != plan.task_id or not isinstance(cursor, int) or not 0 <= cursor < len(plan.tool_calls):
        raise RuntimeError('Checkpoint execution position needs attention.')
    index = 0
    for attempt in trace.attempts:
        if index >= len(plan.tool_calls) or attempt.tool_call != plan.tool_calls[index]:
            raise RuntimeError('Checkpoint attempts differ from the current plan; needs attention.')
        if attempt.status == 'success' and attempt.observation.outcome != 'unknown':
            for artifact in attempt.artifacts:
                if artifact.get('kind') == 'path':
                    for item in [artifact, *artifact.get('components', [])]:
                        path = Path(item['path'])
                        digest = hashlib.sha256()
                        with path.open('rb') as stream:
                            while chunk := stream.read(1024 * 1024):
                                digest.update(chunk)
                        if (path.stat().st_size != item.get('size_bytes')
                                or digest.hexdigest() != item.get('sha256')):
                            raise RuntimeError('Completed checkpoint artifact changed; needs attention.')
            index += 1
        elif attempt.status != 'blocked' or not attempt.metadata.get('risk_review'):
            raise RuntimeError('Checkpoint attempts are not a completed prefix; needs attention.')
    latest = trace.latest_attempt()
    proof = _resume_proof(trace, cursor, feedback)
    review = latest.metadata.get('risk_review') if latest is not None else None
    if index != cursor or latest is None or latest.status != 'blocked' or not review:
        raise RuntimeError('Checkpoint does not identify its current risk pause; needs attention.')
    if proof['review_id'] != review.get('review_id') or proof['tool_call'] != review.get('tool_call'):
        raise RuntimeError('Checkpoint risk decision differs from its pause; needs attention.')
    return frozenset(call.step_id for call in plan.tool_calls[:cursor])


def _validate_available_tools(task_plan: TaskPlan, tools: list[dict]) -> None:
    from qgis_mcp_server.qgis_mcp_server import tool_argument_model

    available = {tool["name"]: tool for tool in tools}
    for call in task_plan.tool_calls:
        tool = available.get(call.tool_name)
        if tool is None:
            raise ValueError(f"Planned MCP tool is unavailable: {call.tool_name}")
        # Production clients always retain the actual schema. Minimal GIS fakes
        # may omit it; local SDK validation still applies to those test adapters.
        schema = tool.get("input_schema")
        if schema is not None and schema != tool_argument_model(call.tool_name).model_json_schema():
            raise ValueError(f"MCP schema differs from the paired contract: {call.tool_name}")


async def _persist_execution_record(
    task_plan: TaskPlan,
    session_id: str,
    trace: ExecutionTrace,
    status: str,
    *,
    claim_execution: bool = False,
    inflight_call: dict | None = None,
    resume_state: dict | None = None,
    checkpoint_resume: dict | None = None,
) -> None:
    """Persist the business task and its actual step attempts independently of telemetry."""

    await asyncio.to_thread(
        save_task_execution_record,
        task_id=task_plan.task_id,
        session_id=session_id,
        plan_revision=task_plan.revision,
        status=status,
        claim_execution=claim_execution,
        inflight_call=inflight_call,
        resume_state=resume_state,
        checkpoint_resume=checkpoint_resume,
        plan=task_plan.model_dump(mode="json"),
        attempts=[attempt.model_dump(mode="json") for attempt in trace.attempts],
    )


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


def _no_tool_calls_diagnostic(task_plan: Any) -> str:
    metadata = task_plan.metadata if isinstance(task_plan.metadata, dict) else {}
    has_metadata_tool_calls = isinstance(metadata.get("tool_calls"), list)
    step_parts = [
        (
            f"step {step.step_id} allowed_tools={list(step.allowed_tools)} "
            f"output_refs={list(step.output_refs)}"
        )
        for step in task_plan.steps
    ]
    fallback_reason = metadata.get("fallback_reason") or metadata.get("planner_error")
    parts = [
        f"task_id={task_plan.task_id}",
        f"steps={len(task_plan.steps)}",
        f"metadata.tool_calls={has_metadata_tool_calls}",
        "; ".join(step_parts),
    ]
    if fallback_reason:
        parts.append(f"fallback_reason={fallback_reason}")
    return " | ".join(parts)


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
        "is_completed": None,
    }


def _risk_review_response(
    trace: ExecutionTrace, loop_results: list[LoopResult], call_index: int
) -> dict[str, Any] | None:
    latest_attempt = trace.latest_attempt()
    risk_review = None
    if latest_attempt is not None:
        risk_review_data = latest_attempt.metadata.get("risk_review")
        if isinstance(risk_review_data, dict):
            from agent.tools.tool_contracts import HumanFeedback

            risk_review = HumanFeedback.model_validate(risk_review_data)
    if risk_review is None:
        return None
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
