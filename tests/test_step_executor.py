import asyncio

from agent.tools.step_executor import StepExecutor
from agent.tools.tool_contracts import ExecutionTrace, ToolCallPlan, ToolSpec
from agent.tools.tool_registry import ToolRegistry


class MockMCPClient:
    def __init__(self, response=None, exc: BaseException | None = None):
        self.response = response
        self.exc = exc
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if self.exc is not None:
            raise self.exc
        return self.response


def _registry(*tool_names: str) -> ToolRegistry:
    return ToolRegistry.from_tool_specs([ToolSpec(name=name) for name in tool_names])


def test_step_executor_records_successful_tool_attempt(tmp_path):
    client = MockMCPClient(
        response={
            "status": "success",
            "result": {"path": "shared/screenshots/map.png"},
        }
    )
    executor = StepExecutor(
        mcp_client=client,
        registry=_registry("capture_map_canvas"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    call = ToolCallPlan(
        step_id=1,
        tool_name="capture_map_canvas",
        arguments={"path": "shared/screenshots/map.png"},
    )

    attempt = asyncio.run(
        executor.execute(call, trace, allowed_tools=["capture_map_canvas"])
    )

    assert client.calls == [
        ("capture_map_canvas", {"path": "shared/screenshots/map.png"})
    ]
    assert attempt.status == "success"
    assert attempt.step_id == 1
    assert attempt.observation.status == "success"
    assert attempt.artifacts == [{"kind": "path", "path": "shared/screenshots/map.png"}]
    assert attempt.duration_ms >= 0
    assert trace.latest_attempt() == attempt
    assert trace.status == "success"


def test_step_executor_records_normalized_failure_attempt(tmp_path):
    client = MockMCPClient(
        response={"status": "error", "message": "Missing required parameter: path"}
    )
    executor = StepExecutor(
        mcp_client=client,
        registry=_registry("add_layer"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(step_id=2, tool_name="add_layer", arguments={})

    attempt = asyncio.run(executor.execute(call, trace, allowed_tools=["add_layer"]))

    assert attempt.status == "failed"
    assert attempt.observation.status == "error"
    assert attempt.observation.error_type == "parameter_error"
    assert trace.status == "failed"
    assert trace.latest_attempt() == attempt


def test_step_executor_records_disconnect_exception_attempt(tmp_path):
    client = MockMCPClient(exc=ConnectionError("Could not connect to Qgis socket"))
    executor = StepExecutor(
        mcp_client=client,
        registry=_registry("ping"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(step_id=3, tool_name="ping", arguments={})

    attempt = asyncio.run(executor.execute(call, trace, allowed_tools=["ping"]))

    assert attempt.status == "failed"
    assert attempt.observation.status == "disconnected"
    assert attempt.observation.error_type == "qgis_disconnected"
    assert attempt.observation.retryable is True
    assert trace.status == "failed"


def test_step_executor_does_not_swallow_cancellation(tmp_path):
    client = MockMCPClient(exc=asyncio.CancelledError())
    executor = StepExecutor(
        mcp_client=client,
        registry=_registry("ping"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(step_id=3, tool_name="ping", arguments={})

    try:
        asyncio.run(executor.execute(call, trace, allowed_tools=["ping"]))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError should be re-raised")

    assert trace.attempts == []
    assert trace.status == "running"


def test_step_executor_exception_attempt_remains_json_serializable(tmp_path):
    client = MockMCPClient(exc=ConnectionError("Could not connect to Qgis socket"))
    executor = StepExecutor(
        mcp_client=client,
        registry=_registry("ping"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(step_id=3, tool_name="ping", arguments={})

    attempt = asyncio.run(executor.execute(call, trace, allowed_tools=["ping"]))

    assert attempt.observation.raw_response == {
        "exception_type": "ConnectionError",
        "message": "Could not connect to Qgis socket",
    }
    assert "ConnectionError" in trace.model_dump_json()


def test_step_executor_records_policy_blocked_attempt_without_calling_mcp(tmp_path):
    client = MockMCPClient(response={"status": "success", "result": {"ok": True}})
    executor = StepExecutor(
        mcp_client=client,
        registry=_registry("remove_layer"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-1")
    call = ToolCallPlan(step_id=4, tool_name="remove_layer", arguments={})

    attempt = asyncio.run(executor.execute(call, trace, allowed_tools=["ping"]))

    assert client.calls == []
    assert attempt.status == "blocked"
    assert attempt.observation.status == "error"
    assert attempt.observation.error_type == "unknown_error"
    assert attempt.observation.message == "Tool is outside allowed scope: remove_layer"
    assert attempt.metadata["policy_reason"] == "tool_not_allowed"
    assert trace.status == "failed"
