"""Public spatial-validation seam; the GIS inspector is replaceable in offline CI."""

import asyncio

import pytest

from agent.planning.plan_schema import TaskPlan, ToolCallPlan
from agent.spatial_validation import preflight_spatial_plan, validate_spatial_step


def empty_plan():
    return TaskPlan(task_id="spatial", task_summary="Spatial checks", steps=[],
                    risk_level="low", review_status="approved", tool_calls=[
                        ToolCallPlan(step_id=1, tool_name="execute_processing", arguments={
                            "algorithm": "native:reprojectlayer", "parameters": {},
                        }),
                    ])


def test_preflight_refuses_a_gis_inspector_failure():
    def inspector(request):
        assert request["mode"] == "preflight"
        return {"status": "failed", "checks": [], "errors": ["CRS unavailable"], "facts": {}}

    with pytest.raises(ValueError, match="CRS unavailable"):
        asyncio.run(preflight_spatial_plan(empty_plan(), inspector=inspector))


def test_step_returns_passed_evidence_from_gis_inspector():
    call = ToolCallPlan(step_id=1, tool_name="execute_processing", arguments={
        "algorithm": "native:reprojectlayer", "parameters": {},
    })
    def inspector(request):
        assert request["input_paths"] == {"source": "/controlled/source.gpkg"}
        assert request["output_paths"] == {"projected": "/controlled/result.gpkg"}
        return {"status": "passed", "checks": ["coordinates"], "errors": [],
                "facts": {"algorithm": "native:reprojectlayer"}}

    report = asyncio.run(validate_spatial_step(call, input_paths={"source": "/controlled/source.gpkg"},
                                               output_paths={"projected": "/controlled/result.gpkg"},
                                               inspector=inspector))

    assert report["status"] == "passed"
    assert report["facts"]["algorithm"] == "native:reprojectlayer"


def test_nonprocessing_plan_does_not_need_a_gis_inspector():
    plan = empty_plan()
    plan.tool_calls = []

    def unavailable(_request):
        raise AssertionError("GIS should not be consulted")

    report = asyncio.run(preflight_spatial_plan(plan, inspector=unavailable))
    assert report == {"status": "passed", "checks": [], "errors": [], "facts": {}}


@pytest.mark.parametrize("report", [
    None, {}, {"status": "passed", "checks": [], "errors": ["bad"], "facts": {}},
    {"status": [], "checks": [], "errors": [], "facts": {}},
    {"status": "passed", "checks": [None], "errors": [], "facts": {}},
])
def test_malformed_inspector_evidence_can_never_pass(report):
    with pytest.raises(ValueError, match="invalid report"):
        asyncio.run(preflight_spatial_plan(empty_plan(), inspector=lambda _request: report))

    call = empty_plan().tool_calls[0]
    result = asyncio.run(validate_spatial_step(call, input_paths={"in": "a"}, output_paths={"out": "b"},
                                               inspector=lambda _request: report))
    assert result["status"] == "failed"


def test_preflight_reports_unavailable_inspector_as_value_error():
    def unavailable(_request):
        raise RuntimeError("backend unavailable")

    with pytest.raises(ValueError, match="inspector"):
        asyncio.run(preflight_spatial_plan(empty_plan(), inspector=unavailable))


def test_preflight_passes_direct_producer_facts_only_when_present():
    requests = []

    def inspector(request):
        requests.append(request)
        return {"status": "passed", "checks": [], "errors": [], "facts": {}}

    asyncio.run(preflight_spatial_plan(empty_plan(), inspector=inspector))
    facts = {"area": {"algorithm": "qgis:exportaddgeometrycolumns", "field_units": {"area": "m2"}}}
    asyncio.run(preflight_spatial_plan(empty_plan(), inspector=inspector, input_facts=facts))
    assert "input_facts" not in requests[0]
    assert requests[1]["input_facts"] == facts
