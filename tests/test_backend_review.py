import asyncio

import ui.backend.main as backend_main
from ui.backend.main import (
    RiskReviewDecisionRequest,
    build_risk_review_feedback,
    resolve_risk_review_tool_call,
    serialize_risk_review_required,
)
from fastapi import HTTPException
from agent.tools.tool_contracts import HumanFeedback, ToolCallPlan


def test_build_risk_review_feedback_uses_pending_tool_call():
    pending_call = ToolCallPlan(
        step_id=1,
        tool_name="execute_code",
        arguments={"code": "print('reviewed')"},
        risk_level="high",
        requires_review=True,
    )
    request = RiskReviewDecisionRequest(
        thread_id="thread-1",
        review_id="review-1",
        status="approved",
        reviewer="tester",
    )

    feedback = build_risk_review_feedback(request, pending_call)

    assert isinstance(feedback, HumanFeedback)
    assert feedback.tool_call == pending_call
    assert feedback.status == "approved"
    assert feedback.metadata["thread_id"] == "thread-1"


def test_serialize_risk_review_required_accepts_checkpoint_dict():
    pending_call = ToolCallPlan(
        step_id=1,
        tool_name="save_project",
        arguments={"path": "shared/projects/reviewed.qgz"},
        risk_level="high",
        requires_review=True,
    )
    feedback = HumanFeedback(
        review_id="review-2",
        status="pending",
        tool_call=pending_call,
        reason="risk_review_pending",
    )

    payload = serialize_risk_review_required(feedback.model_dump(mode="json"))

    assert payload["review_id"] == "review-2"
    assert payload["status"] == "pending"
    assert payload["tool_call"]["tool_name"] == "save_project"
    assert payload["tool_call"]["arguments"]["path"] == "shared/projects/reviewed.qgz"


def test_resolve_risk_review_tool_call_rejects_stale_review_id():
    pending_call = ToolCallPlan(
        step_id=1,
        tool_name="execute_code",
        arguments={"code": "print('current')"},
        risk_level="high",
        requires_review=True,
    )
    pending_feedback = HumanFeedback(
        review_id="current-review",
        status="pending",
        tool_call=pending_call,
    )
    request = RiskReviewDecisionRequest(
        thread_id="thread-1",
        review_id="stale-review",
        status="approved",
    )

    try:
        resolve_risk_review_tool_call(request, pending_feedback)
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "stale risk review" in exc.detail
    else:
        raise AssertionError("stale risk review decision was accepted")


def test_get_app_with_checkpointer_interrupts_before_risk_review(monkeypatch):
    asyncio.run(_assert_get_app_with_checkpointer_interrupts_before_risk_review(monkeypatch))


def test_default_upload_dir_matches_documented_shared_uploads_path():
    assert backend_main.DEFAULT_UPLOAD_DIR.name == "uploads"
    assert backend_main.DEFAULT_UPLOAD_DIR.parent == backend_main.SHARED_DIR


async def _assert_get_app_with_checkpointer_interrupts_before_risk_review(monkeypatch):
    captured = {}
    app = object()

    async def fake_get_graph(**kwargs):
        captured.update(kwargs)
        return app

    monkeypatch.setattr(backend_main, "_app_with_checkpointer", None)
    monkeypatch.setattr(backend_main, "get_graph", fake_get_graph)

    result = await backend_main.get_app_with_checkpointer()

    assert result is app
    assert captured["with_checkpointer"] is True
    assert captured["interrupt_before"] == ["human_review_node", "risk_review_node"]
    assert captured["interrupt_after"] == ["executor_node"]
