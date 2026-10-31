"""Reviewed-template catalog contracts, exercised without external services."""

from copy import deepcopy

import pytest

from agent.planning.plan_schema import TaskPlan
from agent.tools.skill_registry import (
    SkillTemplateError,
    load_skill_registry,
    render_template,
)


def variable(name, value_type, example):
    return {"name": name, "type": value_type, "source": "example", "example": example}


def files():
    return [
        {
            "file_id": "new-points",
            "filename": "new-points.geojson",
            "qgis_path": "/controlled/new-points.geojson",
            "layer_name": "new-points",
            "layer_info": {"geometry": "Point", "crs": "EPSG:4326", "is_2d": True},
        },
        {
            "file_id": "new-mask",
            "filename": "new-mask.gpkg",
            "qgis_path": "/controlled/new-mask.gpkg|layername=mask",
            "layer_name": "mask",
            "layer_info": {"geometry": "Polygon", "crs": "EPSG:4326", "is_2d": True},
        },
    ]


def test_complete_placeholders_keep_types_and_values_are_copied():
    variables = [variable("distance", "number", 100), variable("flag", "boolean", False),
                 variable("fields", "string_list", []), variable("name", "string", "layer")]
    template = {"nested": [{"distance": "${distance}", "flag": "${flag}",
                            "fields": "${fields}", "label": "图层-${name}"}]}
    bindings = {"distance": 0, "flag": False, "fields": [], "name": "选中"}
    original = deepcopy(template)
    rendered = render_template(template, variables, bindings)
    assert rendered == {"nested": [{"distance": 0, "flag": False, "fields": [], "label": "图层-选中"}]}
    assert type(rendered["nested"][0]["distance"]) is int
    assert type(rendered["nested"][0]["flag"]) is bool
    rendered["nested"][0]["fields"].append("changed")
    assert bindings["fields"] == []
    assert template == original


@pytest.mark.parametrize(("template", "variables", "bindings"), [
    ("${missing}", [variable("missing", "string", "x")], {}),
    ("${unknown}", [], {}),
    ("${bad-name}", [], {}),
    ("${", [], {}),
    ({"${key}": "literal"}, [], {}),
    ("${value}", [variable("value", "string", "x")], {"value": "${again}"}),
    ("${n}", [variable("n", "number", 1)], {"n": True}),
    ("${n}", [variable("n", "number", 1)], {"n": float("nan")}),
    ("${flag}", [variable("flag", "boolean", False)], {"flag": 0}),
    ("prefix-${n}", [variable("n", "number", 1)], {"n": 2}),
    ("fixed", [], {"unknown": "x"}),
])
def test_missing_unknown_malformed_and_wrong_typed_bindings_are_rejected(template, variables, bindings):
    with pytest.raises(SkillTemplateError):
        render_template(template, variables, bindings)


def test_no_variables_is_a_recursive_final_plan_placeholder_guard():
    assert render_template({"numbers": [0, False, 1.5], "literal": "ok"}, [], {}) == {
        "numbers": [0, False, 1.5], "literal": "ok"
    }
    with pytest.raises(SkillTemplateError):
        render_template({"tool_calls": [{"arguments": {"path": "${unbound}"}}]}, [], {})
    with pytest.raises(SkillTemplateError):
        render_template({"tool_calls": [{"arguments": {"${illegal_key}": 1}}]}, [], {})


