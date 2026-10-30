import asyncio

from agent.nodes.tool_call_execution_node import execute_tool_call_plan_sequence
from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan, ToolCallPlan
from agent.tools.tool_call_planner import compile_task_plan_to_tool_calls
from agent.tools.tool_contracts import ToolSpec
from agent.tools.tool_registry import ToolRegistry


class MockMCPClient:
    def __init__(self, workspace_root):
        self.calls = []
        self.workspace_root = workspace_root

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if tool_name == "capture_map_canvas":
            target = self.workspace_root / arguments["path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"GIS boundary fake image")
        return {
            "status": "success",
            "result": {
                "path": arguments.get("path") if tool_name != "add_layer" else None,
                "id": "points" if tool_name == "add_layer" else None,
                "loaded": tool_name == "add_layer",
                "rendered": tool_name == "capture_map_canvas",
                "success": True,
            },
        }


def _task_plan() -> TaskPlan:
    return TaskPlan(
        task_id="task-load-and-screenshot",
        task_summary="加载图层并截图",
        risk_level="low",
        review_status="approved",
        required_inputs=[
            InputSpec(
                input_id="points",
                description="点图层",
                file_id="file-points",
                path="shared/uploads/points.geojson",
            )
        ],
        expected_outputs=[
            OutputSpec(
                output_id="screenshot",
                description="截图",
                path="shared/outputs/task-load-and-screenshot/r1/screenshot/points.png",
            )
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="加载图层",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=["QgsVectorLayer"],
                allowed_tools=["add_layer"],
                input_refs=["points"],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            ),
            PlanStep(
                step_id=2,
                description="截图当前地图",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["capture_map_canvas"],
                input_refs=[],
                output_refs=["screenshot"],
                risk_level="low",
                requires_review=False,
            ),
        ],
        tool_calls=[
            ToolCallPlan(
                step_id=1,
                tool_name="add_layer",
                arguments={
                    "path": "shared/uploads/points.geojson",
                    "layer_type": "vector",
                    "name": "points",
                },
            ),
            ToolCallPlan(
                step_id=2,
                tool_name="capture_map_canvas",
                arguments={
                    "path": "shared/outputs/task-load-and-screenshot/r1/screenshot/points.png",
                    "width": 1200,
                    "height": 800,
                },
            ),
        ],
        metadata={},
    )


def test_task_plan_to_tool_call_plan_to_execution_loop_minimal_closed_loop(tmp_path):
    task_plan = _task_plan()
    available_tools = ["add_layer", "capture_map_canvas"]
    tool_calls = compile_task_plan_to_tool_calls(task_plan)
    client = MockMCPClient(tmp_path)
    registry = ToolRegistry.from_tool_specs(
        [ToolSpec(name="add_layer"), ToolSpec(name="capture_map_canvas")]
    )

    result = asyncio.run(
        execute_tool_call_plan_sequence(
            tool_calls,
            mcp_client=client,
            registry=registry,
            workspace_root=tmp_path,
            allowed_tools=available_tools,
            task_id=task_plan.task_id,
        )
    )

    assert [call.tool_name for call in tool_calls] == [
        "add_layer",
        "capture_map_canvas",
    ]
    assert result.status == "success"
    assert len(result.trace.attempts) == 2
    assert client.calls == [
        (
            "add_layer",
            {
                "path": "shared/uploads/points.geojson",
                "layer_type": "vector",
                "name": "points",
            },
        ),
        (
            "capture_map_canvas",
            {"path": "shared/outputs/task-load-and-screenshot/r1/screenshot/points.png", "width": 1200, "height": 800},
        ),
    ]
