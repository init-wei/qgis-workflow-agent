import asyncio

from agent.nodes.tool_call_execution_node import execute_tool_call_plan_sequence
from agent.tools.tool_contracts import ToolCallPlan, ToolSpec
from agent.tools.tool_registry import ToolRegistry


class FakeMCPClient:
    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return {"status": "success", "result": {"path": arguments.get("path")}}


def test_execute_tool_call_plan_sequence_records_trace(tmp_path):
    client = FakeMCPClient()
    registry = ToolRegistry.from_tool_specs(
        [ToolSpec(name="add_layer"), ToolSpec(name="capture_map_canvas")]
    )
    calls = [
        ToolCallPlan(
            step_id=1,
            tool_name="add_layer",
            arguments={"path": "shared/uploads/points.geojson"},
        ),
        ToolCallPlan(
            step_id=2,
            tool_name="capture_map_canvas",
            arguments={"path": "shared/outputs/points.png"},
        ),
    ]

    result = asyncio.run(
        execute_tool_call_plan_sequence(
            calls,
            mcp_client=client,
            registry=registry,
            workspace_root=tmp_path,
            allowed_tools=["add_layer", "capture_map_canvas"],
            task_id="task-1",
        )
    )

    assert result.status == "success"
    assert result.trace.status == "success"
    assert len(result.trace.attempts) == 2
    assert client.calls == [
        ("add_layer", {"path": "shared/uploads/points.geojson"}),
        ("capture_map_canvas", {"path": "shared/outputs/points.png"}),
    ]


def test_execute_tool_call_plan_sequence_stops_on_failure(tmp_path):
    class FailingClient(FakeMCPClient):
        async def call_tool(self, tool_name, arguments):
            self.calls.append((tool_name, arguments))
            return {"status": "error", "message": "File not found: missing.gpkg"}

    client = FailingClient()
    registry = ToolRegistry.from_tool_specs([ToolSpec(name="add_layer")])
    calls = [
        ToolCallPlan(
            step_id=1,
            tool_name="add_layer",
            arguments={"path": "shared/uploads/missing.gpkg"},
        )
    ]

    result = asyncio.run(
        execute_tool_call_plan_sequence(
            calls,
            mcp_client=client,
            registry=registry,
            workspace_root=tmp_path,
            allowed_tools=["add_layer"],
            task_id="task-1",
        )
    )

    assert result.status == "needs_review"
    assert len(result.trace.attempts) == 1
    assert result.decision.reason == "missing_file_requires_human"
