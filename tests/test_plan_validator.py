from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan, ToolCallPlan
from agent.planning.plan_validator import PlanValidator
import pytest


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
            OutputSpec(output_id="output", description="Output layer", path="shared/outputs/task-validate/r1/output/output.gpkg")
        ],
        "steps": [
            PlanStep(
                step_id=1,
                description="Run a processing algorithm",
                candidate_gdal_apis=["gdal.Warp"],
                candidate_pyqgis_apis=["processing.run"],
                allowed_tools=["execute_processing"],
                input_refs=["input"],
                output_refs=["output"],
                risk_level="low",
                requires_review=False,
            )
        ],
        "tool_calls": [
            ToolCallPlan(
                step_id=1,
                tool_name="execute_processing",
                arguments={
                    "algorithm": "native:buffer",
                    "parameters": {"INPUT": "data/input.gpkg", "OUTPUT": "shared/outputs/task-validate/r1/output/output.gpkg"},
                },
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


@pytest.mark.parametrize("tool", ["ping", "get_qgis_info", "get_project_info", "get_layers"])
def test_metadata_query_does_not_need_a_file_output_or_load_wording(tool):
    plan = _plan(
        risk_level="low", required_inputs=[], expected_outputs=[],
        steps=[PlanStep(
            step_id=1, description="Inspect the current project information",
            allowed_tools=[tool], input_refs=[], output_refs=[],
            risk_level="low", requires_review=False,
        )],
        tool_calls=[ToolCallPlan(step_id=1, tool_name=tool, arguments={})],
    )

    report = PlanValidator().validate(plan)

    assert report.is_valid is True
    assert report.issues == []


def test_validator_rejects_a_step_without_an_explicit_tool_call():
    report = PlanValidator().validate(_plan(risk_level="low", tool_calls=[]))

    assert report.is_valid is False
    assert any(issue.code == "missing_tool_call" for issue in report.issues)


def test_validator_rejects_references_missing_from_the_plan():
    plan = _plan(
        steps=[
            PlanStep(
                step_id=1,
                description="Run a processing algorithm",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["execute_processing"],
                input_refs=["not_declared"],
                output_refs=["output"],
                risk_level="low",
                requires_review=False,
            )
        ]
    )

    report = PlanValidator().validate(plan)

    assert report.is_valid is False
    assert any(issue.code == "unknown_input_reference" for issue in report.issues)


def test_validator_flags_missing_input_path_or_source():
    report = PlanValidator().validate(
        _plan(required_inputs=[InputSpec(input_id="input", description="Input layer")])
    )

    assert report.is_valid is False
    assert {issue.code for issue in report.issues} == {"missing_input_path", "call_input_reference_mismatch"}


def test_validator_flags_missing_expected_output():
    report = PlanValidator().validate(_plan(expected_outputs=[]))

    assert report.is_valid is False
    assert any(issue.code == "missing_output" for issue in report.issues)


def test_validator_accepts_display_only_load_plan_without_file_output():
    report = PlanValidator().validate(
        _plan(
            expected_outputs=[],
            steps=[
                PlanStep(
                    step_id=1,
                    description="Load phase11_points layer into QGIS",
                    candidate_gdal_apis=[],
                    candidate_pyqgis_apis=[],
                    allowed_tools=["add_layer"],
                    input_refs=["input"],
                    output_refs=[],
                    risk_level="low",
                    requires_review=False,
                )
            ],
        tool_calls=[
            ToolCallPlan(
                step_id=1,
                tool_name="add_layer",
                arguments={"path": "data/input.gpkg", "layer_type": "vector"},
            )
        ],
        )
    )

    assert report.is_valid is True
    assert report.issues == []


def test_validator_flags_coarse_api_reference():
    plan = _plan(
        steps=[
            PlanStep(
                step_id=1,
                description="Load layer",
                candidate_gdal_apis=["gdal"],
                candidate_pyqgis_apis=["QgsVectorLayer"],
                allowed_tools=["execute_processing"],
                input_refs=["input"],
                output_refs=["output"],
                risk_level="low",
                requires_review=False,
            )
        ],
    )

    report = PlanValidator().validate(plan)

    assert report.is_valid is True
    coarse_issue = next(
        issue for issue in report.issues if issue.code == "coarse_api_reference"
    )
    assert coarse_issue.severity == "warning"


def test_validator_requires_explicit_review_for_high_risk_step_without_mutating_plan():
    plan = _plan(
        steps=[
            PlanStep(
                step_id=1,
                description="Overwrite output layer and save project",
                candidate_gdal_apis=["gdal.Warp"],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=["input"],
                output_refs=["output"],
                risk_level="high",
                requires_review=False,
            )
        ],
        tool_calls=[
            ToolCallPlan(
                step_id=1,
                tool_name="add_layer",
                arguments={"path": "data/input.gpkg", "layer_type": "vector"},
                risk_level="high",
                requires_review=True,
            )
        ],
    )

    report = PlanValidator().validate(plan)

    assert report.requires_review is True
    assert plan.steps[0].requires_review is False
    assert report.is_valid is False
    assert any(issue.code == "requires_review_missing" for issue in report.issues)


def test_bound_processing_chain_rejects_reverse_tool_call_order(tmp_path, monkeypatch):
    from agent.nodes.planner_node import bind_uploaded_files

    monkeypatch.chdir(tmp_path)
    source = str(tmp_path / 'source.geojson')
    first = str(tmp_path / 'shared/outputs/task-validate/r1/o1/first.gpkg')
    second = str(tmp_path / 'shared/outputs/task-validate/r1/o2/second.gpkg')
    plan = _plan(
        required_inputs=[InputSpec(input_id='input', description='source', path=source)],
        expected_outputs=[OutputSpec(output_id='o1', description='reprojected', path=first),
                          OutputSpec(output_id='o2', description='buffered', path=second)],
        steps=[PlanStep(step_id=1, description='Reproject', allowed_tools=['execute_processing'],
                        input_refs=['input'], output_refs=['o1'], risk_level='low', requires_review=False),
               PlanStep(step_id=2, description='Buffer', allowed_tools=['execute_processing'],
                        input_refs=['o1'], output_refs=['o2'], risk_level='low', requires_review=False)],
        tool_calls=[ToolCallPlan(step_id=1, tool_name='execute_processing', arguments={
                        'algorithm': 'native:reprojectlayer', 'parameters': {'INPUT': source, 'OUTPUT': first}}),
                    ToolCallPlan(step_id=2, tool_name='execute_processing', arguments={
                        'algorithm': 'native:buffer', 'parameters': {'INPUT': first, 'OUTPUT': second}})],
    )
    bound = bind_uploaded_files(plan, [{'file_id': 'source-id', 'filename': 'source.geojson',
                                     'qgis_path': source, 'layer_name': 'source'}])
    assert PlanValidator().validate(bound).is_valid
    reversed_plan = bound.model_copy(update={'tool_calls': list(reversed(bound.tool_calls))})
    report = PlanValidator().validate(reversed_plan)
    assert not report.is_valid
    assert 'unordered_tool_calls' in {issue.code for issue in report.issues}


def test_outputs_cannot_belong_to_a_different_task_or_revision():
    plan = _plan()
    assert PlanValidator().validate(plan).is_valid
    for update in ({'task_id': 'different-task'}, {'revision': 2}):
        report = PlanValidator().validate(plan.model_copy(update=update))
        assert not report.is_valid
        assert 'unsafe_output_path' in {issue.code for issue in report.issues}


def test_write_destination_must_match_the_reviewed_output_reference():
    plan = _plan()
    call = plan.tool_calls[0].model_copy(update={'arguments': {
        'algorithm': 'native:buffer', 'parameters': {'INPUT': 'data/input.gpkg',
        'OUTPUT': 'shared/outputs/task-validate/r1/other/other.gpkg'}}})
    report = PlanValidator().validate(plan.model_copy(update={'tool_calls': [call]}))
    assert not report.is_valid
    assert 'output_reference_mismatch' in {issue.code for issue in report.issues}


def test_verified_completed_prefix_may_keep_its_existing_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plan = _plan(risk_level="low")
    artifact = tmp_path / plan.expected_outputs[0].path
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"completed file")

    assert not PlanValidator().validate(plan).is_valid
    report = PlanValidator().validate(plan, completed_step_ids=frozenset({1}))

    assert report.is_valid, report.summary
    assert artifact.read_bytes() == b"completed file"


def test_completed_prefix_does_not_allow_existing_future_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = "shared/outputs/task-validate/r1/first/first.png"
    second = "shared/outputs/task-validate/r1/second/second.png"
    plan = _plan(
        required_inputs=[],
        expected_outputs=[OutputSpec(output_id="first", description="First", path=first),
                          OutputSpec(output_id="second", description="Second", path=second)],
        steps=[PlanStep(step_id=i, description="Capture", allowed_tools=["capture_map_canvas"],
                        output_refs=[ref], risk_level="low", requires_review=False)
               for i, ref in [(1, "first"), (2, "second")]],
        tool_calls=[ToolCallPlan(step_id=i, tool_name="capture_map_canvas", arguments={"path": path})
                    for i, path in [(1, first), (2, second)]],
    )
    for path in [first, second]:
        artifact = tmp_path / path
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"existing")

    report = PlanValidator().validate(plan, completed_step_ids=frozenset({1}))

    assert not report.is_valid
    assert {issue.code for issue in report.issues} == {"unsafe_output_path"}


