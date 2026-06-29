#!/usr/bin/env python3
"""GeoTool-compatible schema adapter.

The adapter intentionally exchanges plain dictionaries instead of importing
GeoTool-LLM code. That keeps qgis-workflow-agent as the runtime owner while
still producing stable seed data for the training project.
"""

from __future__ import annotations

from typing import Any

from agent.planning.plan_schema import TaskPlan
from agent.state import Plan, Step
from agent.tools.tool_contracts import ExecutionTrace, ToolCallPlan


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
        "review_status": task_plan.review_status,
        "metadata": dict(task_plan.metadata),
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
    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise ValueError("GeoTool tool call payload must include non-empty tool_calls.")

    calls: list[ToolCallPlan] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ValueError("Each GeoTool tool call must be a dict.")
        step_id = raw_call.get("step_id")
        tool_name = raw_call.get("tool")
        arguments = raw_call.get("args")
        if not isinstance(step_id, int):
            raise ValueError("GeoTool tool call step_id must be an int.")
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
                requires_review=bool(raw_call.get("requires_review", False)),
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


def qgis_execution_trace_to_geotool_dict(
    trace: ExecutionTrace, user_query: str
) -> dict:
    """Convert an ExecutionTrace into a GeoTool-compatible trace payload."""

    observations = [
        {
            "attempt_id": attempt.attempt_id,
            "step_id": attempt.step_id,
            "tool_name": attempt.tool_call.tool_name,
            "status": attempt.observation.status,
            "error_type": attempt.observation.error_type,
            "message": attempt.observation.message,
            "data": attempt.observation.data,
            "artifacts": attempt.artifacts,
            "metadata": attempt.metadata,
        }
        for attempt in trace.attempts
    ]
    repair_actions = [
        attempt.metadata["repair_action"]
        for attempt in trace.attempts
        if isinstance(attempt.metadata.get("repair_action"), dict)
    ]

    return {
        "trace_id": trace.trace_id,
        "task_id": trace.task_id,
        "user_query": user_query,
        "task_plan": trace.metadata.get("task_plan", {}),
        "tool_call_plan": trace.metadata.get("tool_call_plan", []),
        "observations": observations,
        "repair_actions": repair_actions,
        "final_status": trace.status,
        "metadata": {
            "source_project": "qgis-workflow-agent",
            "export_version": "v0",
            **(
                trace.metadata.get("geotool_metadata")
                if isinstance(trace.metadata.get("geotool_metadata"), dict)
                else {}
            ),
        },
    }


def _step_to_geotool_dict(step) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "description": step.description,
        "step_type": step.step_type,
        "candidate_tools": list(step.allowed_tools),
        "input_refs": list(step.input_refs),
        "output_refs": list(step.output_refs),
        "risk_level": step.risk_level,
        "requires_review": step.requires_review,
        "metadata": {
            "candidate_gdal_apis": list(step.candidate_gdal_apis),
            "candidate_pyqgis_apis": list(step.candidate_pyqgis_apis),
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
