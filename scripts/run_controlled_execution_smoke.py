#!/usr/bin/env python3
"""Run one real controlled-execution smoke case against a QGIS MCP server."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.adapters.geotool_schema_adapter import qgis_task_plan_to_geotool_dict
from agent.nodes.executor_node import controlled_executor_node
from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan
from agent.state import create_initial_state
from agent.tools.export_geotool_trace import (
    build_geotool_trace_seed,
    export_geotool_trace_jsonl,
)
from agent.tools.mcp_client import QGIS_MCP_SERVER_URL, QGISMCPClient

DEFAULT_SMOKE_TRACE_PATH = Path(".target/demo/controlled_smoke_v0_1.jsonl")
DEFAULT_SMOKE_SCREENSHOT_PATH = Path(".target/demo/smoke_points.png")
DEMO_TRACE_PATH = Path("exports/geotool_traces/controlled_smoke_v0_1.jsonl")
DEMO_SCREENSHOT_PATH = Path("exports/geotool_traces/smoke_points.png")


@dataclass(frozen=True)
class MCPPreflightResult:
    ok: bool
    url: str
    message: str


async def check_mcp_preflight(
    url: str = QGIS_MCP_SERVER_URL,
    timeout_seconds: float = 5.0,
) -> MCPPreflightResult:
    client = QGISMCPClient(url=url)
    try:
        tools = await asyncio.wait_for(client.list_tools(), timeout=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - preserve connection context for operators.
        return MCPPreflightResult(ok=False, url=url, message=str(exc))
    finally:
        await client.close()
    tool_names = ", ".join(sorted(tool["name"] for tool in tools))
    return MCPPreflightResult(ok=True, url=url, message=f"tools: {tool_names}")


def skip_reason_for_preflight(result: MCPPreflightResult) -> str:
    return (
        "QGIS MCP endpoint is not reachable. "
        f"Set QGIS_MCP_SERVER_URL={result.url} after starting the QGIS MCP "
        f"HTTP wrapper. Details: {result.message}"
    )


def build_smoke_task_plan(screenshot_path: str | Path = DEFAULT_SMOKE_SCREENSHOT_PATH) -> TaskPlan:
    screenshot_path_text = str(screenshot_path)
    return TaskPlan(
        task_id="smoke-load-layer-capture",
        task_summary="Load one vector layer and capture a screenshot",
        risk_level="low",
        review_status="approved",
        required_inputs=[
            InputSpec(
                input_id="input_points",
                description="Smoke vector layer",
                path="shared/uploads/phase11_points.geojson",
            )
        ],
        expected_outputs=[
            OutputSpec(
                output_id="loaded_points",
                description="Loaded smoke layer",
            ),
            OutputSpec(
                output_id="map_screenshot",
                description="Smoke screenshot",
                path=screenshot_path_text,
            ),
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="Load the smoke vector layer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=["QgsVectorLayer"],
                allowed_tools=["add_layer"],
                input_refs=["input_points"],
                output_refs=["loaded_points"],
                risk_level="low",
                requires_review=False,
            ),
            PlanStep(
                step_id=2,
                description="Capture the current map canvas screenshot",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=["QgsMapCanvas"],
                allowed_tools=["capture_map_canvas"],
                input_refs=["loaded_points"],
                output_refs=["map_screenshot"],
                risk_level="low",
                requires_review=False,
            ),
        ],
        metadata={
            "tool_calls": [
                {
                    "step_id": 1,
                    "tool_name": "add_layer",
                    "arguments": {
                        "path": "shared/uploads/phase11_points.geojson",
                        "layer_type": "vector",
                    },
                    "metadata": {
                        "call_id": "smoke-call-001",
                        "output_ref": "loaded_points",
                    },
                },
                {
                    "step_id": 2,
                    "tool_name": "capture_map_canvas",
                    "arguments": {
                        "path": screenshot_path_text,
                    },
                    "metadata": {
                        "call_id": "smoke-call-002",
                        "depends_on": ["smoke-call-001"],
                        "output_ref": "map_screenshot",
                    },
                },
            ]
        },
    )


def resolve_smoke_outputs(
    output_path: str | Path | None,
    screenshot_path: str | Path | None,
    update_demo_artifacts: bool = False,
) -> tuple[Path, Path]:
    if update_demo_artifacts:
        default_trace_path = DEMO_TRACE_PATH
        default_screenshot_path = DEMO_SCREENSHOT_PATH
    else:
        default_trace_path = DEFAULT_SMOKE_TRACE_PATH
        default_screenshot_path = DEFAULT_SMOKE_SCREENSHOT_PATH

    resolved_trace_path = Path(output_path) if output_path is not None else default_trace_path
    resolved_screenshot_path = (
        Path(screenshot_path) if screenshot_path is not None else default_screenshot_path
    )
    return resolved_trace_path, resolved_screenshot_path


async def run_smoke(
    output_path: str | Path = DEFAULT_SMOKE_TRACE_PATH,
    screenshot_path: str | Path = DEFAULT_SMOKE_SCREENSHOT_PATH,
) -> Path:
    os.environ["QGIS_AGENT_EXECUTION_MODE"] = "tool_call_plan"
    user_query = "Load one vector layer and capture a screenshot."
    task_plan = build_smoke_task_plan(screenshot_path)
    state = create_initial_state("controlled-smoke", user_query)
    state["task_plan"] = task_plan
    state["status"] = True
    state["tool_selection"] = ["add_layer", "capture_map_canvas"]

    result = await controlled_executor_node(state)
    trace = result.get("execution_trace")
    if trace is None:
        raise RuntimeError("Controlled smoke did not produce an execution_trace")

    seed = build_geotool_trace_seed(
        trace,
        user_query=user_query,
        task_plan=qgis_task_plan_to_geotool_dict(task_plan, user_query=user_query),
    )
    return export_geotool_trace_jsonl([seed], output_path)


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
        help="Path to write the captured QGIS map canvas screenshot.",
    )
    parser.add_argument(
        "--update-demo-artifacts",
        action="store_true",
        help="Write tracked demo artifacts under exports/geotool_traces.",
    )
    args = parser.parse_args()
    trace_path, screenshot_path = resolve_smoke_outputs(
        args.output,
        args.screenshot_output,
        update_demo_artifacts=args.update_demo_artifacts,
    )
    output_path = asyncio.run(run_smoke(trace_path, screenshot_path))
    print(f"GeoTool trace JSONL: {output_path}")


if __name__ == "__main__":
    main()
