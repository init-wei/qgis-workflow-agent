import asyncio

from agent.tools.execution_loop import ExecutionLoop
from agent.tools.risk_review import RiskReviewManager
from agent.tools.tool_contracts import (
    ExecutionTrace,
    HumanFeedback,
    ToolCallPlan,
    ToolSpec,
)
from agent.tools.tool_registry import ToolRegistry
from ui.backend.main import RiskReviewDecisionRequest, build_risk_review_feedback


class SequenceMCPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.responses.pop(0)


def _registry(*tool_names: str) -> ToolRegistry:
    return ToolRegistry.from_tool_specs([ToolSpec(name=name) for name in tool_names])


def test_execute_code_without_feedback_pauses_for_risk_review(tmp_path):
    client = SequenceMCPClient([{"status": "success", "result": {"ok": True}}])
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("execute_code"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-risk")
    call = ToolCallPlan(
        step_id=1,
        tool_name="execute_code",
        arguments={"code": "print('hello')"},
        risk_level="high",
        requires_review=True,
    )

    result = asyncio.run(loop.run(call, trace, allowed_tools=["execute_code"]))

    assert result.status == "needs_review"
    assert result.decision.outcome == "ask_human"
    assert client.calls == []
    assert trace.latest_attempt().status == "blocked"
    risk_review = trace.latest_attempt().metadata["risk_review"]
    assert risk_review["status"] == "pending"
    assert risk_review["tool_call"]["tool_name"] == "execute_code"


def test_approved_feedback_resumes_high_risk_call(tmp_path):
    client = SequenceMCPClient([{"status": "success", "result": {"ok": True}}])
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("execute_code"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-risk")
    call = ToolCallPlan(
        step_id=1,
        tool_name="execute_code",
        arguments={"code": "print('hello')"},
        risk_level="high",
        requires_review=True,
    )
    feedback = HumanFeedback(
        review_id="review-1",
        status="approved",
        tool_call=call,
        comment="Looks safe",
        reviewer="tester",
    )

    result = asyncio.run(
        loop.run(
            call,
            trace,
            allowed_tools=["execute_code"],
            human_feedback=feedback,
        )
    )

    assert result.status == "success"
    assert client.calls == [("execute_code", {"code": "print('hello')"})]
    assert trace.latest_attempt().metadata["risk_review"]["status"] == "approved"
    assert trace.latest_attempt().metadata["risk_review"]["reviewer"] == "tester"


def test_modified_feedback_runs_modified_arguments_and_records_change(tmp_path):
    client = SequenceMCPClient([{"status": "success", "result": {"ok": True}}])
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("save_project"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-risk")
    call = ToolCallPlan(
        step_id=2,
        tool_name="save_project",
        arguments={"path": "shared/projects/original.qgz"},
        risk_level="medium",
    )
    feedback = HumanFeedback(
        review_id="review-2",
        status="modified",
        tool_call=call,
        modified_arguments={"path": "shared/projects/reviewed.qgz"},
        comment="Use reviewed output path",
    )

    result = asyncio.run(
        loop.run(
            call,
            trace,
            allowed_tools=["save_project"],
            human_feedback=feedback,
        )
    )

    assert result.status == "success"
    assert client.calls == [("save_project", {"path": "shared/projects/reviewed.qgz"})]
    risk_review = trace.latest_attempt().metadata["risk_review"]
    assert risk_review["status"] == "modified"
    assert risk_review["original_arguments"] == {"path": "shared/projects/original.qgz"}
    assert risk_review["modified_arguments"] == {"path": "shared/projects/reviewed.qgz"}


def test_rejected_feedback_aborts_without_calling_mcp(tmp_path):
    client = SequenceMCPClient([{"status": "success", "result": {"ok": True}}])
    loop = ExecutionLoop(
        mcp_client=client,
        registry=_registry("remove_layer"),
        workspace_root=tmp_path,
    )
    trace = ExecutionTrace(trace_id="trace-risk")
    call = ToolCallPlan(
        step_id=3,
        tool_name="remove_layer",
        arguments={"layer_id": "roads"},
    )
    feedback = HumanFeedback(
        review_id="review-3",
        status="rejected",
        tool_call=call,
        reason="reviewer_rejected",
        comment="Do not remove this layer",
    )

    result = asyncio.run(
        loop.run(
            call,
            trace,
            allowed_tools=["remove_layer"],
            human_feedback=feedback,
        )
    )

    assert result.status == "failed"
    assert result.decision.reason == "risk_review_rejected"
    assert client.calls == []
    assert trace.latest_attempt().metadata["risk_review"]["status"] == "rejected"


def test_review_manager_flags_save_overwrite_and_delete_semantics():
    manager = RiskReviewManager()

    assert manager.requires_risk_review(
        ToolCallPlan(step_id=1, tool_name="save_project", arguments={})
    )
    assert manager.requires_risk_review(
        ToolCallPlan(
            step_id=2,
            tool_name="execute_processing",
            arguments={"parameters": {"OVERWRITE": True}},
        )
    )
    assert manager.requires_risk_review(
        ToolCallPlan(
            step_id=3,
            tool_name="layer_operation",
            arguments={"operation": "delete selected features"},
        )
    )
    assert not manager.requires_risk_review(
        ToolCallPlan(
            step_id=4,
            tool_name="set_layer_visibility",
            arguments={"visible": True},
        )
    )


def test_backend_builds_risk_review_feedback_from_api_shape():
    call = ToolCallPlan(
        step_id=4,
        tool_name="save_project",
        arguments={"path": "shared/projects/original.qgz"},
    )
    request = RiskReviewDecisionRequest(
        thread_id="thread-1",
        review_id="review-4",
        status="modified",
        comment="Change output path",
        modified_arguments={"path": "shared/projects/final.qgz"},
        reviewer="tester",
    )

    feedback = build_risk_review_feedback(request, call)

    assert feedback.review_id == "review-4"
    assert feedback.status == "modified"
    assert feedback.modified_arguments == {"path": "shared/projects/final.qgz"}
    assert feedback.metadata["thread_id"] == "thread-1"
