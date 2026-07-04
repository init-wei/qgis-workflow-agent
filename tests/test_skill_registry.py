from datetime import UTC, datetime

import pytest

from agent.tools.skill_draft import SkillExtractor
from agent.tools.skill_registry import (
    SkillRegistrationError,
    SkillRegistry,
    build_skill_execution_plan,
)
from agent.tools.tool_contracts import ExecutionTrace, StepAttempt, ToolCallPlan, ToolObservation


def _successful_trace(*, risk_level: str = "low", requires_review: bool = False):
    started_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    finished_at = datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC)
    observation = ToolObservation(
        tool_name="execute_processing",
        status="success",
        message="Created output layer",
        artifacts=[{"kind": "path", "path": "shared/demo/output.gpkg"}],
    )
    trace = ExecutionTrace(trace_id="trace-skill-1", task_id="task-skill-1")
    trace.append_attempt(
        StepAttempt(
            attempt_id="attempt-1",
            step_id=1,
            tool_call=ToolCallPlan(
                step_id=1,
                tool_name="execute_processing",
                arguments={
                    "algorithm": "native:centroids",
                    "parameters": {
                        "INPUT": "shared/demo/input.gpkg",
                        "OUTPUT": "shared/demo/output.gpkg",
                        "layer_name": "parcels",
                    },
                },
                risk_level=risk_level,
                requires_review=requires_review,
                metadata={
                    "description": "Create centroids",
                    "candidate_pyqgis_apis": ["processing.run"],
                },
            ),
            observation=observation,
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=1000,
            artifacts=observation.artifacts,
        )
    )
    return trace


def test_registers_auto_approved_low_risk_skill_and_renders_tool_call_plan():
    draft = SkillExtractor().extract(_successful_trace())

    skill = SkillRegistry().register(
        draft,
        auto_approve=True,
        source="test_fixture",
    )
    plan = build_skill_execution_plan(
        skill,
        {
            "input_path_1": "shared/new/input.gpkg",
            "output_path_1": "shared/new/output.gpkg",
            "layer_name_1": "new_parcels",
            "artifact_path_1": "shared/new/output.gpkg",
        },
    )

    assert skill.is_registered is True
    assert skill.review_status == "approved"
    assert skill.metadata["registration_source"] == "test_fixture"
    assert plan.status == "ready_for_controlled_execution"
    assert plan.calls[0].tool_name == "execute_processing"
    assert plan.calls[0].arguments["parameters"]["INPUT"] == "shared/new/input.gpkg"
    assert plan.calls[0].arguments["parameters"]["OUTPUT"] == "shared/new/output.gpkg"
    assert plan.calls[0].metadata["registered_skill_id"] == skill.skill_id
    assert "artifact_created:shared/new/output.gpkg" in plan.success_criteria


def test_rejects_unapproved_manual_registration():
    draft = SkillExtractor().extract(_successful_trace())

    with pytest.raises(SkillRegistrationError, match="approved review"):
        SkillRegistry().register(draft)


def test_auto_registration_rejects_high_risk_steps():
    draft = SkillExtractor().extract(
        _successful_trace(risk_level="high", requires_review=True)
    )

    with pytest.raises(SkillRegistrationError, match="high-risk"):
        SkillRegistry().register(draft, auto_approve=True)
