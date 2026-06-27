import pytest

from agent.planning.plan_parser import PlanParseError, parse_task_plan_json


VALID_PLAN = {
    "task_id": "task-parse",
    "task_summary": "Clip a raster by a polygon layer",
    "risk_level": "medium",
    "review_status": "draft",
    "required_inputs": [
        {"input_id": "raster", "description": "Input raster", "path": "data/input.tif"}
    ],
    "expected_outputs": [
        {"output_id": "clip", "description": "Clipped raster", "path": "data/clip.tif"}
    ],
    "steps": [
        {
            "step_id": 1,
            "description": "Clip raster with mask layer",
            "candidate_gdal_apis": ["gdal.Warp"],
            "candidate_pyqgis_apis": [],
            "allowed_tools": ["gdal"],
            "input_refs": ["raster"],
            "output_refs": ["clip"],
            "risk_level": "medium",
            "requires_review": False,
        }
    ],
    "metadata": {"source": "test"},
}


def test_parse_task_plan_from_dict():
    plan = parse_task_plan_json(VALID_PLAN)

    assert plan.task_id == "task-parse"
    assert plan.steps[0].candidate_gdal_apis == ["gdal.Warp"]


def test_parse_task_plan_from_json_string():
    raw = """
    {
      "task_id": "task-json",
      "task_summary": "Load project layers",
      "risk_level": "low",
      "review_status": "draft",
      "steps": []
    }
    """

    plan = parse_task_plan_json(raw)

    assert plan.task_id == "task-json"


def test_parse_task_plan_from_fenced_json_block():
    raw = f"```json\n{__import__('json').dumps(VALID_PLAN)}\n```"

    plan = parse_task_plan_json(raw)

    assert plan.task_summary == VALID_PLAN["task_summary"]


def test_parse_task_plan_rejects_missing_required_field():
    invalid = VALID_PLAN.copy()
    invalid.pop("task_summary")

    with pytest.raises(PlanParseError):
        parse_task_plan_json(invalid)


def test_parse_task_plan_rejects_missing_steps():
    invalid = VALID_PLAN.copy()
    invalid.pop("steps")

    with pytest.raises(PlanParseError):
        parse_task_plan_json(invalid)


def test_parse_task_plan_rejects_invalid_enum():
    invalid = VALID_PLAN.copy()
    invalid["review_status"] = "needs-human"

    with pytest.raises(PlanParseError):
        parse_task_plan_json(invalid)
