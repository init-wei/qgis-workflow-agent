import pytest

from agent.adapters.geotool_schema_adapter import (
    geotool_task_plan_to_qgis_legacy_plan,
    geotool_tool_call_plan_to_qgis_calls,
    qgis_execution_trace_to_geotool_dict,
    qgis_task_plan_to_geotool_dict,
)
from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan
from agent.tools.tool_contracts import ExecutionTrace, ToolCallPlan


def _task_plan() -> TaskPlan:
    return TaskPlan(
        task_id="task-geo-1",
        task_summary="加载图层并截图",
        risk_level="low",
        review_status="approved",
        required_inputs=[
            InputSpec(
                input_id="input_layer",
                description="输入点图层",
                path="shared/uploads/points.geojson",
            )
        ],
        expected_outputs=[
            OutputSpec(
                output_id="screenshot",
                description="地图截图",
                path="shared/outputs/points.png",
            )
        ],
        steps=[
            PlanStep(
                step_id=1,
                description="加载点图层",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=["QgsVectorLayer"],
                allowed_tools=["add_layer"],
                input_refs=["input_layer"],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        ],
        metadata={},
    )


def test_qgis_task_plan_exports_geotool_compatible_dict():
    payload = qgis_task_plan_to_geotool_dict(_task_plan(), user_query="加载图层")

    assert payload["task_id"] == "task-geo-1"
    assert payload["user_query"] == "加载图层"
    assert payload["goal"] == "加载图层并截图"
    assert payload["steps"][0]["candidate_tools"] == ["add_layer"]
    assert payload["steps"][0]["metadata"]["candidate_pyqgis_apis"] == [
        "QgsVectorLayer"
    ]


def test_qgis_task_plan_export_rejects_empty_steps():
    task_plan = _task_plan().model_copy(update={"steps": []})

    with pytest.raises(ValueError, match="steps"):
        qgis_task_plan_to_geotool_dict(task_plan, user_query="加载图层")


def test_geotool_task_plan_imports_legacy_plan():
    legacy = geotool_task_plan_to_qgis_legacy_plan(
        {
            "goal": "加载图层",
            "steps": [
                {
                    "step_id": 1,
                    "description": "加载点图层",
                    "metadata": {
                        "candidate_gdal_apis": [],
                        "candidate_pyqgis_apis": ["QgsVectorLayer"],
                    },
                }
            ],
        }
    )

    assert legacy.task == "加载图层"
    assert legacy.steps[0].step_id == 1
    assert legacy.steps[0].pyqgis_api == ["QgsVectorLayer"]


def test_geotool_tool_call_plan_imports_qgis_calls():
    calls = geotool_tool_call_plan_to_qgis_calls(
        {
            "tool_calls": [
                {
                    "call_id": "call-1",
                    "step_id": 1,
                    "tool": "add_layer",
                    "args": {"path": "shared/uploads/points.geojson"},
                    "risk_level": "low",
                    "requires_review": False,
                    "depends_on": [],
                    "output_ref": "loaded_layer",
                }
            ]
        }
    )

    assert len(calls) == 1
    assert calls[0].tool_name == "add_layer"
    assert calls[0].arguments["path"] == "shared/uploads/points.geojson"
    assert calls[0].metadata["call_id"] == "call-1"
    assert calls[0].metadata["output_ref"] == "loaded_layer"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tool_calls": [{"step_id": 1, "args": {}}]},
        {"tool_calls": [{"step_id": "1", "tool": "add_layer", "args": {}}]},
    ],
)
def test_geotool_tool_call_plan_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        geotool_tool_call_plan_to_qgis_calls(payload)


def test_qgis_execution_trace_exports_geotool_dict():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-geo-1")
    call = ToolCallPlan(
        step_id=1,
        tool_name="add_layer",
        arguments={"path": "shared/uploads/points.geojson"},
    )
    trace.metadata["task_plan"] = qgis_task_plan_to_geotool_dict(
        _task_plan(), user_query="加载图层"
    )
    trace.metadata["tool_call_plan"] = [call.model_dump(mode="json")]

    payload = qgis_execution_trace_to_geotool_dict(trace, user_query="加载图层")

    assert payload["trace_id"] == "trace-1"
    assert payload["task_id"] == "task-geo-1"
    assert payload["user_query"] == "加载图层"
    assert payload["tool_call_plan"][0]["tool_name"] == "add_layer"
    assert payload["metadata"]["source_project"] == "qgis-workflow-agent"