@pytest.mark.parametrize(("query", "family"), [
    ("加载图层", "load"),
    ("reproject selected layer", "reproject"),
    ("先重投影再缓冲 125 米", "buffer"),
    ("clip the source", "clip"),
    ("按字段融合", "dissolve"),
    ("统计字段值", "statistics"),
])
def test_catalog_candidates_are_real_taskplan_examples_bound_to_selected_files(query, family):
    selected = files()
    original = deepcopy(selected)
    examples = load_skill_registry().matching_examples(query, selected)
    candidate = next(item for item in examples if item["family"] == family)
    assert set(candidate) == {"skill_id", "version", "family", "name", "summary", "review_status", "human_review", "task_plan", "provenance"}
    assert candidate["version"] and candidate["skill_id"]
    assert candidate["review_status"] == "human_reviewed"
    assert candidate["human_review"]["skill_id"] == candidate["skill_id"]
    assert candidate["human_review"]["version"] == candidate["version"]
    assert candidate["human_review"]["evidence"].startswith("https://github.com/init-wei/qgis-workflow-agent/issues/26#issuecomment-")
    assert candidate["provenance"][0]["task_id"]
    assert candidate["provenance"][0]["evidence_url"].startswith("https://github.com/")
    plan = TaskPlan.model_validate(candidate["task_plan"])
    assert plan.review_status == "pending_review"
    assert plan.required_inputs[0].file_id == "new-points"
    assert plan.required_inputs[0].path == "/controlled/new-points.geojson"
    assert plan.required_inputs[0].layer_name == "new-points"
    assert selected == original
    if family == "clip":
        assert plan.required_inputs[1].file_id == "new-mask"
        assert plan.tool_calls[0].arguments["parameters"]["OVERLAY"] == selected[1]["qgis_path"]
    if family == "buffer":
        buffer = next(call for call in plan.tool_calls if call.arguments.get("algorithm") == "native:buffer")
        assert buffer.distance_unit == "m"
        assert type(buffer.arguments["parameters"]["DISTANCE"]) in {int, float}
        assert type(buffer.arguments["parameters"]["DISSOLVE"]) is bool
        assert buffer.arguments["parameters"]["DISSOLVE"] is False


def test_matching_respects_input_conditions_and_does_not_invent_facts():
    registry = load_skill_registry()
    assert registry.matching_examples("请写一句问候", files()) == []
    assert registry.matching_examples("clip", files()[:1]) == []
    selected = files()
    selected[1]["layer_info"] = {"geometry": "Point"}
    assert registry.matching_examples("clip", selected) == []
    selected[1]["layer_info"] = {}
    assert registry.matching_examples("clip", selected) == []
    selected = files()
    selected[0].pop("file_id")
    assert registry.matching_examples("加载", selected) == []
    # Buffer may follow a reprojection: the original need not be in meters.
    assert any(item["family"] == "buffer" for item in registry.matching_examples("缓冲", files()))


def test_cached_catalog_does_not_let_callers_mutate_later_examples():
    registry = load_skill_registry()
    first = registry.matching_examples("加载", files())
    first[0]["task_plan"]["tool_calls"][0]["arguments"]["name"] = "changed"
    first[0]["provenance"][0]["task_id"] = "changed"
    second = registry.matching_examples("加载", files())
    assert second[0]["task_plan"]["tool_calls"][0]["arguments"]["name"] != "changed"
    assert second[0]["provenance"][0]["task_id"] != "changed"


@pytest.mark.parametrize("query", ["加载", "重投影", "缓冲", "裁剪", "融合", "统计"])
def test_all_example_plans_pass_existing_structural_validator_after_output_binding(query, tmp_path, monkeypatch):
    from agent.nodes.planner_node import bind_task_outputs
    from agent.planning.plan_validator import PlanValidator

    monkeypatch.chdir(tmp_path)
    example = load_skill_registry().matching_examples(query, files())[0]
    plan = bind_task_outputs(TaskPlan.model_validate(example["task_plan"]))
    report = PlanValidator().validate(plan)
    assert report.is_valid, [(issue.code, issue.message) for issue in report.issues]


@pytest.mark.parametrize("field", ["file_id", "filename", "qgis_path", "layer_name"])
def test_template_candidate_misses_when_file_text_cannot_be_a_safe_binding(field):
    selected = files()
    selected[0][field] = "map${v}.geojson" if field == "filename" else "valid-${literal}"
    assert load_skill_registry().matching_examples("加载", selected) == []


def test_unsafe_overlay_only_excludes_the_candidate_that_uses_it():
    selected = files()
    selected[1]["filename"] = "mask${v}.gpkg"
    candidates = load_skill_registry().matching_examples("加载并裁剪", selected)
    assert [candidate["family"] for candidate in candidates] == ["load"]


def _pending_skill_data():
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parents[1] / "agent/skills/load.json").read_text())
    # Pending state is a test fixture, independent of current catalog reviews.
    data["review_status"] = "pending_human_review"
    data.pop("human_review", None)
    return data


