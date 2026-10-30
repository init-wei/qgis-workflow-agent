import pytest

from agent.adapters.geotool_schema_adapter import (
    geotool_tool_call_plan_to_qgis_calls,
    qgis_execution_trace_to_geotool_dict,
    qgis_task_plan_to_geotool_dict,
    qgis_tool_calls_to_geotool_plan,
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
                file_id="file-points",
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
            ),
            PlanStep(
                step_id=2,
                description="截图当前地图",
                candidate_gdal_apis=[],
                candidate_pyqgis_apis=[],
                allowed_tools=["capture_map_canvas"],
                input_refs=[],
                output_refs=["screenshot"],
                risk_level="low",
                requires_review=False,
            )
        ],
        tool_calls=[
            ToolCallPlan(
                step_id=1,
                tool_name="add_layer",
                arguments={
                    "path": "shared/uploads/points.geojson",
                    "layer_type": "vector",
                    "name": "points",
                },
            ),
            ToolCallPlan(
                step_id=2,
                tool_name="capture_map_canvas",
                arguments={"path": "shared/outputs/points.png", "width": 1200, "height": 800},
            ),
        ],
        metadata={},
    )


def test_qgis_task_plan_exports_geotool_compatible_dict():
    payload = qgis_task_plan_to_geotool_dict(_task_plan(), user_query="加载图层")

    assert payload["task_id"] == "task-geo-1"
    assert payload["user_query"] == "加载图层"
    assert payload["goal"] == "加载图层并截图"
    assert "review_status" not in payload
    assert payload["metadata"]["qgis_review_status"] == "approved"
    assert payload["steps"][0]["step_id"] == "1"
    assert payload["steps"][0]["intent"] == "qgis_operation"
    assert "step_type" not in payload["steps"][0]
    assert payload["steps"][0]["candidate_tools"] == ["add_layer"]
    assert payload["steps"][0]["metadata"]["candidate_pyqgis_apis"] == [
        "QgsVectorLayer"
    ]
    assert payload["tool_calls"][0]["tool_name"] == "add_layer"
    assert payload["tool_calls"][0]["arguments"]["path"] == "shared/uploads/points.geojson"


def test_qgis_task_plan_export_rejects_empty_steps():
    task_plan = _task_plan().model_copy(update={"steps": []})

    with pytest.raises(ValueError, match="steps"):
        qgis_task_plan_to_geotool_dict(task_plan, user_query="加载图层")


def test_geotool_tool_call_plan_imports_canonical_calls():
    calls = geotool_tool_call_plan_to_qgis_calls(
        {
            "metadata": {"schema_version": "geotool-interop-v0.1"},
            "calls": [
                {
                    "call_id": "call-1",
                    "step_id": "1",
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


def test_geotool_tool_call_plan_rejects_wrong_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        geotool_tool_call_plan_to_qgis_calls(
            {
                "metadata": {"schema_version": "geopotential-v0.2"},
                "calls": [
                    {
                        "call_id": "call-1",
                        "step_id": "1",
                        "tool": "add_layer",
                        "args": {"path": "shared/uploads/points.geojson"},
                    }
                ],
            }
        )


def test_geotool_tool_call_plan_rejects_string_requires_review():
    with pytest.raises(ValueError, match="requires_review"):
        geotool_tool_call_plan_to_qgis_calls(
            {
                "metadata": {"schema_version": "geotool-interop-v0.1"},
                "calls": [
                    {
                        "call_id": "call-1",
                        "step_id": "1",
                        "tool": "add_layer",
                        "args": {"path": "shared/uploads/points.geojson"},
                        "requires_review": "False",
                    }
                ],
            }
        )


def test_geotool_tool_call_plan_still_imports_legacy_tool_calls():
    calls = geotool_tool_call_plan_to_qgis_calls(
        {
            "tool_calls": [
                {
                    "call_id": "legacy-call-1",
                    "step_id": 1,
                    "tool": "add_layer",
                    "args": {"path": "shared/uploads/points.geojson"},
                }
            ]
        }
    )

    assert calls[0].step_id == 1
    assert calls[0].metadata["call_id"] == "legacy-call-1"


def test_qgis_tool_calls_export_canonical_geotool_plan():
    call = ToolCallPlan(
        step_id=1,
        tool_name="add_layer",
        arguments={"path": "shared/uploads/points.geojson"},
        metadata={"call_id": "call-1", "output_ref": "loaded_layer"},
    )

    payload = qgis_tool_calls_to_geotool_plan("task-geo-1", [call])

    assert payload == {
        "task_id": "task-geo-1",
        "calls": [
            {
                "call_id": "call-1",
                "step_id": "1",
                "tool": "add_layer",
                "args": {"path": "shared/uploads/points.geojson"},
                "output_ref": "loaded_layer",
                "depends_on": [],
                "risk_level": "low",
                "requires_review": False,
                "metadata": {},
            }
        ],
        "metadata": {"schema_version": "geotool-interop-v0.1"},
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"calls": [{"step_id": 1, "args": {}}]},
        {"calls": [{"step_id": "not-an-int", "tool": "add_layer", "args": {}}]},
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
    assert payload["tool_call_plan"]["calls"][0]["tool"] == "add_layer"
    assert payload["metadata"]["source_project"] == "qgis-workflow-agent"
