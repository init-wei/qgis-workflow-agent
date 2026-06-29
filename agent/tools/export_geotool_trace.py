#!/usr/bin/env python3
"""Export ExecutionTrace objects as GeoTool-LLM seed JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

from agent.adapters.geotool_schema_adapter import qgis_execution_trace_to_geotool_dict
from agent.tools.tool_contracts import ExecutionTrace


class GeoToolTraceSeed(BaseModel):
    """One GeoTool-compatible trace seed row."""

    trace_id: str
    task_id: str | None = None
    user_query: str
    task_plan: dict[str, Any] = Field(default_factory=dict)
    tool_call_plan: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    repair_actions: list[dict[str, Any]] = Field(default_factory=list)
    final_status: str
    metadata: dict[str, Any] = Field(
        default_factory=lambda: {
            "source_project": "qgis-workflow-agent",
            "export_version": "v0",
        }
    )


def build_geotool_trace_seed(
    trace: ExecutionTrace,
    *,
    user_query: str,
    task_plan: dict[str, Any] | None = None,
    tool_call_plan: list[dict[str, Any]] | None = None,
) -> GeoToolTraceSeed:
    """Build a serializable seed record from one execution trace."""

    trace_payload = qgis_execution_trace_to_geotool_dict(trace, user_query=user_query)
    return GeoToolTraceSeed(
        trace_id=trace_payload["trace_id"],
        task_id=trace_payload["task_id"],
        user_query=user_query,
        task_plan=task_plan or trace_payload.get("task_plan", {}),
        tool_call_plan=tool_call_plan or _tool_call_plan_from_trace(trace),
        observations=trace_payload["observations"],
        repair_actions=trace_payload["repair_actions"],
        final_status=trace_payload["final_status"],
        metadata=trace_payload["metadata"],
    )


def export_geotool_trace_jsonl(
    seeds: Iterable[GeoToolTraceSeed | dict[str, Any]],
    output_path: str | Path = "exports/geotool_traces/seed.jsonl",
) -> Path:
    """Write seed records to JSONL and return the output path."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        for seed in seeds:
            record = (
                seed
                if isinstance(seed, GeoToolTraceSeed)
                else GeoToolTraceSeed.model_validate(seed)
            )
            output_file.write(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
                + "\n"
            )
    return path


def _tool_call_plan_from_trace(trace: ExecutionTrace) -> list[dict[str, Any]]:
    seen: set[tuple[int, str, str]] = set()
    calls: list[dict[str, Any]] = []
    for attempt in trace.attempts:
        key = (
            attempt.step_id,
            attempt.tool_call.tool_name,
            json.dumps(attempt.tool_call.arguments, sort_keys=True, ensure_ascii=False),
        )
        if key in seen:
            continue
        seen.add(key)
        calls.append(attempt.tool_call.model_dump(mode="json"))
    return calls
