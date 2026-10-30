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
from agent.spatial_validation import preflight_spatial_plan, validate_spatial_step
from agent.state import AgentState
from agent.tools.execution_loop import ExecutionLoop
from agent.tools.mcp_client import get_mcp_client
from agent.tools.tool_call_planner import compile_task_plan_to_tool_calls
from agent.tools.tool_contracts import EvaluationDecision, ExecutionTrace, LoopResult
from agent.tools.database import save_task_execution_record
from agent.tools.execution_guard import block_unknown_task, execution_slot
from agent.tools.tool_registry import ToolRegistry
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import require_standalone_sqlite

logger = logging.getLogger(__name__)


async def executor_node(state: AgentState, *, spatial_inspector=None) -> dict[str, Any]:
    """Execute the reviewed TaskPlan through the controlled loop runtime."""
    return await controlled_executor_node(state, spatial_inspector=spatial_inspector)


async def controlled_executor_node(state: AgentState, *, spatial_inspector=None) -> dict[str, Any]:
    """Execute the reviewed TaskPlan through the one non-queued process slot."""
    task_plan = state.get("task_plan")
    task_id = task_plan.get("task_id") if isinstance(task_plan, dict) else getattr(task_plan, "task_id", None)
    with execution_slot(task_id):
        return await _execute_reviewed_plan(state, spatial_inspector=spatial_inspector)


async def _execute_reviewed_plan(state: AgentState, *, spatial_inspector=None) -> dict[str, Any]:
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
        response = _failed_trace(trace, '受控执行未开始：当前 TaskPlan 校验失败。 ' + validation_report.summary)
        await _persist_execution_record(task_plan, session_id, trace, "failed")
        return response
    if not tool_calls:
        raise RuntimeError("Validated TaskPlan unexpectedly contains no tool calls.")

    try:
        await asyncio.to_thread(validate_original_inputs, task_plan)
        await preflight_spatial_plan(task_plan, inspector=spatial_inspector)
        mcp_client = await get_mcp_client()
        await validate_processing_calls(task_plan, mcp_client)
        tools = await mcp_client.list_tools()
        _validate_available_tools(task_plan, tools)
        registry = ToolRegistry.from_mcp_tools(tools)
        loop = ExecutionLoop(mcp_client=mcp_client, registry=registry,
                             workspace_root=Path.cwd(), max_attempts=1)
    except Exception as exc:
        response = _failed_trace(trace, f'受控执行未开始：{exc}')
        await _persist_execution_record(task_plan, session_id, trace, "failed")
        return response

    allowed_tools = sorted(
        {tool_name for step in task_plan.steps for tool_name in step.allowed_tools}
    )
    risk_review_feedback = state.get("risk_review_feedback")
    loop_results: list[LoopResult] = []
    for call_index, call in enumerate(tool_calls[start_index:], start=start_index):
        logger.info("Controlled executor calling %s for step %s", call.tool_name, call.step_id)
        human_feedback = risk_review_feedback if call_index == start_index else None
        try:
            input_paths = await asyncio.to_thread(_verified_inputs, task_plan, trace, call)
        except (OSError, ValueError) as exc:
            response = _failed_trace(trace, f'受控执行停止：无法验证步骤输入。{exc}')
            await _persist_execution_record(task_plan, session_id, trace, 'failed')
            return response
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
        if result.status == 'success' and call.tool_name == 'execute_processing':
            attempt = trace.latest_attempt()
            output_paths = {}
            try:
                output_paths = _actual_outputs(task_plan, call, attempt)
                if await asyncio.to_thread(_verified_inputs, task_plan, trace, call) != input_paths:
                    raise ValueError('Processing inputs changed before spatial validation.')
                report = await validate_spatial_step(
                    call, input_paths=input_paths, output_paths=output_paths,
                    inspector=spatial_inspector,
                )
                if report.get('status') != 'passed':
                    if not report.get('errors'):
                        report = {**report, 'status': 'failed',
                                  'errors': ['Processing result has no applicable spatial validation.']}
                else:
                    # The inspector's verdict applies only to these exact file bytes.
                    for artifact in attempt.artifacts:
                        if artifact.get('kind') == 'path':
                            _verify_recorded_file(artifact)
                    if await asyncio.to_thread(_verified_inputs, task_plan, trace, call) != input_paths:
                        raise ValueError('Processing inputs changed during spatial validation.')
            except Exception as exc:
                report = {'status': 'failed', 'checks': [],
                          'errors': [f'Spatial result could not be verified: {exc}'], 'facts': {}}
            for artifact in attempt.artifacts:
                if artifact.get('kind') == 'path':
                    artifact['spatial_validation'] = report
                    artifact['deliverable'] = report.get('status') == 'passed'
                    artifact['producer_step_id'] = call.step_id
                    artifact['output_ref'] = next(
                        (ref for ref, path in output_paths.items() if path == artifact.get('path')),
                        None,
                    )
            if report.get('status') != 'passed':
                trace.status = 'failed'
                result = result.model_copy(update={
                    'status': 'failed',
                    'decision': EvaluationDecision(
                        outcome='abort', reason='spatial_validation_failed',
                        message='; '.join(report.get('errors') or ['Spatial validation failed.']),
                    ),
                })
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
        if record_status == 'failed':
            _failed_trace(trace, result.decision.message or result.decision.reason)
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


