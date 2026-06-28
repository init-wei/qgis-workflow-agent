import asyncio

from agent.nodes.executor_node import executor_node
from agent.planning.plan_schema import OutputSpec, PlanStep, TaskPlan
from agent.state import create_initial_state
from agent.tools.tool_contracts import HumanFeedback


class FakeMCPClient:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [
            {"name": "execute_processing", "description": "Run processing"},
            {"name": "add_layer", "description": "Add layer"},
            {"name": "execute_code", "description": "Execute code"},
            {"name": "save_project", "description": "Save project"},
        ]

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if arguments.get("path") == "shared/uploads/missing.gpkg":
            return {
                "status": "error",
                "message": "File not found: shared/uploads/missing.gpkg",
            }
        if tool_name == "execute_code":
            return {"status": "success", "result": {"executed": True}}
        if tool_name == "save_project":
            return {"status": "success", "result": {"saved": arguments["path"]}}
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
    assert result["is_completed"] is None
    assert result["execution_trace"].status == "success"
    assert len(result["execution_trace"].attempts) == 2
    assert result["messages"][-1].content.startswith("受控执行完成")


def test_executor_node_pauses_for_risk_review_without_failing(monkeypatch):
    asyncio.run(_run_risk_pause_assertions(monkeypatch))


async def _run_risk_pause_assertions(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)

    state = create_initial_state("session-risk", "Run reviewed code")
    state["task_plan"] = _high_risk_task_plan()
    state["status"] = True
    state["tool_selection"] = ["execute_code", "add_layer"]

    result = await executor_node(state)

    assert fake_client.calls == []
    assert result["is_completed"] is None
    assert result["pending_risk_review"].status == "pending"
    assert result["pending_risk_review"].tool_call.tool_name == "execute_code"
    assert result["execution_cursor"] == 0
    assert result["execution_trace"].latest_attempt().status == "blocked"
    assert result["messages"][-1].content.startswith("风险审核待处理")


def test_executor_node_resumes_from_approved_risk_review(monkeypatch):
    asyncio.run(_run_risk_resume_assertions(monkeypatch))


async def _run_risk_resume_assertions(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)

    task_plan = _high_risk_task_plan()
    reviewed_call = task_plan.metadata["tool_calls"][0]
    feedback = HumanFeedback(
        review_id="review-1",
        status="approved",
        tool_call=_tool_call_from_metadata(task_plan, reviewed_call),
    )
    state = create_initial_state("session-risk", "Run reviewed code")
    state["task_plan"] = task_plan
    state["status"] = True
    state["tool_selection"] = ["execute_code", "add_layer"]

    pause_result = await executor_node(state)
    feedback = pause_result["pending_risk_review"].model_copy(
        update={"status": "approved"}
    )
    state["execution_trace"] = pause_result["execution_trace"]
    state["risk_review_feedback"] = feedback
    state["execution_cursor"] = pause_result["execution_cursor"]

    result = await executor_node(state)

    assert [call[0] for call in fake_client.calls] == ["execute_code", "add_layer"]
    assert result["pending_risk_review"] is None
    assert result["risk_review_feedback"] is None
    assert result["execution_cursor"] is None
    assert result["is_completed"] is None
    assert result["execution_trace"].status == "success"
    assert [attempt.status for attempt in result["execution_trace"].attempts] == [
        "blocked",
        "success",
        "success",
    ]


def test_executor_node_rejected_risk_review_stops_without_mcp(monkeypatch):
    asyncio.run(_run_rejected_risk_review_assertions(monkeypatch))


async def _run_rejected_risk_review_assertions(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)

    task_plan = _high_risk_task_plan()
    reviewed_call = task_plan.metadata["tool_calls"][0]
    feedback = HumanFeedback(
        review_id="review-2",
        status="rejected",
        tool_call=_tool_call_from_metadata(task_plan, reviewed_call),
        comment="Do not run code",
    )
    state = create_initial_state("session-risk", "Run reviewed code")
    state["task_plan"] = task_plan
    state["status"] = True
    state["tool_selection"] = ["execute_code", "add_layer"]
    state["risk_review_feedback"] = feedback
    state["execution_cursor"] = 0

    result = await executor_node(state)

    assert fake_client.calls == []
    assert result["is_completed"] is False
    assert result["pending_risk_review"] is None
    assert result["execution_trace"].latest_attempt().status == "blocked"
    assert "risk_review_rejected" in result["messages"][-1].content


def test_executor_node_treats_non_risk_needs_review_as_failure(monkeypatch):
    asyncio.run(_run_non_risk_needs_review_assertions(monkeypatch))


async def _run_non_risk_needs_review_assertions(monkeypatch):
    fake_client = FakeMCPClient()

    async def fake_get_mcp_client():
        return fake_client

    monkeypatch.setattr("agent.nodes.executor_node.get_mcp_client", fake_get_mcp_client)

    state = create_initial_state("session-missing-file", "Load missing layer")
    state["task_plan"] = _missing_file_task_plan()
    state["status"] = True
    state["tool_selection"] = ["add_layer"]

    result = await executor_node(state)

    assert [call[0] for call in fake_client.calls] == ["add_layer"]
    assert result["is_completed"] is False
    assert result["pending_risk_review"] is None
    assert result["execution_cursor"] is None
    assert result["messages"][-1].content.startswith("受控执行停止")
    assert "missing_file_requires_human" in result["messages"][-1].content


def _high_risk_task_plan():
    return TaskPlan(
        task_id="task-risk",
        task_summary="Run reviewed code then load layer",
        risk_level="high",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[
            OutputSpec(
                output_id="loaded_output",
                description="Loaded layer",
                path="shared/outputs/reviewed.gpkg",
            )
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="Run reviewed code",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["execute_code"],
                input_refs=[],
                output_refs=[],
                risk_level="high",
                requires_review=True,
            ),
            PlanStep(
                step_id=2,
                description="Load generated output layer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=["loaded_output"],
                risk_level="low",
                requires_review=False,
            ),
        ],
        metadata={
            "tool_calls": [
                {
                    "step_id": 1,
                    "tool_name": "execute_code",
                    "arguments": {"code": "print('reviewed')"},
                }
            ]
        },
    )


def _missing_file_task_plan():
    return TaskPlan(
        task_id="task-missing-file",
        task_summary="Load a missing layer",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[],
        steps=[
            PlanStep(
                step_id=1,
                description="Load missing output layer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={
            "tool_calls": [
                {
                    "step_id": 1,
                    "tool_name": "add_layer",
                    "arguments": {
                        "path": "shared/uploads/missing.gpkg",
                        "layer_type": "vector",
                    },
                }
            ]
        },
    )


def _tool_call_from_metadata(task_plan, raw_call):
    from agent.tools.tool_call_planner import compile_task_plan_to_tool_calls

    _ = raw_call
    return compile_task_plan_to_tool_calls(task_plan)[0]
