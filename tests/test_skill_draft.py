from datetime import UTC, datetime

import pytest

from agent.tools.skill_draft import SkillExtractionError, SkillExtractor
from agent.tools.tool_contracts import (
    ExecutionTrace,
    RepairAction,
    StepAttempt,
    ToolCallPlan,
    ToolObservation,
)


def _attempt(
    *,
    attempt_id: str = "attempt-1",
    status: str = "success",
    tool_name: str = "execute_processing",
    arguments: dict | None = None,
    observation: ToolObservation | None = None,
    metadata: dict | None = None,
) -> StepAttempt:
    started_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    finished_at = datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC)
    if arguments is None:
        arguments = {
            "algorithm": "native:buffer",
            "parameters": {
                "INPUT": "D:/project_files/QGIS-Agent-Refactoring/shared/data/roads.gpkg",
                "OUTPUT": "D:/project_files/QGIS-Agent-Refactoring/shared/output/buffered_roads.gpkg",
                "DISTANCE": 10,
                "layer_name": "roads",
            },
        }
    if observation is None:
        observation = ToolObservation(
            tool_name=tool_name,
            status="success",
            message="Created buffered layer",
            data={"output": arguments["parameters"]["OUTPUT"]},
            artifacts=[
                {
                    "kind": "path",
                    "path": arguments["parameters"]["OUTPUT"],
                    "description": "Buffered roads layer",
                }
            ],
            raw_response={"status": "success"},
        )
    return StepAttempt(
        attempt_id=attempt_id,
        step_id=1,
        tool_call=ToolCallPlan(
            step_id=1,
            tool_name=tool_name,
            arguments=arguments,
            risk_level="medium",
            metadata={
                "description": "Buffer the roads layer",
                "candidate_gdal_apis": ["gdal.VectorTranslate"],
                "candidate_pyqgis_apis": ["processing.run"],
            },
        ),
        observation=observation,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=1000,
        artifacts=observation.artifacts,
        metadata=metadata or {},
    )


def test_extracts_unregistered_draft_from_complete_successful_trace():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    trace.append_attempt(
        _attempt(
            metadata={
                "retrieval_context": {
                    "query": "processing.run native:buffer INPUT OUTPUT",
                    "records": [
                        {
                            "api_name": "processing.run",
                            "source": "pyqgis",
                            "summary": "Runs a processing algorithm.",
                        }
                    ],
                }
            }
        )
    )

    draft = SkillExtractor().extract(trace)

    assert draft.source_trace_id == "trace-1"
    assert draft.source_task_id == "task-1"
    assert draft.review_status == "draft"
    assert draft.is_registered is False
    assert draft.steps[0].tool_name == "execute_processing"
    assert draft.steps[0].evidence["retrieval_context"]["query"].startswith(
        "processing.run"
    )
    assert "Created buffered layer" in draft.success_criteria


def test_generalizes_paths_outputs_and_layer_names_in_arguments_template():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    original_input = "D:/project_files/QGIS-Agent-Refactoring/shared/data/roads.gpkg"
    original_output = (
        "D:/project_files/QGIS-Agent-Refactoring/shared/output/buffered_roads.gpkg"
    )
    trace.append_attempt(_attempt())

    draft = SkillExtractor().extract(trace)

    template = draft.steps[0].arguments_template
    assert template["parameters"]["INPUT"] == "${input_path_1}"
    assert template["parameters"]["OUTPUT"] == "${output_path_1}"
    assert template["parameters"]["layer_name"] == "${layer_name_1}"
    assert draft.steps[0].artifacts_template[0]["path"] == "${artifact_path_1}"
    assert original_input not in str(template)
    assert original_output not in str(template)
    assert {"input_path_1", "output_path_1", "layer_name_1", "artifact_path_1"} <= {
        variable.name for variable in draft.variables
    }