def _verify_recorded_file(artifact: dict) -> None:
    """Require each consumed byte sequence to match the completed producer's evidence."""
    try:
        for item in [artifact, *artifact.get('components', [])]:
            path = Path(item['path'])
            require_standalone_sqlite(path)
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if path.stat().st_size != item.get('size_bytes') or digest != item.get('sha256'):
                raise ValueError(f'Produced artifact changed after validation: {path}')
            require_standalone_sqlite(path)
    except (KeyError, OSError, ValueError) as exc:
        artifact['deliverable'] = False
        artifact['integrity_validation'] = {'status': 'failed', 'errors': [str(exc)]}
        raise ValueError(f'Produced artifact changed or is unreadable: {exc}') from exc


def _verified_inputs(plan: TaskPlan, trace: ExecutionTrace, call) -> dict[str, str]:
    """Resolve each reference only from its original or a verified actual producer."""
    validate_original_inputs(plan)
    step = next(item for item in plan.steps if item.step_id == call.step_id)
    originals = {item.input_id: item.path for item in plan.required_inputs}
    outputs = {item.output_id: item.path for item in plan.expected_outputs}
    result = {}
    for ref in step.input_refs:
        if ref in originals:
            result[ref] = originals[ref]
            continue
        producer = next(item for item in plan.steps if ref in item.output_refs)
        attempts = [attempt for attempt in trace.attempts
                    if attempt.step_id == producer.step_id and attempt.status == 'success'
                    and attempt.observation.status == 'success'
                    and attempt.observation.outcome == 'completed']
        if len(attempts) != 1:
            raise ValueError(f'No completed producer for output reference {ref}.')
        artifact = next((item for item in attempts[0].artifacts
                         if item.get('kind') == 'path' and item.get('path') == outputs[ref]), None)
        if artifact is None:
            raise ValueError(f'Producer did not return the approved output for {ref}.')
        if attempts[0].tool_call.tool_name == 'execute_processing' and (
            artifact.get('output_ref') != ref
            or artifact.get('producer_step_id') != producer.step_id
            or artifact.get('deliverable') is not True
            or (artifact.get('spatial_validation') or {}).get('status') != 'passed'
        ):
            raise ValueError(f'Producer output {ref} has not passed spatial validation.')
        _verify_recorded_file(artifact)
        result[ref] = artifact['path']
    return result


def _actual_outputs(plan: TaskPlan, call, attempt) -> dict[str, str]:
    step = next(item for item in plan.steps if item.step_id == call.step_id)
    outputs = {item.output_id: item.path for item in plan.expected_outputs}
    result = {}
    for ref in step.output_refs:
        expected = outputs[ref]
        matches = [item for item in attempt.artifacts
                   if item.get('kind') == 'path' and item.get('path') == expected]
        if len(matches) != 1:
            raise ValueError(f'Producer did not return the approved output for {ref}.')
        _verify_recorded_file(matches[0])
        result[ref] = matches[0]['path']
    return result


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
                    try:
                        _verify_recorded_file(artifact)
                    except ValueError as exc:
                        raise RuntimeError(f'Completed checkpoint artifact changed; needs attention. {exc}') from exc
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
        summary=trace.metadata.get('failure_reason'),
    )


def _failed_trace(trace: ExecutionTrace, reason: str) -> dict[str, Any]:
    """Record a known failure without fabricating a tool attempt or unknown write."""
    trace.status = 'failed'
    trace.metadata['failure_reason'] = reason
    return {
        'execution_trace': trace, 'messages': [AIMessage(content=reason)],
        'pending_risk_review': None, 'risk_review_feedback': None,
        'execution_cursor': None, 'is_completed': None,
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
