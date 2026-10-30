import asyncio

from agent.tools.execution_loop import ExecutionLoop
from agent.tools.tool_contracts import ExecutionTrace, ToolCallPlan, ToolSpec
from agent.tools.tool_registry import ToolRegistry


class SequenceMCPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _registry(*tool_names: str) -> ToolRegistry:
    return ToolRegistry.from_tool_specs([ToolSpec(name=name) for name in tool_names])


def test_parameter_error_does_not_execute_unreviewed_repair_arguments(tmp_path):
    client = SequenceMCPClient(
        [
            {"status": "error", "message": "Missing required parameter: path", "outcome": "not_started"},
            {
                "status": "success",
                "result": {"path": "shared/output/layer.gpkg"},
            },
        ]
    )
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("add_layer"),
        workspace_root=tmp_path,
        max_attempts=2,
    )
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    call = ToolCallPlan(
        step_id=1,
        tool_name="add_layer",
        arguments={},
        metadata={"repair_arguments": {"path": "shared/output/layer.gpkg"}},
    )

    result = asyncio.run(loop.run(call, trace, allowed_tools=["add_layer"]))

    assert result.status == "needs_review"
    assert result.attempts_used == 1
    assert result.decision.outcome == "ask_human"
    assert client.calls == [("add_layer", {})]
    assert trace.attempts[0].metadata["repair_action"]["action_type"] == "ask_human"



def test_execution_loop_file_not_found_needs_review_without_retry(tmp_path):
    client = SequenceMCPClient(
        [{"status": "error", "message": "Layer is not valid: data/missing.gpkg", "outcome": "not_started"}]
    )
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("add_layer"),
        workspace_root=tmp_path,
        max_attempts=2,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(
        step_id=1,
        tool_name="add_layer",
        arguments={"path": "data/missing.gpkg"},
    )

    result = asyncio.run(loop.run(call, trace, allowed_tools=["add_layer"]))

    assert result.status == "needs_review"
    assert result.attempts_used == 1
    assert result.decision.outcome == "ask_human"
    assert client.calls == [("add_layer", {"path": "data/missing.gpkg"})]
    assert trace.status == "failed"
    assert trace.latest_attempt().metadata["repair_action"]["action_type"] == (
        "ask_human"
    )


def test_execution_loop_retries_qgis_disconnect_and_succeeds(tmp_path):
    client = SequenceMCPClient(
        [
            ConnectionError("Could not connect to Qgis socket"),
            {"status": "success", "result": {"ok": True}},
        ]
    )
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("ping"),
        workspace_root=tmp_path,
        max_attempts=2,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(step_id=1, tool_name="ping", arguments={})

    result = asyncio.run(loop.run(call, trace, allowed_tools=["ping"]))

    assert result.status == "success"
    assert result.attempts_used == 2
    assert result.decision.outcome == "success"
    assert client.calls == [("ping", {}), ("ping", {})]
    assert trace.attempts[0].metadata["evaluation"]["error_type"] == (
        "qgis_disconnected"
    )
    assert trace.attempts[0].metadata["repair_action"]["action_type"] == (
        "update_arguments"
    )


def test_execution_loop_max_attempts_exhausted_fails(tmp_path):
    client = SequenceMCPClient(
        [
            ConnectionError("Could not connect to Qgis socket"),
            ConnectionError("Could not connect to Qgis socket"),
        ]
    )
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("ping"),
        workspace_root=tmp_path,
        max_attempts=2,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(step_id=1, tool_name="ping", arguments={})

    result = asyncio.run(loop.run(call, trace, allowed_tools=["ping"]))

    assert result.status == "failed"
    assert result.attempts_used == 2
    assert result.decision.outcome == "abort"
    assert len(trace.attempts) == 2
    assert trace.latest_attempt().metadata["evaluation"]["reason"] == (
        "max_attempts_exhausted"
    )


def test_write_call_with_unknown_outcome_is_not_replayed(tmp_path):
    client = SequenceMCPClient([
        ConnectionError("QGIS socket disconnected after request"),
        {"status": "success", "result": {"id": "layer-1"}},
    ])
    loop = ExecutionLoop(mcp_client=client, registry=_registry("add_layer"),
                         workspace_root=tmp_path, max_attempts=2)
    trace = ExecutionTrace(trace_id="trace-unknown")
    call = ToolCallPlan(step_id=1, tool_name="add_layer",
                        arguments={"path": "shared/uploads/points.geojson", "layer_type": "vector"})

    result = asyncio.run(loop.run(call, trace, allowed_tools=["add_layer"]))

    assert result.status != "success"
    assert len(client.calls) == 1
    assert trace.attempts[0].observation.status == "disconnected"
