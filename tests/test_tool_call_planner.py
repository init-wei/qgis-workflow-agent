from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan
from agent.tools.tool_call_planner import (
    ToolCallPlannerInput,
    compile_task_plan_to_tool_calls,
    plan_tool_calls,
)


def test_compile_task_plan_uses_explicit_metadata_tool_calls():
    task_plan = TaskPlan(
        task_id="task-buffer",
        task_summary="Buffer roads",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[
            OutputSpec(
                output_id="buffer_output",
                description="Buffered roads",
                path="shared/outputs/roads_buffer.gpkg",
            )
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="Run native buffer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=["processing.run"],
                allowed_tools=["execute_processing"],
                input_refs=[],
                output_refs=["buffer_output"],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={
            "tool_calls": [
                {
                    "step_id": 1,
                    "tool_name": "execute_processing",
                    "arguments": {
                        "algorithm": "native:buffer",
                        "parameters": {
                            "INPUT": "shared/uploads/roads.geojson",
                            "DISTANCE": 100,
                            "SEGMENTS": 5,
                            "END_CAP_STYLE": 0,
                            "JOIN_STYLE": 0,
                            "MITER_LIMIT": 2,
                            "DISSOLVE": False,
                            "OUTPUT": "shared/outputs/roads_buffer.gpkg",
                        },
                    },
                }
            ]
        },
    )

    calls = compile_task_plan_to_tool_calls(task_plan)

    assert len(calls) == 1
    assert calls[0].step_id == 1
    assert calls[0].tool_name == "execute_processing"
    assert calls[0].arguments["algorithm"] == "native:buffer"
    assert calls[0].arguments["parameters"]["OUTPUT"] == "shared/outputs/roads_buffer.gpkg"
    assert calls[0].metadata["task_plan_id"] == "task-buffer"
    assert calls[0].metadata["candidate_pyqgis_apis"] == ["processing.run"]


def test_compile_task_plan_adds_output_load_when_requested():
    task_plan = TaskPlan(
        task_id="task-load-output",
        task_summary="Load output",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[
            OutputSpec(
                output_id="out",
                description="Output layer",
                path="shared/outputs/result.gpkg",
            )
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="Load generated output layer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=["out"],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={},
    )

    calls = compile_task_plan_to_tool_calls(task_plan)

    assert len(calls) == 1
    assert calls[0].tool_name == "add_layer"
    assert calls[0].arguments == {
        "path": "shared/outputs/result.gpkg",
        "layer_type": "vector",
    }


def test_compile_task_plan_rejects_metadata_call_for_unreviewed_step():
    task_plan = TaskPlan(
        task_id="task-smuggled-call",
        task_summary="Reviewed one safe step",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[],
        steps=[
            PlanStep(
                step_id=1,
                description="Reviewed safe operation",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={
            "tool_calls": [
                {
                    "step_id": 99,
                    "tool_name": "execute_processing",
                    "arguments": {
                        "algorithm": "native:buffer",
                        "parameters": {"OUTPUT": "shared/outputs/smuggled.gpkg"},
                    },
                }
            ]
        },
    )

    calls = compile_task_plan_to_tool_calls(task_plan)

    assert calls == []


def test_compile_task_plan_normalizes_add_layer_metadata_arguments():
    task_plan = TaskPlan(
        task_id="task-normalize-add-layer",
        task_summary="Load layer",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[],
        steps=[
            PlanStep(
                step_id=1,
                description="Load vector layer",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={
            "tool_calls": [
                {
                    "step_id": 1,
                    "tool_name": "add_layer",
                    "arguments": {
                        "uri": "shared/uploads/phase11_points.geojson",
                        "layer_name": "phase11_points",
                    },
                }
            ]
        },
    )

    calls = compile_task_plan_to_tool_calls(task_plan)

    assert calls[0].arguments == {
        "path": "shared/uploads/phase11_points.geojson",
        "name": "phase11_points",
        "layer_type": "vector",
    }


def test_rule_based_planner_generates_load_layer_call_from_input_ref():
    task_plan = TaskPlan(
        task_id="task-load",
        task_summary="Load layer",
        risk_level="low",
        review_status="approved",
        required_inputs=[
            InputSpec(
                input_id="points",
                description="Point layer",
                path="shared/uploads/points.geojson",
            )
        ],
        expected_outputs=[],
        steps=[
            PlanStep(
                step_id=1,
                description="加载图层",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=["points"],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={},
    )

    calls = plan_tool_calls(
        ToolCallPlannerInput(
            task_plan=task_plan,
            step_id=1,
            available_tools=["add_layer"],
            api_context=[],
            workspace_state={},
            previous_observation=None,
        )
    )

    assert len(calls) == 1
    assert calls[0].tool_name == "add_layer"
    assert calls[0].arguments["path"] == "shared/uploads/points.geojson"
    assert calls[0].requires_review is False


def test_rule_based_planner_marks_insufficient_arguments_for_review():
    task_plan = TaskPlan(
        task_id="task-unknown",
        task_summary="Run one step",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[],
        steps=[
            PlanStep(
                step_id=1,
                description="运行一个需要参数的工具",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={},
    )

    calls = plan_tool_calls(
        ToolCallPlannerInput(
            task_plan=task_plan,
            step_id=1,
            available_tools=["add_layer"],
            api_context=[],
            workspace_state={},
            previous_observation=None,
        )
    )

    assert len(calls) == 1
    assert calls[0].requires_review is True
    assert calls[0].metadata["planner_error"] == "insufficient_arguments"
