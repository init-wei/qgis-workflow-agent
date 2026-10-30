from agent.planning.plan_schema import PlanStep, TaskPlan, ToolCallPlan
from agent.tools.tool_call_planner import compile_task_plan_to_tool_calls


def _plan(tool_calls: list[ToolCallPlan]) -> TaskPlan:
    return TaskPlan(
        task_id="task-explicit-calls",
        task_summary="Load a selected layer",
        risk_level="low",
        review_status="approved",
        required_inputs=[],
        expected_outputs=[],
        steps=[
            PlanStep(
                step_id=1,
                description="Load a selected vector layer",
                allowed_tools=["add_layer"],
                input_refs=[],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        tool_calls=tool_calls,
    )


def test_compiler_preserves_the_reviewed_call_and_arguments():
    reviewed_call = ToolCallPlan(
        step_id=1,
        tool_name="add_layer",
        arguments={
            "path": "shared/uploads/selected.geojson",
            "layer_type": "vector",
            "name": "selected",
        },
    )

    calls = compile_task_plan_to_tool_calls(_plan([reviewed_call]))

    assert calls == [reviewed_call]
    assert calls[0].arguments == {
        "path": "shared/uploads/selected.geojson",
        "layer_type": "vector",
        "name": "selected",
    }


def test_compiler_does_not_infer_calls_from_step_descriptions():
    calls = compile_task_plan_to_tool_calls(_plan([]))

    assert calls == []
