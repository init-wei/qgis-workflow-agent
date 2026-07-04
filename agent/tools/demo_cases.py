#!/usr/bin/env python3
"""Replayable Phase 9/10 demo cases backed by static trace fixtures."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent.tools.skill_draft import SkillDraft, SkillExtractor
from agent.tools.skill_registry import SkillRegistry, build_skill_execution_plan
from agent.tools.tool_contracts import ExecutionTrace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_CASES_DIR = PROJECT_ROOT / "demo_cases"


class DemoCase(BaseModel):
    """A reproducible demo case with a static execution trace fixture."""

    case_id: str = Field(description="Stable demo case ID")
    title: str = Field(description="Human-readable demo title")
    summary: str = Field(description="Short demo description")
    tags: list[str] = Field(default_factory=list, description="Demo categories")
    trace: ExecutionTrace = Field(description="Replayable execution trace")
    reuse_variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional variable replacements for SkillDraft reuse preview",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional fixture metadata"
    )


class DemoReplayResult(BaseModel):
    """Replay summary derived from a demo case fixture."""

    case_id: str = Field(description="Demo case ID")
    status: str = Field(description="Replay status")
    attempts_used: int = Field(description="Number of attempts in the trace")
    artifact_count: int = Field(description="Number of artifacts recorded")
    repair_summaries: list[dict[str, Any]] = Field(default_factory=list)
    skill_draft: dict[str, Any] | None = Field(default=None)
    registered_skill: dict[str, Any] | None = Field(default=None)
    skill_execution_plan: dict[str, Any] | None = Field(default=None)
    reuse_preview: dict[str, Any] | None = Field(default=None)
    trace_summary: dict[str, Any] = Field(default_factory=dict)


def load_demo_cases(directory: Path | None = None) -> list[DemoCase]:
    """Load all demo case fixtures in deterministic order."""

    case_dir = directory or DEMO_CASES_DIR
    cases = []
    for path in sorted(case_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            cases.append(DemoCase.model_validate(json.load(handle)))
    return cases


def load_demo_case(case_id: str, directory: Path | None = None) -> DemoCase:
    """Load one demo case fixture by ID."""

    for case in load_demo_cases(directory):
        if case.case_id == case_id:
            return case
    raise KeyError(f"Unknown demo case: {case_id}")


def replay_demo_case(case: DemoCase) -> DemoReplayResult:
    """Replay a static trace fixture into a deterministic inspection summary."""

    trace = case.trace
    skill_draft = None
    registered_skill = None
    skill_execution_plan = None
    reuse_preview = None
    repair_summaries: list[dict[str, Any]] = []

    if trace.status == "success" and trace.latest_attempt() is not None:
        draft = SkillExtractor().extract(trace)
        repair_summaries = draft.repair_strategies
        if case.reuse_variables or case.metadata.get("include_skill_draft"):
            skill_draft = draft.model_dump(mode="json")
            reuse_preview = build_reuse_preview(draft, case.reuse_variables)
        if case.metadata.get("auto_register_skill"):
            registry = SkillRegistry()
            registered = registry.register(
                draft,
                auto_approve=True,
                source="demo_fixture",
            )
            plan = build_skill_execution_plan(registered, case.reuse_variables)
            registered_skill = registered.model_dump(mode="json")
            skill_execution_plan = plan.model_dump(mode="json")

    return DemoReplayResult(
        case_id=case.case_id,
        status="success" if trace.status == "success" else "failed",
        attempts_used=len(trace.attempts),
        artifact_count=sum(len(attempt.artifacts) for attempt in trace.attempts),
        repair_summaries=repair_summaries,
        skill_draft=skill_draft,
        registered_skill=registered_skill,
        skill_execution_plan=skill_execution_plan,
        reuse_preview=reuse_preview,
        trace_summary={
            "trace_id": trace.trace_id,
            "task_id": trace.task_id,
            "trace_status": trace.status,
            "attempt_statuses": [attempt.status for attempt in trace.attempts],
            "tools": [attempt.tool_call.tool_name for attempt in trace.attempts],
        },
    )


def summarize_demo_case(case: DemoCase) -> dict[str, Any]:
    """Return a compact list-view summary for the demo API."""

    replay = replay_demo_case(case)
    return {
        "case_id": case.case_id,
        "title": case.title,
        "summary": case.summary,
        "tags": case.tags,
        "trace_id": case.trace.trace_id,
        "trace_status": case.trace.status,
        "attempts_used": replay.attempts_used,
        "artifact_count": replay.artifact_count,
        "has_repair": bool(replay.repair_summaries),
        "has_skill_draft": replay.skill_draft is not None,
        "has_registered_skill": replay.registered_skill is not None,
    }


def build_reuse_preview(
    draft: SkillDraft, replacements: dict[str, Any]
) -> dict[str, Any] | None:
    """Render the first SkillDraft step with explicit variable replacements."""

    if not draft.steps:
        return None
    step = draft.steps[0]
    return {
        "draft_id": draft.draft_id,
        "tool_name": step.tool_name,
        "arguments": _replace_placeholders(step.arguments_template, replacements),
        "success_criteria": _replace_placeholders(
            draft.success_criteria, replacements
        ),
    }


def _replace_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_placeholders(child, replacements)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_replace_placeholders(child, replacements) for child in value]
    if isinstance(value, str):
        replaced = value
        for name, replacement in replacements.items():
            replaced = replaced.replace(f"${{{name}}}", str(replacement))
        return replaced
    return value
