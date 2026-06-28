from ui.backend.main import build_plan_review_update
from ui.backend.main import (
    RiskReviewDecisionRequest,
    build_risk_review_feedback,
    serialize_risk_review_required,
)
from agent.planning.plan_schema import PlanStep, TaskPlan
from agent.state import Plan, Step
from agent.tools.tool_contracts import HumanFeedback, ToolCallPlan


def test_build_plan_review_update_promotes_legacy_and_task_plan():
    draft = Plan(
        task="Load a layer",
        steps=[Step(step_id=1, description="Load vector", gdal_api=[], pyqgis_api=[])],
    )
    task_plan_draft = TaskPlan(
        task_id="task-review",
        task_summary="Load a layer",
        risk_level="low",
        review_status="pending_review",
        steps=[
            PlanStep(
                step_id=1,
                description="Load vector",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=[],
                input_refs=[],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
    )

    update = build_plan_review_update(
        {"draft": draft, "task_plan_draft": task_plan_draft},
        approved=True,
    )

    assert update["plan"] is draft
    assert update["status"] is True
    assert update["task_plan"].review_status == "approved"
    assert task_plan_draft.review_status == "pending_review"


def test_build_plan_review_update_rejects_with_advise():
    update = build_plan_review_update({}, approved=False, advise="请增加输出路径")

    assert update == {"status": False, "advise": "请增加输出路径"}


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
