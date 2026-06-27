from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan
from agent.planning.plan_validator import PlanValidator


def _plan(**updates):
    data = {
        "task_id": "task-validate",
        "task_summary": "Validate generated plan",
        "risk_level": "medium",
        "review_status": "draft",
        "required_inputs": [
            InputSpec(input_id="input", description="Input layer", path="data/input.gpkg")
        ],
        "expected_outputs": [
            OutputSpec(output_id="output", description="Output layer", path="data/output.gpkg")
        ],
        "steps": [
            PlanStep(
                step_id=1,
                description="Run a processing algorithm",
                candidate_gdal_apis=["gdal.Warp"],
                candidate_pyqgis_apis=["processing.run"],
                allowed_tools=["qgis_processing"],
                input_refs=["input"],
                output_refs=["output"],
                risk_level="medium",
                requires_review=False,
            )
        ],
    }
    data.update(updates)
    return TaskPlan(**data)


def test_validator_accepts_specific_low_risk_plan():
    report = PlanValidator().validate(_plan(risk_level="low"))

    assert report.is_valid is True
    assert report.requires_review is False
    assert report.issues == []


def test_validator_flags_missing_input_path_or_source():
    report = PlanValidator().validate(
        _plan(required_inputs=[InputSpec(input_id="input", description="Input layer")])
    )

    assert report.is_valid is False
    assert {issue.code for issue in report.issues} == {"missing_input_path"}


def test_validator_flags_missing_expected_output():
    report = PlanValidator().validate(_plan(expected_outputs=[]))

    assert report.is_valid is False
    assert any(issue.code == "missing_output" for issue in report.issues)


def test_validator_flags_coarse_api_reference():
    plan = _plan(
        steps=[
            PlanStep(
                step_id=1,
                description="Load layer",
                candidate_gdal_apis=["gdal"],
                candidate_pyqgis_apis=["QgsVectorLayer"],
                allowed_tools=[],
                input_refs=["input"],
                output_refs=["output"],
                risk_level="low",
                requires_review=False,
            )
        ]
    )

    report = PlanValidator().validate(plan)

    assert report.is_valid is False
    assert any(issue.code == "coarse_api_reference" for issue in report.issues)


def test_validator_requires_review_for_high_risk_step_and_auto_marks_it():
    plan = _plan(
        steps=[
            PlanStep(
                step_id=1,
                description="Overwrite output layer and save project",
                candidate_gdal_apis=["gdal.Warp"],
                candidate_pyqgis_apis=[],
                allowed_tools=["execute_code"],
                input_refs=["input"],
                output_refs=["output"],
                risk_level="high",
                requires_review=False,
            )
        ]
    )

    report = PlanValidator().validate(plan)

    assert report.requires_review is True
    assert plan.steps[0].requires_review is True
    assert any(issue.code == "requires_review_missing" for issue in report.issues)
