#!/usr/bin/env python3
"""Compile reviewed TaskPlan objects into deterministic ToolCallPlan sequences."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan
from agent.tools.tool_contracts import ToolCallPlan


@dataclass(frozen=True)
class ToolCallPlannerInput:
    """Input for the rule-based ToolCallPlanner MVP."""

    task_plan: TaskPlan
    step_id: int | str
    available_tools: list[str] = field(default_factory=list)
    api_context: list[dict[str, Any]] = field(default_factory=list)
    workspace_state: dict[str, Any] = field(default_factory=dict)
    previous_observation: dict[str, Any] | None = None


def plan_tool_calls(planner_input: ToolCallPlannerInput) -> list[ToolCallPlan]:
    """Generate deterministic ToolCallPlan objects for one reviewed step."""

    try:
        step_id = int(planner_input.step_id)
    except (TypeError, ValueError):
        return []

    step = next(
        (
            candidate
            for candidate in planner_input.task_plan.steps
            if candidate.step_id == step_id
        ),
        None,
    )
    if step is None:
        return []

    calls = _compile_step_fallback_call(
        task_plan=planner_input.task_plan,
        step=step,
        available_tools=planner_input.available_tools,
    )
    return [calls] if calls is not None else []


def compile_task_plan_to_tool_calls(task_plan: TaskPlan) -> list[ToolCallPlan]:
    """Return executable tool calls for a reviewed TaskPlan.

    The preferred contract is `task_plan.metadata["tool_calls"]`, because it
    keeps execution deterministic after plan review. A small fallback supports
    safe output loading and screenshot steps so older plans can still run.
    """

    explicit_calls = _compile_explicit_calls(task_plan)
    fallback_calls = _compile_fallback_calls(task_plan)
    return explicit_calls + [
        call
        for call in fallback_calls
        if not _has_call_for_step(explicit_calls, call.step_id)
    ]


def _compile_explicit_calls(task_plan: TaskPlan) -> list[ToolCallPlan]:
    raw_calls = task_plan.metadata.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        return []

    steps_by_id = {step.step_id: step for step in task_plan.steps}
    calls: list[ToolCallPlan] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        step_id = raw_call.get("step_id")
        tool_name = raw_call.get("tool_name")
        arguments = raw_call.get("arguments", {})
        if not isinstance(step_id, int) or not tool_name or not isinstance(arguments, dict):
            continue

        step = steps_by_id.get(step_id)
        if step is None:
            continue
        calls.append(
            _build_call(
                task_plan=task_plan,
                step=step,
                step_id=step_id,
                tool_name=str(tool_name),
                arguments=_normalize_tool_arguments(str(tool_name), arguments),
                metadata=raw_call.get("metadata") if isinstance(raw_call.get("metadata"), dict) else {},
            )
        )
    return calls


def _compile_fallback_calls(task_plan: TaskPlan) -> list[ToolCallPlan]:
    outputs_by_id = {output.output_id: output for output in task_plan.expected_outputs}
    calls: list[ToolCallPlan] = []
    for step in task_plan.steps:
        allowed_tools = set(step.allowed_tools)
        description = step.description.lower()

        if "add_layer" in allowed_tools and _looks_like_load_step(description):
            output = _first_output_for_step(step, outputs_by_id)
            if output and output.path:
                calls.append(
                    _build_call(
                        task_plan=task_plan,
                        step=step,
                        step_id=step.step_id,
                        tool_name="add_layer",
                        arguments={
                            "path": output.path,
                            "layer_type": _layer_type_from_path(output.path),
                        },
                    )
                )

        if "capture_map_canvas" in allowed_tools and _looks_like_screenshot_step(description):
            output = _first_output_for_step(step, outputs_by_id)
            if output and output.path:
                calls.append(
                    _build_call(
                        task_plan=task_plan,
                        step=step,
                        step_id=step.step_id,
                        tool_name="capture_map_canvas",
                        arguments={"path": output.path, "width": 1200, "height": 800},
                    )
                )

    return calls


def _compile_step_fallback_call(
    task_plan: TaskPlan,
    step: PlanStep,
    available_tools: list[str] | None = None,
) -> ToolCallPlan | None:
    allowed_tools = set(step.allowed_tools)
    if available_tools:
        allowed_tools &= set(available_tools)
    description = step.description.lower()
    inputs_by_id = {input_spec.input_id: input_spec for input_spec in task_plan.required_inputs}
    outputs_by_id = {output.output_id: output for output in task_plan.expected_outputs}
    input_spec = _first_input_for_step(step, inputs_by_id)
    output = _first_output_for_step(step, outputs_by_id)

    if {"add_layer", "load_vector_layer"} & allowed_tools and _looks_like_load_step(description):
        tool_name = "add_layer" if "add_layer" in allowed_tools else "load_vector_layer"
        path = _first_known_path(input_spec, output)
        if path:
            return _build_call(
                task_plan=task_plan,
                step=step,
                step_id=step.step_id,
                tool_name=tool_name,
                arguments={"path": path, "layer_type": _layer_type_from_path(path)},
            )
        return _review_needed_call(task_plan, step, tool_name, "missing_layer_path")

    if "execute_processing" in allowed_tools and _looks_like_processing_step(description):
        if output and output.path:
            return _build_call(
                task_plan=task_plan,
                step=step,
                step_id=step.step_id,
                tool_name="execute_processing",
                arguments={
                    "algorithm": _processing_algorithm_for_step(step),
                    "parameters": {"OUTPUT": output.path},
                },
            )
        return _review_needed_call(
            task_plan, step, "execute_processing", "missing_processing_output"
        )

    if "capture_map_canvas" in allowed_tools and _looks_like_screenshot_step(description):
        if output and output.path:
            return _build_call(
                task_plan=task_plan,
                step=step,
                step_id=step.step_id,
                tool_name="capture_map_canvas",
                arguments={"path": output.path, "width": 1200, "height": 800},
            )
        return _review_needed_call(
            task_plan, step, "capture_map_canvas", "missing_screenshot_path"
        )

    if len(allowed_tools) == 1:
        tool_name = next(iter(allowed_tools))
        return _review_needed_call(task_plan, step, tool_name, "insufficient_arguments")

    return None


def _build_call(
    task_plan: TaskPlan,
    step: PlanStep | None,
    step_id: int,
    tool_name: str,
    arguments: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> ToolCallPlan:
    step_metadata = _step_metadata(task_plan, step, metadata or {})
    return ToolCallPlan(
        step_id=step_id,
        tool_name=tool_name,
        arguments=arguments,
        risk_level=step.risk_level if step else task_plan.risk_level,
        requires_review=step.requires_review if step else task_plan.risk_level == "high",
        metadata=step_metadata,
    )


def _review_needed_call(
    task_plan: TaskPlan,
    step: PlanStep,
    tool_name: str,
    reason: str,
) -> ToolCallPlan:
    return _build_call(
        task_plan=task_plan,
        step=step,
        step_id=step.step_id,
        tool_name=tool_name,
        arguments={},
        metadata={"planner_error": reason},
    ).model_copy(update={"requires_review": True})


def _step_metadata(
    task_plan: TaskPlan, step: PlanStep | None, extra: dict[str, Any]
) -> dict[str, Any]:
    metadata = {
        "task_plan_id": task_plan.task_id,
        "task_summary": task_plan.task_summary,
    }
    if step:
        metadata.update(
            {
                "description": step.description,
                "candidate_gdal_apis": list(step.candidate_gdal_apis),
                "candidate_pyqgis_apis": list(step.candidate_pyqgis_apis),
                "input_refs": list(step.input_refs),
                "output_refs": list(step.output_refs),
            }
        )
    metadata.update(extra)
    return metadata


def _first_output_for_step(
    step: PlanStep, outputs_by_id: dict[str, OutputSpec]
) -> OutputSpec | None:
    for output_ref in step.output_refs:
        output = outputs_by_id.get(output_ref)
        if output is not None:
            return output
    return None


def _first_input_for_step(
    step: PlanStep, inputs_by_id: dict[str, InputSpec]
) -> InputSpec | None:
    for input_ref in step.input_refs:
        input_spec = inputs_by_id.get(input_ref)
        if input_spec is not None:
            return input_spec
    return None


def _first_known_path(
    input_spec: InputSpec | None, output_spec: OutputSpec | None
) -> str | None:
    if input_spec and input_spec.path:
        return input_spec.path
    if output_spec and output_spec.path:
        return output_spec.path
    return None


def _has_call_for_step(calls: list[ToolCallPlan], step_id: int) -> bool:
    return any(call.step_id == step_id for call in calls)


def _looks_like_load_step(description: str) -> bool:
    return any(token in description for token in ("load", "加载", "add layer", "加入"))


def _looks_like_screenshot_step(description: str) -> bool:
    return any(token in description for token in ("screenshot", "截图", "capture"))


def _looks_like_processing_step(description: str) -> bool:
    return any(
        token in description
        for token in ("processing", "buffer", "clip", "intersection", "缓冲", "裁剪", "相交")
    )


def _processing_algorithm_for_step(step: PlanStep) -> str:
    algorithm = step.metadata.get("algorithm") if hasattr(step, "metadata") else None
    if isinstance(algorithm, str) and algorithm:
        return algorithm
    description = step.description.lower()
    if "clip" in description or "裁剪" in description:
        return "native:clip"
    if "intersection" in description or "相交" in description:
        return "native:intersection"
    return "native:buffer"


def _layer_type_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".tif", ".tiff", ".vrt", ".img", ".asc"}:
        return "raster"
    return "vector"


def _normalize_tool_arguments(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if tool_name != "add_layer":
        return arguments

    normalized = dict(arguments)
    if "path" not in normalized and isinstance(normalized.get("uri"), str):
        normalized["path"] = normalized.pop("uri")
    if "name" not in normalized and isinstance(normalized.get("layer_name"), str):
        normalized["name"] = normalized.pop("layer_name")
    if "layer_type" not in normalized and isinstance(normalized.get("path"), str):
        normalized["layer_type"] = _layer_type_from_path(normalized["path"])
    return normalized
