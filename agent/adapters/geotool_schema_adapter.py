#!/usr/bin/env python3
"""GeoTool-compatible schema adapter.

The adapter intentionally exchanges plain dictionaries instead of importing
GeoTool-LLM code. That keeps qgis-workflow-agent as the runtime owner while
still producing stable seed data for the training project.
"""

from __future__ import annotations

import json
from typing import Any

from agent.planning.plan_schema import TaskPlan
from agent.state import Plan, Step
from agent.tools.tool_contracts import ExecutionTrace, StepAttempt, ToolCallPlan


SCHEMA_VERSION = "geotool-interop-v0.1"


def qgis_task_plan_to_geotool_dict(task_plan: TaskPlan, user_query: str) -> dict:
    """Convert the internal TaskPlan into a GeoTool-compatible task payload."""

    if not task_plan.steps:
        raise ValueError("TaskPlan must include non-empty steps.")

    return {
        "task_id": task_plan.task_id,
        "user_query": user_query,
        "goal": task_plan.task_summary,
        "required_inputs": [
            input_spec.model_dump(mode="json")
            for input_spec in task_plan.required_inputs
        ],
        "expected_outputs": [
            output_spec.model_dump(mode="json")
            for output_spec in task_plan.expected_outputs
        ],
        "steps": [_step_to_geotool_dict(step) for step in task_plan.steps],
        "risk_level": task_plan.risk_level,
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "qgis_review_status": task_plan.review_status,
            **dict(task_plan.metadata),
        },
    }


def geotool_task_plan_to_qgis_legacy_plan(payload: dict) -> Plan:
    """Convert a GeoTool task payload into the legacy Plan used by the UI."""

    if not isinstance(payload, dict):
        raise ValueError("GeoTool task plan payload must be a dict.")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("GeoTool task plan payload must include non-empty steps.")

    return Plan(
        task=str(payload.get("goal") or payload.get("task_summary") or ""),
        steps=[_legacy_step_from_geotool(step) for step in steps],
        metadata={
            "source_schema": "geotool",
            "task_id": payload.get("task_id"),
            "risk_level": payload.get("risk_level"),
        },
    )


def geotool_tool_call_plan_to_qgis_calls(payload: dict) -> list[ToolCallPlan]:
    """Convert GeoTool ToolCallPlan payloads into internal ToolCallPlan objects."""

    if not isinstance(payload, dict):
        raise ValueError("GeoTool tool call payload must be a dict.")
    _validate_schema_version(payload.get("metadata"))
    raw_calls = payload.get("calls")
    if raw_calls is None:
        raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise ValueError("GeoTool tool call payload must include non-empty calls.")

    calls: list[ToolCallPlan] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ValueError("Each GeoTool tool call must be a dict.")
        step_id = _coerce_step_id(raw_call.get("step_id"))
        tool_name = raw_call.get("tool") or raw_call.get("tool_name")
        arguments = raw_call.get("args")
        if arguments is None:
            arguments = raw_call.get("arguments")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("GeoTool tool call must include tool.")
        if not isinstance(arguments, dict):
            raise ValueError("GeoTool tool call args must be a dict.")

        calls.append(
            ToolCallPlan(
                step_id=step_id,
                tool_name=tool_name,
                arguments=arguments,
                risk_level=raw_call.get("risk_level") or "low",
                requires_review=_strict_requires_review(raw_call),
                metadata={
                    "call_id": raw_call.get("call_id"),
                    "depends_on": raw_call.get("depends_on", []),
                    "output_ref": raw_call.get("output_ref"),
                    **(
                        raw_call.get("metadata")
                        if isinstance(raw_call.get("metadata"), dict)
                        else {}
                    ),
                },
            )
        )
    return calls


def _validate_schema_version(metadata: Any) -> None:
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise ValueError("GeoTool tool call metadata must be a dict.")
    schema_version = metadata.get("schema_version")
    if schema_version is not None and schema_version != SCHEMA_VERSION:
        raise ValueError(f"metadata.schema_version must be {SCHEMA_VERSION}")


