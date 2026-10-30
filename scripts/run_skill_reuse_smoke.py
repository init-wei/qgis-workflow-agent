#!/usr/bin/env python3
"""Run a registered Skill reuse smoke case against a QGIS MCP server."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.adapters.geotool_schema_adapter import qgis_task_plan_to_geotool_dict
from agent.nodes.executor_node import controlled_executor_node
from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan
from agent.state import create_initial_state
from agent.nodes.planner_node import bind_task_outputs
from agent.planning.plan_validator import PlanValidator
from agent.tools.database import save_task_execution_record
from agent.tools.demo_cases import DemoReplayResult, load_demo_case, replay_demo_case
from agent.tools.export_geotool_trace import (
    build_geotool_trace_seed,
    export_geotool_trace_jsonl,
)

DEFAULT_SKILL_REUSE_TRACE_PATH = Path(".target/demo/skill_reuse_smoke_v0_1.jsonl")
DEFAULT_SKILL_REUSE_SCREENSHOT_PATH = Path("skill_reuse_smoke.png")
SKILL_REUSE_CASE_ID = "skill_reuse_live_smoke"


def resolve_skill_reuse_outputs(
    output_path: str | Path | None,
    screenshot_path: str | Path | None,
) -> tuple[Path, Path]:
    """Resolve ignored daily-run output paths for the Skill reuse smoke."""

    resolved_trace_path = (
        Path(output_path) if output_path is not None else DEFAULT_SKILL_REUSE_TRACE_PATH
    )
    resolved_screenshot_path = (
        Path(screenshot_path)
        if screenshot_path is not None
        else DEFAULT_SKILL_REUSE_SCREENSHOT_PATH
    )
    return resolved_trace_path, resolved_screenshot_path


def build_skill_reuse_task_plan(
    screenshot_path: str | Path = DEFAULT_SKILL_REUSE_SCREENSHOT_PATH,
    task_id: str = "skill-reuse-live-smoke",
) -> tuple[TaskPlan, DemoReplayResult]:
    """Build a reviewed TaskPlan from the registered Skill reuse fixture."""

    case = load_demo_case(SKILL_REUSE_CASE_ID)
    replacements = dict(case.reuse_variables)
    screenshot_path_text = str(screenshot_path)
    replacements["parameter_2"] = screenshot_path_text
    replacements["artifact_path_1"] = screenshot_path_text
    case = case.model_copy(update={"reuse_variables": replacements})
    replay = replay_demo_case(case)
    if replay.registered_skill is None or replay.skill_execution_plan is None:
        raise RuntimeError("Skill reuse smoke fixture did not register a skill")

    calls = replay.skill_execution_plan["calls"]
    return (
        bind_task_outputs(TaskPlan(
            task_id=task_id,
            task_summary="Run a registered Skill reuse smoke in QGIS",
            risk_level="low",
            review_status="approved",
            required_inputs=[
                InputSpec(
                    input_id="input_points",
                    description="Reusable smoke vector layer",
                    file_id="skill-reuse-smoke-input",
                    path=_first_call_argument(calls, "add_layer", "path"),
                )
            ],
            expected_outputs=[
                OutputSpec(
                    output_id="map_screenshot",
                    description="Skill reuse smoke screenshot",
                    path=screenshot_path_text,
                ),
            ],
            steps=[
                PlanStep(
                    step_id=1,
                    description="Load the reusable smoke vector layer",
                    candidate_gdal_apis=[],
                    candidate_pyqgis_apis=["QgsVectorLayer"],
                    allowed_tools=["add_layer"],
                    input_refs=["input_points"],
                    output_refs=[],
                    risk_level="low",
                    requires_review=False,
                ),
                PlanStep(
                    step_id=2,
                    description="Capture the map canvas for the reused skill",
                    candidate_gdal_apis=[],
                    candidate_pyqgis_apis=["QgsMapCanvas"],
                    allowed_tools=["capture_map_canvas"],
                    input_refs=[],
                    output_refs=["map_screenshot"],
                    risk_level="low",
                    requires_review=False,
                ),
            ],
            tool_calls=[_task_plan_tool_call(call) for call in calls],
            metadata={
                "registered_skill_id": replay.registered_skill["skill_id"],
                "source_draft_id": replay.registered_skill["source_draft_id"],
                "skill_execution_status": replay.skill_execution_plan["status"],
            },
        )),
        replay,
    )


async def run_skill_reuse_smoke(
    output_path: str | Path = DEFAULT_SKILL_REUSE_TRACE_PATH,
    screenshot_path: str | Path = DEFAULT_SKILL_REUSE_SCREENSHOT_PATH,
) -> Path:
    """Run the registered Skill reuse smoke and export a GeoTool trace JSONL."""

    os.environ["QGIS_AGENT_EXECUTION_MODE"] = "tool_call_plan"
    user_query = "Run the registered add-layer screenshot skill reuse smoke."
    task_plan, _replay = build_skill_reuse_task_plan(screenshot_path, task_id=f"skill-smoke-{uuid4().hex}")
    state = create_initial_state("skill-reuse-smoke", user_query)
    state["task_plan"] = task_plan
    report = PlanValidator().validate(task_plan)
    if not report.is_valid:
        raise ValueError(report.summary)
    await asyncio.to_thread(save_task_execution_record, task_id=task_plan.task_id,
                            session_id=state["session_id"], plan_revision=task_plan.revision,
                            status="approved", plan=task_plan.model_dump(mode="json"), attempts=[])
    result = await controlled_executor_node(state)
    trace = result.get("execution_trace")
    if trace is None:
        raise RuntimeError("Skill reuse smoke did not produce an execution_trace")

    seed = build_geotool_trace_seed(
        trace,
        user_query=user_query,
        task_plan=qgis_task_plan_to_geotool_dict(task_plan, user_query=user_query),
    )
    return export_geotool_trace_jsonl([seed], output_path)


def _first_call_argument(calls: list[dict[str, Any]], tool_name: str, key: str) -> str:
    for call in calls:
        if call.get("tool_name") == tool_name:
            value = call.get("arguments", {}).get(key)
            if isinstance(value, str):
                return value
    raise RuntimeError(f"Skill reuse smoke missing {tool_name}.{key}")


def _task_plan_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    metadata = call.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "step_id": call["step_id"],
        "tool_name": call["tool_name"],
        "arguments": call["arguments"],
        "metadata": {
            **metadata,
            "call_id": f"skill-reuse-call-{call['step_id']:03d}",
            "output_ref": "loaded_points"
            if call["tool_name"] == "add_layer"
            else "map_screenshot",
            "depends_on": []
            if call["step_id"] == 1
            else [f"skill-reuse-call-{call['step_id'] - 1:03d}"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the GeoTool interop v0.1 JSONL trace.",
    )
    parser.add_argument(
        "--screenshot-output",
        default=None,
        help="New screenshot filename, bound to this smoke task controlled output directory.",
    )
    args = parser.parse_args()
    trace_path, screenshot_path = resolve_skill_reuse_outputs(
        args.output,
        args.screenshot_output,
    )
    output_path = asyncio.run(run_skill_reuse_smoke(trace_path, screenshot_path))
    print(f"Skill reuse GeoTool trace JSONL: {output_path}")


if __name__ == "__main__":
    main()
