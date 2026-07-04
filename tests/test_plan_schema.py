import pytest
from pydantic import ValidationError

from agent.planning.plan_schema import (
    InputSpec,
    OutputSpec,
    PlanStep,
    TaskPlan,
)


def test_task_plan_accepts_minimal_contract():
    plan = TaskPlan(
        task_id="task-1",
        task_summary="Load a vector layer and create a buffer",
        risk_level="medium",
        review_status="draft",
        required_inputs=[
            InputSpec(input_id="input-layer", description="Source vector layer", path="data/input.gpkg")
        ],
        expected_outputs=[
            OutputSpec(output_id="buffer-layer", description="Buffered output", path="data/output.gpkg")
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="Create a 100 meter buffer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=["processing.run"],
                allowed_tools=["qgis_processing"],
                input_refs=["input-layer"],
                output_refs=["buffer-layer"],
                risk_level="medium",
                requires_review=False,
            )
        ],
    )

    assert plan.task_id == "task-1"
    assert plan.steps[0].step_type == "qgis_operation"
    assert plan.metadata == {}


def test_task_plan_rejects_invalid_risk_level():
    with pytest.raises(ValidationError):
        TaskPlan(
            task_id="task-1",
            task_summary="Bad risk",
            risk_level="unsafe",
            review_status="draft",
            steps=[],
        )


def test_task_plan_requires_review_status():
    with pytest.raises(ValidationError):
        TaskPlan(
            task_id="task-1",
            task_summary="Missing review status",
            risk_level="low",
            steps=[],
        )


def test_plan_step_requires_review_is_strict_bool():
    with pytest.raises(ValidationError, match="requires_review"):
        PlanStep(
            step_id=1,
            description="Load layer",
            allowed_tools=["add_layer"],
            risk_level="low",
            requires_review="False",
        )
