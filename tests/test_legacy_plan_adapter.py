from agent.planning.legacy_adapter import legacy_plan_to_task_plan, task_plan_to_legacy_plan
from agent.planning.plan_schema import PlanStep, TaskPlan
from agent.state import Plan, Step


def test_legacy_plan_to_task_plan_preserves_steps_and_api_candidates():
    legacy = Plan(
        task="Buffer a vector layer",
        steps=[
            Step(
                step_id=1,
                description="Create buffer",
                gdal_api=["gdal.VectorTranslate"],
                pyqgis_api=["processing.run"],
            )
        ],
        metadata={"risk_level": "medium"},
    )

    task_plan = legacy_plan_to_task_plan(legacy, user_query="buffer request")

    assert task_plan.task_summary == legacy.task
    assert task_plan.metadata["legacy_task"] == legacy.task
    assert task_plan.metadata["user_query"] == "buffer request"
    assert task_plan.steps[0].candidate_gdal_apis == ["gdal.VectorTranslate"]
    assert task_plan.steps[0].candidate_pyqgis_apis == ["processing.run"]


def test_task_plan_to_legacy_plan_keeps_review_shape_and_metadata():
    task_plan = TaskPlan(
        task_id="task-adapt",
        task_summary="Clip input layer",
        risk_level="high",
        review_status="pending_review",
        steps=[
            PlanStep(
                step_id=1,
                description="Clip layer",
                candidate_gdal_apis=["gdal.VectorTranslate"],
                candidate_pyqgis_apis=["processing.run"],
                allowed_tools=["qgis_processing"],
                input_refs=["input"],
                output_refs=["output"],
                risk_level="high",
                requires_review=True,
            )
        ],
        metadata={"validation_summary": "1 issue found"},
    )

    legacy = task_plan_to_legacy_plan(task_plan)

    assert legacy.task == "Clip input layer"
    assert legacy.steps[0].gdal_api == ["gdal.VectorTranslate"]
    assert legacy.steps[0].pyqgis_api == ["processing.run"]
    assert legacy.metadata["task_plan_id"] == "task-adapt"
    assert legacy.metadata["risk_level"] == "high"
    assert legacy.metadata["review_status"] == "pending_review"
    assert legacy.metadata["validation_summary"] == "1 issue found"