def _strict_requires_review(raw_call: dict[str, Any]) -> bool:
    value = raw_call.get("requires_review", False)
    if not isinstance(value, bool):
        raise ValueError("GeoTool tool call requires_review must be a boolean.")
    return value


def qgis_tool_calls_to_geotool_plan(
    task_id: str | None, calls: list[ToolCallPlan]
) -> dict[str, Any]:
    """Convert internal qgis calls into a canonical GeoTool ToolCallPlan."""

    return {
        "task_id": str(task_id or ""),
        "calls": [
            _qgis_tool_call_to_geotool_call(call, index)
            for index, call in enumerate(calls, start=1)
        ],
        "metadata": {"schema_version": SCHEMA_VERSION},
    }


def qgis_execution_trace_to_geotool_dict(
    trace: ExecutionTrace, user_query: str
) -> dict:
    """Convert an ExecutionTrace into a GeoTool-compatible trace payload."""

    call_ids = _call_ids_by_key(trace.attempts)
    tool_calls = _unique_tool_calls(trace.attempts)
    observations = [
        _attempt_to_geotool_observation(attempt, call_ids[_tool_call_key(attempt.tool_call)])
        for attempt in trace.attempts
    ]
    repair_actions = [
        _repair_action_to_geotool_dict(
            attempt.metadata["repair_action"],
            attempt,
            call_ids[_tool_call_key(attempt.tool_call)],
            trace.task_id,
        )
        for attempt in trace.attempts
        if isinstance(attempt.metadata.get("repair_action"), dict)
    ]

    return {
        "trace_id": trace.trace_id,
        "task_id": trace.task_id,
        "user_query": user_query,
        "task_plan": trace.metadata.get("task_plan", {}),
        "tool_call_plan": _trace_tool_call_plan_to_geotool(trace, tool_calls),
        "observations": observations,
        "repair_actions": repair_actions,
        "final_status": _trace_status_to_geotool(trace.status),
        "metadata": {
            "source_project": "qgis-workflow-agent",
            "export_version": SCHEMA_VERSION,
            "schema_version": SCHEMA_VERSION,
            **(
                trace.metadata.get("geotool_metadata")
                if isinstance(trace.metadata.get("geotool_metadata"), dict)
                else {}
            ),
        },
    }


def _step_to_geotool_dict(step) -> dict[str, Any]:
    return {
        "step_id": str(step.step_id),
        "intent": step.step_type,
        "description": step.description,
        "candidate_tools": list(step.allowed_tools),
        "input_refs": list(step.input_refs),
        "output_refs": list(step.output_refs),
        "risk_level": step.risk_level,
        "requires_review": step.requires_review,
        "metadata": {
            "candidate_gdal_apis": list(step.candidate_gdal_apis),
            "candidate_pyqgis_apis": list(step.candidate_pyqgis_apis),
            "qgis_step_type": step.step_type,
        },
    }


def _legacy_step_from_geotool(raw_step: dict) -> Step:
    if not isinstance(raw_step, dict):
        raise ValueError("GeoTool step must be a dict.")
    metadata = raw_step.get("metadata") if isinstance(raw_step.get("metadata"), dict) else {}
    return Step(
        step_id=int(raw_step.get("step_id")),
        description=str(raw_step.get("description") or ""),
        gdal_api=list(metadata.get("candidate_gdal_apis") or []),
        pyqgis_api=list(metadata.get("candidate_pyqgis_apis") or []),
    )


