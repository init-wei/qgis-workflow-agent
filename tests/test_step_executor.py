import asyncio
import base64
import hashlib

import pytest

from agent.tools.step_executor import StepExecutor
from agent.tools.tool_contracts import ExecutionTrace, ToolCallPlan, ToolSpec
from agent.tools.tool_registry import ToolRegistry

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
)

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


@pytest.mark.parametrize('mode', ['missing', 'wrong_path', 'unreadable'])
def test_step_executor_never_confirms_an_unverified_screenshot(tmp_path, mode):
    requested = tmp_path / 'shared/outputs/task-1/r1/image/map.png'
    reported = requested if mode != 'wrong_path' else requested.with_name('other.png')

    class Client:
        async def call_tool(self, tool_name, arguments):
            if mode != 'missing':
                reported.parent.mkdir(parents=True)
                reported.write_bytes(b'file')
                if mode == 'unreadable':
                    reported.chmod(0)
            return {'status': 'success', 'result': {'success': True, 'path': str(reported)}}

    call = ToolCallPlan(step_id=1, tool_name='capture_map_canvas', arguments={'path': str(requested)})
    executor = StepExecutor(Client(), _registry('capture_map_canvas'), tmp_path)
    attempt = asyncio.run(executor.execute(call, ExecutionTrace(trace_id='trace')))
    assert attempt.status == 'failed'
    assert attempt.observation.outcome == 'unknown'


def test_step_executor_records_successful_tool_attempt(tmp_path):
    path = "shared/outputs/task-1/r1/image/map.png"

    class ImageClient(MockMCPClient):
        async def call_tool(self, tool_name, arguments):
            output = tmp_path / path
            output.parent.mkdir(parents=True)
            output.write_bytes(PNG)
            return await super().call_tool(tool_name, arguments)

    client = ImageClient(response={"status": "success", "result": {"success": True, "path": path}})
    executor = StepExecutor(
        mcp_client=client,
        registry=_registry("capture_map_canvas"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    call = ToolCallPlan(
        step_id=1,
        tool_name="capture_map_canvas",
        arguments={"path": path},
    )

    attempt = asyncio.run(
        executor.execute(call, trace, allowed_tools=["capture_map_canvas"])
    )

    assert client.calls == [
        ("capture_map_canvas", {"path": path})
    ]
    assert attempt.status == "success"
    assert attempt.step_id == 1
    assert attempt.observation.status == "success"
    assert attempt.artifacts == [{"kind": "path", "path": path, "size_bytes": len(PNG),
                                  "sha256": hashlib.sha256(PNG).hexdigest()}]
    assert attempt.duration_ms >= 0
    assert trace.latest_attempt() == attempt
    assert trace.status == "success"


@pytest.mark.parametrize('mode', ['correct', 'missing', 'wrong_path', 'wrong_algorithm'])
def test_step_executor_verifies_processing_artifact_and_algorithm(tmp_path, mode):
    requested = tmp_path / 'shared/outputs/task-1/r1/output/result.gpkg'
    reported = requested if mode != 'wrong_path' else requested.with_name('other.gpkg')
    algorithm = 'native:buffer'

    class Client:
        async def call_tool(self, tool_name, arguments):
            if mode != 'missing':
                reported.parent.mkdir(parents=True)
                reported.write_bytes(b'synthetic result')
            return {'status': 'success', 'result': {
                'algorithm': algorithm if mode != 'wrong_algorithm' else 'native:clip',
                'result': {'OUTPUT': str(reported)},
            }}

    call = ToolCallPlan(step_id=1, tool_name='execute_processing', arguments={
        'algorithm': algorithm, 'parameters': {'OUTPUT': str(requested)}})
    executor = StepExecutor(Client(), _registry('execute_processing'), tmp_path)
    attempt = asyncio.run(executor.execute(call, ExecutionTrace(trace_id='trace')))
    assert attempt.status == ('success' if mode == 'correct' else 'failed')
    assert attempt.observation.outcome == ('completed' if mode == 'correct' else 'unknown')
    if mode == 'correct':
        assert attempt.artifacts == [{
            'kind': 'path', 'path': str(requested), 'size_bytes': 16,
            'sha256': hashlib.sha256(b'synthetic result').hexdigest(),
        }]


def test_step_executor_rejects_shapefile_without_required_companions(tmp_path):
    requested = tmp_path / 'shared/outputs/task-1/r1/output/result.shp'

    class Client:
        async def call_tool(self, tool_name, arguments):
            requested.parent.mkdir(parents=True)
            requested.write_bytes(b'incomplete shapefile')
            return {'status': 'success', 'result': {
                'algorithm': 'native:buffer', 'result': {'OUTPUT': str(requested)},
            }}

    call = ToolCallPlan(step_id=1, tool_name='execute_processing', arguments={
        'algorithm': 'native:buffer', 'parameters': {'OUTPUT': str(requested)}})
    attempt = asyncio.run(StepExecutor(Client(), _registry('execute_processing'), tmp_path).execute(
        call, ExecutionTrace(trace_id='trace')))
    assert attempt.status == 'failed'
    assert attempt.observation.outcome == 'unknown'


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