@pytest.mark.parametrize("completed", [frozenset({2}), frozenset({99})])
def test_nonprefix_completed_steps_cannot_relax_output_safety(completed):
    report = PlanValidator().validate(_plan(), completed_step_ids=completed)

    assert not report.is_valid
    assert "invalid_completed_prefix" in {issue.code for issue in report.issues}


def test_completed_output_must_still_be_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    report = PlanValidator().validate(_plan(), completed_step_ids=frozenset({1}))

    assert not report.is_valid
    assert "unsafe_output_path" in {issue.code for issue in report.issues}


def test_two_future_artifacts_cannot_reserve_the_same_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = "shared/outputs/task-validate/r1/shared/first.png"
    second = "shared/outputs/task-validate/r1/shared/second.png"
    plan = _plan(
        required_inputs=[],
        expected_outputs=[OutputSpec(output_id="first", description="First", path=first),
                          OutputSpec(output_id="second", description="Second", path=second)],
        steps=[PlanStep(step_id=i, description="Capture", allowed_tools=["capture_map_canvas"],
                        output_refs=[ref], risk_level="low", requires_review=False)
               for i, ref in [(1, "first"), (2, "second")]],
        tool_calls=[ToolCallPlan(step_id=i, tool_name="capture_map_canvas", arguments={"path": path})
                    for i, path in [(1, first), (2, second)]],
    )

    report = PlanValidator().validate(plan)

    assert not report.is_valid
    assert "duplicate_output_directory" in {issue.code for issue in report.issues}