def _coerce_step_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("GeoTool tool call step_id must be an int-like value.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("GeoTool tool call step_id must be an int-like value.") from exc


def _qgis_tool_call_to_geotool_call(
    call: ToolCallPlan, index: int
) -> dict[str, Any]:
    metadata = dict(call.metadata)
    call_id = metadata.pop("call_id", None) or f"call-{index:03d}"
    output_ref = metadata.pop("output_ref", None)
    depends_on = metadata.pop("depends_on", [])
    return {
        "call_id": str(call_id),
        "step_id": str(call.step_id),
        "tool": call.tool_name,
        "args": dict(call.arguments),
        "output_ref": output_ref,
        "depends_on": list(depends_on) if isinstance(depends_on, list) else [],
        "risk_level": call.risk_level,
        "requires_review": call.requires_review,
        "metadata": metadata,
    }


def _tool_call_key(call: ToolCallPlan) -> tuple[int, str, str]:
    return (
        call.step_id,
        call.tool_name,
        json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
    )


def _unique_tool_calls(attempts: list[StepAttempt]) -> list[ToolCallPlan]:
    seen: set[tuple[int, str, str]] = set()
    calls: list[ToolCallPlan] = []
    for attempt in attempts:
        key = _tool_call_key(attempt.tool_call)
        if key in seen:
            continue
        seen.add(key)
        calls.append(attempt.tool_call)
    return calls


def _trace_tool_call_plan_to_geotool(
    trace: ExecutionTrace, fallback_calls: list[ToolCallPlan]
) -> dict[str, Any]:
    raw_plan = trace.metadata.get("tool_call_plan")
    if isinstance(raw_plan, dict):
        return raw_plan
    if isinstance(raw_plan, list):
        calls = [ToolCallPlan.model_validate(raw_call) for raw_call in raw_plan]
        return qgis_tool_calls_to_geotool_plan(trace.task_id, calls)
    return qgis_tool_calls_to_geotool_plan(trace.task_id, fallback_calls)


def _call_ids_by_key(attempts: list[StepAttempt]) -> dict[tuple[int, str, str], str]:
    ids: dict[tuple[int, str, str], str] = {}
    for attempt in attempts:
        key = _tool_call_key(attempt.tool_call)
        if key not in ids:
            raw_call_id = attempt.tool_call.metadata.get("call_id")
            ids[key] = str(raw_call_id or f"call-{len(ids) + 1:03d}")
    return ids


def _attempt_to_geotool_observation(
    attempt: StepAttempt, call_id: str
) -> dict[str, Any]:
    metadata = {
        key: value
        for key, value in attempt.metadata.items()
        if key != "repair_action"
    }
    metadata.update(
        {
            "attempt_id": attempt.attempt_id,
            "qgis_step_id": attempt.step_id,
            "qgis_status": attempt.status,
            "qgis_observation_status": attempt.observation.status,
        }
    )
    return {
        "call_id": call_id,
        "tool": attempt.tool_call.tool_name,
        "status": _attempt_status_to_geotool(attempt),
        "output_ref": attempt.tool_call.metadata.get("output_ref"),
        "artifacts": attempt.artifacts or attempt.observation.artifacts,
        "error_type": attempt.observation.error_type,
        "error_message": attempt.observation.message,
        "raw_response": attempt.observation.raw_response,
        "metadata": metadata,
    }


def _attempt_status_to_geotool(
    attempt: StepAttempt,
) -> str:
    if attempt.status == "success" or attempt.observation.status == "success":
        return "success"
    if attempt.status == "blocked":
        return "blocked"
    return "failed"


def _trace_status_to_geotool(status: str) -> str:
    if status == "success":
        return "success"
    if status == "failed":
        return "failed"
    return "needs_review"


def _repair_action_to_geotool_dict(
    raw_action: dict[str, Any],
    attempt: StepAttempt,
    call_id: str,
    task_id: str | None,
) -> dict[str, Any]:
    qgis_action_type = str(raw_action.get("action_type") or "")
    action_type = {
        "update_arguments": "update_args",
        "abort": "abort",
        "ask_human": "ask_human",
    }.get(qgis_action_type, "ask_human")
    reason = str(raw_action.get("reason") or "qgis_repair_action")
    metadata = raw_action.get("metadata") if isinstance(raw_action.get("metadata"), dict) else {}
    return {
        "task_id": str(task_id or ""),
        "failed_call_id": call_id,
        "error_type": attempt.observation.error_type or "unknown_error",
        "observed_error": attempt.observation.message or "",
        "diagnosis": reason,
        "action_type": action_type,
        "repair_calls": [],
        "updated_args": dict(raw_action.get("updated_arguments") or {}),
        "retry_from_call_id": call_id if action_type == "update_args" else None,
        "metadata": {
            "qgis_action_type": qgis_action_type,
            "qgis_reason": reason,
            **metadata,
        },
    }
