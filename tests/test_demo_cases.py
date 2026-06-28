from agent.tools.demo_cases import (
    load_demo_case,
    load_demo_cases,
    replay_demo_case,
)
from agent.tools.skill_draft import SkillDraft
from agent.tools.tool_contracts import ExecutionTrace


def test_loads_three_replayable_demo_cases():
    cases = load_demo_cases()

    assert [case.case_id for case in cases] == [
        "simple_success",
        "failure_repair",
        "skill_draft_preview",
    ]
    for case in cases:
        reloaded = ExecutionTrace.model_validate_json(case.trace.model_dump_json())
        assert reloaded.trace_id == case.trace.trace_id
        assert replay_demo_case(case).status == "success"


def test_failure_repair_demo_contains_failed_attempt_success_and_repair_summary():
    case = load_demo_case("failure_repair")
    result = replay_demo_case(case)

    assert [attempt.status for attempt in case.trace.attempts] == [
        "failed",
        "success",
    ]
    assert result.attempts_used == 2
    assert result.repair_summaries
    assert result.repair_summaries[0]["error_type"] == "parameter_error"
    assert result.repair_summaries[0]["action_type"] == "update_arguments"


def test_skill_draft_preview_demo_generates_unregistered_draft_and_reuse_preview():
    case = load_demo_case("skill_draft_preview")
    result = replay_demo_case(case)

    draft = SkillDraft.model_validate(result.skill_draft)
    assert draft.review_status == "draft"
    assert draft.is_registered is False
    assert result.reuse_preview
    assert result.reuse_preview["tool_name"] == "execute_processing"
    assert "shared/demo/reuse_output.gpkg" in str(result.reuse_preview)
    assert "${output_path_1}" not in str(result.reuse_preview)


def test_unknown_demo_case_raises_key_error():
    try:
        load_demo_case("missing-case")
    except KeyError as exc:
        assert "missing-case" in str(exc)
    else:
        raise AssertionError("Expected missing demo case to raise KeyError")
