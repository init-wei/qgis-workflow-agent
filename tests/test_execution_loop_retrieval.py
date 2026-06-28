import asyncio

from agent.tools.execution_loop import ExecutionLoop
from agent.tools.retrieval import ApiDocRecord, RetrievalContext
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


class FakeRetriever:
    def __init__(self):
        self.contexts = []

    async def retrieve(self, context: RetrievalContext) -> RetrievalContext:
        self.contexts.append(context)
        record = ApiDocRecord(
            api_name=context.candidate_apis[0] if context.candidate_apis else "QGIS",
            library="GDAL",
            content=f"Evidence for {context.query_source}: {context.query}",
            source="fake",
        )
        return context.model_copy(update={"records": [record]})


def _registry(*tool_names: str) -> ToolRegistry:
    return ToolRegistry.from_tool_specs([ToolSpec(name=name) for name in tool_names])


def test_execution_loop_records_retrieval_context_on_success(tmp_path):
    client = SequenceMCPClient([{"status": "success", "result": {"ok": True}}])
    retriever = FakeRetriever()
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("execute_processing"),
        workspace_root=tmp_path,
        retriever=retriever,
    )
    trace = ExecutionTrace(trace_id="trace-rag")
    call = ToolCallPlan(
        step_id=1,
        tool_name="execute_processing",
        arguments={"algorithm": "gdal:warpreproject"},
        metadata={"candidate_gdal_apis": ["osgeo.gdal.Warp"]},
    )

    result = asyncio.run(loop.run(call, trace, allowed_tools=["execute_processing"]))

    assert result.status == "success"
    assert len(retriever.contexts) == 1
    retrieval_context = trace.latest_attempt().metadata["retrieval_context"]
    assert retrieval_context["query_source"] == "initial"
    assert retrieval_context["records"][0]["api_name"] == "osgeo.gdal.Warp"


def test_parameter_error_triggers_error_driven_retrieval_before_retry(tmp_path):
    client = SequenceMCPClient(
        [
            {"status": "error", "message": "Missing required parameter: path"},
            {"status": "success", "result": {"ok": True}},
        ]
    )
    retriever = FakeRetriever()
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("add_layer"),
        workspace_root=tmp_path,
        retriever=retriever,
        max_attempts=2,
    )
    trace = ExecutionTrace(trace_id="trace-rag")
    call = ToolCallPlan(
        step_id=2,
        tool_name="add_layer",
        arguments={},
        metadata={
            "candidate_pyqgis_apis": ["QgsVectorLayer"],
            "repair_arguments": {"path": "shared/input/layer.gpkg"},
        },
    )

    result = asyncio.run(loop.run(call, trace, allowed_tools=["add_layer"]))

    assert result.status == "success"
    assert [context.query_source for context in retriever.contexts] == [
        "initial",
        "error",
        "initial",
    ]
    first_attempt = trace.attempts[0]
    second_attempt = trace.attempts[1]
    assert first_attempt.metadata["repair_retrieval_context"]["query_source"] == (
        "error"
    )
    assert (
        "parameter_error" in first_attempt.metadata["repair_retrieval_context"]["query"]
    )
    assert second_attempt.metadata["retrieval_context"]["query_source"] == "initial"
    assert (
        second_attempt.tool_call.metadata["retrieval_context"]["records"][0]["api_name"]
        == "QgsVectorLayer"
    )


def test_qgis_disconnect_does_not_trigger_error_driven_retrieval(tmp_path):
    client = SequenceMCPClient(
        [
            ConnectionError("Could not connect to Qgis socket"),
            {"status": "success", "result": {"ok": True}},
        ]
    )
    retriever = FakeRetriever()
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("ping"),
        workspace_root=tmp_path,
        retriever=retriever,
        max_attempts=2,
    )
    trace = ExecutionTrace(trace_id="trace-rag")
    call = ToolCallPlan(step_id=3, tool_name="ping", arguments={})

    result = asyncio.run(loop.run(call, trace, allowed_tools=["ping"]))

    assert result.status == "success"
    assert [context.query_source for context in retriever.contexts] == [
        "initial",
        "initial",
    ]
    assert "repair_retrieval_context" not in trace.attempts[0].metadata
