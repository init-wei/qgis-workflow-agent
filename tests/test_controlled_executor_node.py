import asyncio

from agent.nodes.executor_node import executor_node
from agent.planning.plan_schema import OutputSpec, PlanStep, TaskPlan
from agent.state import create_initial_state


class FakeMCPClient:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [
            {"name": "execute_processing", "description": "Run processing"},
            {"name": "add_layer", "description": "Add layer"},
        ]

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if tool_name == "execute_processing":
            return {
                "status": "success",
                "result": {
                    "success": True,
                    "outputs": {"OUTPUT": arguments["parameters"]["OUTPUT"]},
                },
            }
        return {
            "status": "success",
            "result": {"success": True, "loaded": arguments["path"]},
        }


def _task_plan():
    return TaskPlan(
        task_id="task-controlled",
        task_summary="Buffer and load output",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[
            OutputSpec(
                output_id="buffer_output",
                description="Buffered roads",
                path="shared/outputs/roads_buffer.gpkg",
            )
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="Run buffer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=["processing.run"],
                allowed_tools=["execute_processing"],
                input_refs=[],
                output_refs=["buffer_output"],
                risk_level="low",
                requires_review=False,
            ),
            PlanStep(
                step_id=2,
                description="Load generated output layer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=["buffer_output"],
                risk_level="low",
                requires_review=False,
            ),
        ],
        metadata={
            "tool_calls": [
                {
                    "step_id": 1,
                    "tool_name": "execute_processing",
                    "arguments": {
                        "algorithm": "native:buffer",
                        "parameters": {
                            "INPUT": "shared/uploads/roads.geojson",
                            "DISTANCE": 100,
                            "SEGMENTS": 5,
                            "END_CAP_STYLE": 0,
                            "JOIN_STYLE": 0,
                            "MITER_LIMIT": 2,
                            "DISSOLVE": False,
                            "OUTPUT": "shared/outputs/roads_buffer.gpkg",
                        },
                    },
                }
            ]
        },
    )


def test_executor_node_runs_task_plan_through_execution_loop(monkeypatch):
    asyncio.run(_run_executor_node_assertions(monkeypatch))


async def _run_executor_node_assertions(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)

    state = create_initial_state("session-controlled", "Buffer roads")
    state["task_plan"] = _task_plan()
    state["status"] = True
    state["tool_selection"] = ["execute_processing", "add_layer"]

    result = await executor_node(state)

    assert [call[0] for call in fake_client.calls] == [
        "execute_processing",
        "add_layer",
    ]
    assert result["is_completed"] is True
    assert result["execution_trace"].status == "success"
    assert len(result["execution_trace"].attempts) == 2
    assert result["messages"][-1].content.startswith("受控执行完成")