def test_does_not_generalize_layer_type_literals():
    trace = ExecutionTrace(trace_id="trace-layer-type", task_id="task-layer-type")
    trace.append_attempt(
        _attempt(
            tool_name="add_layer",
            arguments={
                "path": "shared/uploads/phase11_points.geojson",
                "name": "phase11_points",
                "layer_type": "vector",
            },
            observation=ToolObservation(
                tool_name="add_layer",
                status="success",
                message="Layer loaded",
                raw_response={"status": "success"},
            ),
        )
    )

    draft = SkillExtractor().extract(trace)

    assert draft.steps[0].arguments_template["layer_type"] == "vector"


def test_extracts_repair_strategy_from_failed_attempt_before_success():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    failed_observation = ToolObservation(
        tool_name="execute_processing",
        status="error",
        error_type="parameter_error",
        message="Missing DISTANCE parameter",
        raw_response={"status": "error"},
    )
    trace.append_attempt(
        _attempt(
            attempt_id="attempt-1",
            status="failed",
            observation=failed_observation,
            metadata={
                "evaluation": {
                    "outcome": "retry",
                    "reason": "parameter_error",
                    "error_type": "parameter_error",
                },
                "repair_action": RepairAction(
                    action_type="update_arguments",
                    reason="metadata_repair_arguments",
                    updated_arguments={"parameters": {"DISTANCE": 10}},
                ).model_dump(mode="json"),
                "repair_retrieval_context": {
                    "query": "execute_processing parameter_error DISTANCE",
                    "records": [{"api_name": "processing.run"}],
                },
            },
        )
    )
    trace.append_attempt(_attempt(attempt_id="attempt-2"))

    draft = SkillExtractor().extract(trace)

    assert draft.repair_strategies
    assert draft.repair_strategies[0]["error_type"] == "parameter_error"
    assert draft.repair_strategies[0]["action_type"] == "update_arguments"
    assert draft.repair_strategies[0]["retrieval_context"]["query"].startswith(
        "execute_processing"
    )


def test_excludes_failed_attempts_from_reusable_steps_after_repair():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    failed_observation = ToolObservation(
        tool_name="execute_processing",
        status="error",
        error_type="parameter_error",
        message="Missing DISTANCE parameter",
    )
    bad_arguments = {
        "algorithm": "native:buffer",
        "parameters": {
            "INPUT": "D:/project_files/QGIS-Agent-Refactoring/shared/data/roads.gpkg",
            "OUTPUT": "D:/project_files/QGIS-Agent-Refactoring/shared/output/bad.gpkg",
            "layer_name": "roads",
        },
    }
    trace.append_attempt(
        _attempt(
            attempt_id="attempt-1",
            status="failed",
            arguments=bad_arguments,
            observation=failed_observation,
        )
    )
    trace.append_attempt(_attempt(attempt_id="attempt-2"))

    draft = SkillExtractor().extract(trace)

    assert [step.metadata["attempt_id"] for step in draft.steps] == ["attempt-2"]
    assert "bad.gpkg" not in str(draft.steps)


@pytest.mark.parametrize(
    "trace",
    [
        ExecutionTrace(trace_id="empty", task_id="task-1"),
        ExecutionTrace(trace_id="running", task_id="task-1"),
    ],
)
def test_rejects_empty_or_incomplete_trace(trace):
    with pytest.raises(SkillExtractionError):
        SkillExtractor().extract(trace)


def test_rejects_trace_when_latest_attempt_is_not_successful():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    observation = ToolObservation(
        tool_name="execute_processing",
        status="error",
        error_type="execution_error",
        message="Processing failed",
    )
    trace.append_attempt(
        _attempt(status="failed", observation=observation, metadata={"source": "test"})
    )

    with pytest.raises(SkillExtractionError):
        SkillExtractor().extract(trace)


def test_success_criteria_do_not_carry_concrete_artifact_paths():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    original_output = (
        "D:/project_files/QGIS-Agent-Refactoring/shared/output/buffered_roads.gpkg"
    )
    trace.append_attempt(_attempt())

    draft = SkillExtractor().extract(trace)

    assert original_output not in draft.success_criteria
    assert f"artifact_created:{original_output}" not in draft.success_criteria
    assert "artifact_created:${artifact_path_1}" in draft.success_criteria
