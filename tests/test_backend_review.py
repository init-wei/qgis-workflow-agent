from ui.backend.main import build_plan_review_update
from agent.planning.plan_schema import PlanStep, TaskPlan
from agent.state import Plan, Step


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
