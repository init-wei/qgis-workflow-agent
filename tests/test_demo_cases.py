from agent.tools.demo_cases import (
    load_demo_case,
    load_demo_cases,
    replay_demo_case,
)
from agent.tools.skill_draft import SkillDraft
from agent.tools.tool_contracts import ExecutionTrace
import subprocess
import sys
from pathlib import Path


def test_loads_replayable_demo_cases():
    cases = load_demo_cases()

    assert [case.case_id for case in cases] == [
        "simple_success",
        "failure_repair",
        "skill_draft_preview",
        "skill_reuse_live_smoke",
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


def test_skill_draft_preview_demo_registers_skill_and_renders_execution_plan():
    case = load_demo_case("skill_draft_preview")
    result = replay_demo_case(case)

    draft = SkillDraft.model_validate(result.skill_draft)
    assert draft.review_status == "draft"
    assert draft.is_registered is False
    assert result.registered_skill
    assert result.registered_skill["is_registered"] is True
    assert result.registered_skill["review_status"] == "approved"
    assert result.skill_execution_plan
    assert result.skill_execution_plan["status"] == "ready_for_controlled_execution"
    assert result.skill_execution_plan["calls"][0]["tool_name"] == "execute_processing"
    assert "shared/demo/reuse_output.gpkg" in str(result.skill_execution_plan)
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


def test_replay_script_prints_registered_skill_execution_plan():
    script = Path(__file__).parents[1] / "scripts" / "replay_demo_cases.py"

    result = subprocess.run(
        [sys.executable, str(script), "--case-id", "skill_reuse_live_smoke"],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "registered_skill=skill-draft-demo-skill-reuse-live-smoke" in result.stdout
    assert "skill_execution_plan=ready_for_controlled_execution" in result.stdout