def _reviewed_skill_data():
    from agent.tools.skill_registry import RegisteredSkill, skill_content_sha256

    data = _pending_skill_data()
    digest = skill_content_sha256(RegisteredSkill.model_validate(data))
    data["review_status"] = "human_reviewed"
    # Synthetic test data only; these values are never written to the catalog.
    data["human_review"] = {
        "reviewer": "test reviewer",
        "reviewed_at": "2026-10-31T12:00:00Z",
        "evidence": "test-review-record",
        "skill_id": data["skill_id"],
        "version": data["version"],
        "content_sha256": digest,
    }
    return data


def test_real_review_shape_can_bind_a_specific_semantic_version_and_is_visible():
    from agent.tools.skill_registry import RegisteredSkill, SkillRegistry, skill_content_sha256

    raw = _reviewed_skill_data()
    reviewed = RegisteredSkill.model_validate(raw)
    assert skill_content_sha256(reviewed) == raw["human_review"]["content_sha256"]
    candidate = SkillRegistry([reviewed]).matching_examples("加载", files())[0]
    assert candidate["review_status"] == "human_reviewed"
    assert candidate["human_review"]["reviewer"] == "test reviewer"
    assert candidate["human_review"]["version"] == raw["version"]
    assert candidate["human_review"]["content_sha256"] == raw["human_review"]["content_sha256"]
    assert candidate["task_plan"]["review_status"] == "pending_review"


@pytest.mark.parametrize("invalidity", [
    "missing_review", "pending_with_review", "empty_reviewer", "empty_evidence", "missing_time", "naive_time",
    "wrong_skill", "wrong_version", "wrong_hash", "changed_template", "changed_provenance",
])
def test_unsubstantiated_or_stale_human_review_is_rejected(invalidity):
    from pydantic import ValidationError

    from agent.tools.skill_registry import RegisteredSkill

    data = _reviewed_skill_data()
    if invalidity == "missing_review":
        data.pop("human_review")
    elif invalidity == "pending_with_review":
        data["review_status"] = "pending_human_review"
    elif invalidity == "empty_reviewer":
        data["human_review"]["reviewer"] = "  "
    elif invalidity == "empty_evidence":
        data["human_review"]["evidence"] = "  "
    elif invalidity == "missing_time":
        data["human_review"].pop("reviewed_at")
    elif invalidity == "naive_time":
        data["human_review"]["reviewed_at"] = "2026-10-31T12:00:00"
    elif invalidity == "wrong_skill":
        data["human_review"]["skill_id"] = "qwa-other"
    elif invalidity == "wrong_version":
        data["human_review"]["version"] = "1.0.1"
    elif invalidity == "wrong_hash":
        data["human_review"]["content_sha256"] = "a" * 64
    elif invalidity == "changed_template":
        data["task_plan_template"]["task_summary"] = "Changed after review"
    elif invalidity == "changed_provenance":
        data["provenance"][0]["validation"] = "Changed evidence"
    with pytest.raises(ValidationError):
        RegisteredSkill.model_validate(data)


def test_human_review_hash_is_canonical_and_registry_copies_validated_content():
    from agent.tools.skill_registry import RegisteredSkill, SkillRegistry, SkillTemplateError, skill_content_sha256

    raw = _reviewed_skill_data()
    reordered = {key: raw[key] for key in reversed(raw)}
    reviewed = RegisteredSkill.model_validate(raw)
    assert skill_content_sha256(reviewed) == skill_content_sha256(RegisteredSkill.model_validate(reordered))
    registry = SkillRegistry([reviewed])
    reviewed.task_plan_template["task_summary"] = "changed after registration"
    with pytest.raises(SkillTemplateError):
        SkillRegistry([reviewed])
    example = registry.matching_examples("加载", files())[0]
    assert example["task_plan"]["task_summary"] != "changed after registration"
    assert example["review_status"] == "human_reviewed"


def test_corrupt_catalog_entry_is_rejected_even_before_optional_candidate_matching():
    from pydantic import ValidationError
    from agent.tools.skill_registry import RegisteredSkill

    data = _pending_skill_data()
    data["task_plan_template"]["task_summary"] = "${not_declared}"
    with pytest.raises(ValidationError):
        RegisteredSkill.model_validate(data)
