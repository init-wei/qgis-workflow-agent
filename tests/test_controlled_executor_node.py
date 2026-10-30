import asyncio
from pathlib import Path

from agent.nodes.executor_node import executor_node
from agent.planning.plan_schema import InputSpec, PlanStep, TaskPlan, ToolCallPlan
from agent.state import create_initial_state


class FakeMCPClient:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [{"name": "add_layer", "description": "Add a selected vector layer"}]

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return {
            "status": "success",
            "result": {
                "id": "layer-1",
                "name": arguments["name"],
                "type": "vector",
                "feature_count": 3,
            },
        }


def _load_plan(review_status: str = "approved", *, input_ref: str = "source") -> TaskPlan:
    source_path = "shared/uploads/phase11_points.geojson"
    return TaskPlan(
        task_id="task-load-points",
        revision=1,
        task_summary="Load the selected point layer",
        risk_level="low",
        review_status=review_status,
        required_inputs=[
            InputSpec(
                input_id="source",
                description="Selected source",
                file_id="file-1",
                path=source_path,
                source="phase11_points.geojson",
            )
        ],
        expected_outputs=[],
        steps=[
            PlanStep(
                step_id=1,
                description="Load the selected vector layer",
                allowed_tools=["add_layer"],
                input_refs=[input_ref],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        tool_calls=[
            ToolCallPlan(
                step_id=1,
                tool_name="add_layer",
                arguments={
                    "path": source_path,
                    "layer_type": "vector",
                    "name": "phase11_points",
                },
            )
        ],
    )


def test_executor_does_not_call_mcp_for_an_unapproved_task_plan(monkeypatch):
    asyncio.run(_assert_executor_does_not_call_mcp_before_plan_approval(monkeypatch))


async def _assert_executor_does_not_call_mcp_before_plan_approval(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)
    monkeypatch.setattr("agent.nodes.executor_node.save_task_execution_record", lambda **_kwargs: None)
    state = create_initial_state("session-pending", "Load the selected layer")
    state["task_plan"] = _load_plan("pending_review")

    result = await executor_node(state)

    assert fake_client.calls == []
    assert result["is_completed"] is False
    assert "未批准" in result["messages"][0].content


def test_executor_runs_the_exact_approved_task_plan_call(monkeypatch, tmp_path):
    fixture = Path(__file__).parent / "fixtures/gis/phase11_points.geojson"
    selected = tmp_path / "shared/uploads/phase11_points.geojson"
    selected.parent.mkdir(parents=True)
    selected.write_bytes(fixture.read_bytes())
    monkeypatch.chdir(tmp_path)
    asyncio.run(_assert_executor_runs_exact_call(monkeypatch))


async def _assert_executor_runs_exact_call(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)
    monkeypatch.setattr("agent.nodes.executor_node.save_task_execution_record", lambda **_kwargs: None)
    state = create_initial_state("session-approved", "Load the selected layer")
    state["task_plan"] = _load_plan("approved")

    result = await executor_node(state)

    assert fake_client.calls == [
        (
            "add_layer",
            {
                "path": "shared/uploads/phase11_points.geojson",
                "layer_type": "vector",
                "name": "phase11_points",
            },
        )
    ]
    assert result["execution_trace"].status == "success"
    assert result["execution_trace"].attempts[0].observation.data["feature_count"] == 3


def test_executor_rejects_invalid_references_before_mcp(monkeypatch):
    asyncio.run(_assert_executor_rejects_invalid_references(monkeypatch))


async def _assert_executor_rejects_invalid_references(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)
    monkeypatch.setattr("agent.nodes.executor_node.save_task_execution_record", lambda **_kwargs: None)
    state = create_initial_state("session-invalid", "Load the selected layer")
    state["task_plan"] = _load_plan("approved", input_ref="missing")

    result = await executor_node(state)

    assert fake_client.calls == []
    assert result["is_completed"] is False
    assert "校验失败" in result["messages"][0].content
