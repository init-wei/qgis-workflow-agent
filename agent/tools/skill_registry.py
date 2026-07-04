#!/usr/bin/env python3
"""Controlled registration and execution-plan rendering for SkillDrafts."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.tools.skill_draft import SkillDraft, SkillStepTemplate, SkillVariable
from agent.tools.tool_contracts import RiskLevel, ToolCallPlan


class SkillRegistrationError(ValueError):
    """Raised when a SkillDraft cannot be safely registered."""


class RegisteredSkill(BaseModel):
    """A reviewed SkillDraft that can render controlled tool-call plans."""

    skill_id: str = Field(description="Stable registered skill ID")
    name: str = Field(description="Human-readable skill name")
    summary: str = Field(description="Skill summary")
    source_draft_id: str = Field(description="SkillDraft ID used for registration")
    source_trace_id: str = Field(description="ExecutionTrace ID used as evidence")
    source_task_id: str | None = Field(default=None)
    steps: list[SkillStepTemplate] = Field(default_factory=list)
    variables: list[SkillVariable] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    review_status: Literal["approved"] = "approved"
    is_registered: Literal[True] = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillExecutionPlan(BaseModel):
    """Tool-call plan rendered from a registered skill and explicit variables."""

    skill_id: str = Field(description="Registered skill ID")
    status: Literal["ready_for_controlled_execution"] = "ready_for_controlled_execution"
    calls: list[ToolCallPlan] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillRegistry:
    """In-memory registry for reviewed skills used by demo and tests."""

    def __init__(self) -> None:
        self._skills: dict[str, RegisteredSkill] = {}

    def register(
        self,
        draft: SkillDraft,
        *,
        auto_approve: bool = False,
        source: str = "manual_review",
    ) -> RegisteredSkill:
        """Register a reviewed draft after conservative safety checks."""

        if not draft.steps:
            raise SkillRegistrationError("Skill registration requires at least one step")
        if not auto_approve and draft.review_status != "approved":
            raise SkillRegistrationError("Skill registration requires approved review")
        for step in draft.steps:
            if step.risk_level not in {"low", "medium", "high"}:
                raise SkillRegistrationError(
                    f"Unsupported skill step risk level: {step.risk_level}"
                )
            if step.risk_level == "high" or step.requires_review:
                raise SkillRegistrationError(
                    "Auto registration does not accept high-risk or review-required steps"
                )

        skill_id = f"skill-{draft.draft_id}"
        registered = RegisteredSkill(
            skill_id=skill_id,
            name=draft.name,
            summary=draft.summary,
            source_draft_id=draft.draft_id,
            source_trace_id=draft.source_trace_id,
            source_task_id=draft.source_task_id,
            steps=draft.steps,
            variables=draft.variables,
            success_criteria=draft.success_criteria,
            metadata={
                **draft.metadata,
                "registration_source": source,
                "auto_approved": auto_approve,
            },
        )
        self._skills[registered.skill_id] = registered
        return registered

    def get(self, skill_id: str) -> RegisteredSkill | None:
        """Return a registered skill by ID."""

        return self._skills.get(skill_id)


def build_skill_execution_plan(
    skill: RegisteredSkill,
    replacements: dict[str, Any],
) -> SkillExecutionPlan:
    """Render a registered skill into explicit controlled tool calls."""

    calls = []
    for index, step in enumerate(skill.steps, start=1):
        calls.append(
            ToolCallPlan(
                step_id=step.step_id,
                tool_name=step.tool_name,
                arguments=_replace_placeholders(step.arguments_template, replacements),
                risk_level=_risk_level(step.risk_level),
                requires_review=step.requires_review,
                metadata={
                    **step.metadata,
                    "registered_skill_id": skill.skill_id,
                    "source_draft_id": skill.source_draft_id,
                    "skill_step_index": index,
                    "description": step.description,
                },
            )
        )
    return SkillExecutionPlan(
        skill_id=skill.skill_id,
        calls=calls,
        success_criteria=_replace_placeholders(skill.success_criteria, replacements),
        metadata={
            "source_trace_id": skill.source_trace_id,
            "source_task_id": skill.source_task_id,
            "variable_names": [variable.name for variable in skill.variables],
        },
    )


def _risk_level(value: str) -> RiskLevel:
    if value in {"low", "medium", "high"}:
        return value  # type: ignore[return-value]
    raise SkillRegistrationError(f"Unsupported skill step risk level: {value}")


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
