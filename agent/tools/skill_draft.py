#!/usr/bin/env python3
"""Deterministic SkillDraft extraction from successful execution traces."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.tools.tool_contracts import ExecutionTrace, StepAttempt

SkillReviewStatus = Literal["draft", "pending_review", "approved", "rejected"]
SkillVariableKind = Literal[
    "input_path",
    "output_path",
    "artifact_path",
    "layer_name",
    "parameter",
]

PATH_KEY_HINTS = ("path", "file", "filename", "uri")
INPUT_KEY_HINTS = ("input", "source")
OUTPUT_KEY_HINTS = ("output", "dest", "destination", "save", "export")
LAYER_KEY_HINTS = ("layer", "layer_name", "layer_id", "name")


class SkillExtractionError(ValueError):
    """Raised when a trace is not suitable for SkillDraft extraction."""


class SkillVariable(BaseModel):
    """A generalized value extracted from a concrete execution trace."""

    name: str = Field(description="Stable variable name without placeholder syntax")
    kind: SkillVariableKind = Field(description="Variable category")
    original_value: Any = Field(description="Concrete value from the source trace")
    placeholder: str = Field(description="Placeholder used in templates")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional variable metadata"
    )


class SkillStepTemplate(BaseModel):
    """Reusable template for one traced tool call."""

    step_id: int = Field(description="Source step ID")
    tool_name: str = Field(description="MCP tool name")
    description: str | None = Field(
        default=None, description="Human-readable step description"
    )
    arguments_template: dict[str, Any] = Field(
        default_factory=dict, description="Tool arguments with generalized variables"
    )
    artifacts_template: list[dict[str, Any]] = Field(
        default_factory=list, description="Artifacts with generalized variables"
    )
    risk_level: str = Field(default="low", description="Source tool risk level")
    requires_review: bool = Field(
        default=False, description="Whether the source tool required review"
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Trace evidence used for this step"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional step metadata"
    )


class SkillDraft(BaseModel):
    """Unregistered skill candidate extracted from a successful execution trace."""

    draft_id: str = Field(description="Stable draft ID derived from the source trace")
    name: str = Field(description="Draft skill name")
    summary: str = Field(description="Draft skill summary")
    source_trace_id: str = Field(description="ExecutionTrace ID used as source")
    source_task_id: str | None = Field(
        default=None, description="Optional source TaskPlan ID"
    )
    steps: list[SkillStepTemplate] = Field(
        default_factory=list, description="Reusable step templates"
    )
    variables: list[SkillVariable] = Field(
        default_factory=list, description="Generalized source values"
    )
    success_criteria: list[str] = Field(
        default_factory=list, description="Minimal success signals from the trace"
    )
    repair_strategies: list[dict[str, Any]] = Field(
        default_factory=list, description="Repair strategy summaries from failed attempts"
    )
    review_status: SkillReviewStatus = Field(
        default="draft", description="Manual review state for this draft"
    )
    is_registered: bool = Field(
        default=False, description="Drafts are never registered automatically"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional extraction metadata"
    )


class _TemplateBuilder:
    def __init__(self) -> None:
        self.variables: list[SkillVariable] = []
        self._value_to_placeholder: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    def generalize_arguments(self, value: Any) -> Any:
        return self._generalize(value, key_path=(), artifact=False)

    def generalize_artifacts(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            self._generalize(artifact, key_path=("artifact",), artifact=True)
            for artifact in artifacts
        ]

    def _generalize(self, value: Any, *, key_path: tuple[str, ...], artifact: bool) -> Any:
        if isinstance(value, dict):
            return {
                key: self._generalize(
                    child,
                    key_path=(*key_path, str(key)),
                    artifact=artifact,
                )
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [
                self._generalize(child, key_path=key_path, artifact=artifact)
                for child in value
            ]
        if isinstance(value, str):
            kind = self._classify_string(key_path, value, artifact=artifact)
            if kind is not None:
                return self._placeholder(kind, value, key_path)
        return value

    def _classify_string(
        self, key_path: tuple[str, ...], value: str, *, artifact: bool
    ) -> SkillVariableKind | None:
        normalized_keys = [key.lower() for key in key_path]
        leaf_key = normalized_keys[-1] if normalized_keys else ""
        if artifact and _looks_path_like(value):
            return "artifact_path"
        if _has_hint(leaf_key, INPUT_KEY_HINTS) and _looks_path_like(value):
            return "input_path"
        if _has_hint(leaf_key, OUTPUT_KEY_HINTS) and _looks_path_like(value):
            return "output_path"
        if _has_hint(leaf_key, PATH_KEY_HINTS) and _looks_path_like(value):
            return "artifact_path" if artifact else "parameter"
        if _has_hint(leaf_key, LAYER_KEY_HINTS) and not _looks_path_like(value):
            return "layer_name"
        return None

    def _placeholder(
        self, kind: SkillVariableKind, value: str, key_path: tuple[str, ...]
    ) -> str:
        cache_key = (kind, value)
        cached = self._value_to_placeholder.get(cache_key)
        if cached is not None:
            return cached
        self._counts[kind] = self._counts.get(kind, 0) + 1
        name = f"{kind}_{self._counts[kind]}"
        placeholder = f"${{{name}}}"
        self._value_to_placeholder[cache_key] = placeholder
        self.variables.append(
            SkillVariable(
                name=name,
                kind=kind,
                original_value=value,
                placeholder=placeholder,
                metadata={"key_path": list(key_path)},
            )
        )
        return placeholder


class SkillExtractor:
    """Extract unregistered SkillDrafts from complete successful traces."""

    def extract(self, trace: ExecutionTrace) -> SkillDraft:
        self._validate_trace(trace)
        builder = _TemplateBuilder()
        successful_attempts = self._successful_attempts(trace)
        steps = [
            self._step_template(attempt, builder) for attempt in successful_attempts
        ]
        return SkillDraft(
            draft_id=f"draft-{trace.trace_id}",
            name=self._draft_name(trace),
            summary=self._draft_summary(trace, steps),
            source_trace_id=trace.trace_id,
            source_task_id=trace.task_id,
            steps=steps,
            variables=builder.variables,
            success_criteria=self._success_criteria(successful_attempts, builder),
            repair_strategies=self._repair_strategies(trace),
            metadata={
                "attempt_count": len(trace.attempts),
                "source_trace_status": trace.status,
            },
        )

    def _validate_trace(self, trace: ExecutionTrace) -> None:
        latest_attempt = trace.latest_attempt()
        if latest_attempt is None:
            raise SkillExtractionError("SkillDraft extraction requires at least one attempt")
        if trace.status != "success" or latest_attempt.status != "success":
            raise SkillExtractionError(
                "SkillDraft extraction requires a complete successful trace"
            )

    def _successful_attempts(self, trace: ExecutionTrace) -> list[StepAttempt]:
        return [attempt for attempt in trace.attempts if attempt.status == "success"]

    def _step_template(
        self, attempt: StepAttempt, builder: _TemplateBuilder
    ) -> SkillStepTemplate:
        metadata = attempt.tool_call.metadata
        return SkillStepTemplate(
            step_id=attempt.step_id,
            tool_name=attempt.tool_call.tool_name,
            description=metadata.get("description"),
            arguments_template=builder.generalize_arguments(attempt.tool_call.arguments),
            artifacts_template=builder.generalize_artifacts(attempt.artifacts),
            risk_level=attempt.tool_call.risk_level,
            requires_review=attempt.tool_call.requires_review,
            evidence=self._evidence(attempt),
            metadata={
                "attempt_id": attempt.attempt_id,
                "candidate_gdal_apis": metadata.get("candidate_gdal_apis", []),
                "candidate_pyqgis_apis": metadata.get("candidate_pyqgis_apis", []),
            },
        )

    def _evidence(self, attempt: StepAttempt) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "observation_status": attempt.observation.status,
            "observation_message": attempt.observation.message,
        }
        for key in ("retrieval_context", "repair_retrieval_context"):
            if key in attempt.metadata:
                evidence[key] = _compact_context(attempt.metadata[key])
        return evidence

    def _success_criteria(
        self, attempts: list[StepAttempt], builder: _TemplateBuilder
    ) -> list[str]:
        criteria: list[str] = []
        for attempt in attempts:
            message = attempt.observation.message
            if message:
                criteria.append(message)
            for artifact in builder.generalize_artifacts(attempt.artifacts):
                artifact_path = artifact.get("path")
                if artifact_path:
                    criteria.append(f"artifact_created:{artifact_path}")
        return _dedupe(criteria)

    def _repair_strategies(self, trace: ExecutionTrace) -> list[dict[str, Any]]:
        strategies: list[dict[str, Any]] = []
        for attempt in trace.attempts:
            if attempt.status == "success":
                continue
            repair_action = attempt.metadata.get("repair_action")
            evaluation = attempt.metadata.get("evaluation", {})
            if not repair_action and not evaluation:
                continue
            strategy = {
                "step_id": attempt.step_id,
                "tool_name": attempt.tool_call.tool_name,
                "error_type": evaluation.get("error_type")
                or attempt.observation.error_type,
                "evaluation_reason": evaluation.get("reason"),
            }
            if isinstance(repair_action, dict):
                strategy.update(
                    {
                        "action_type": repair_action.get("action_type"),
                        "repair_reason": repair_action.get("reason"),
                        "updated_arguments": repair_action.get("updated_arguments", {}),
                    }
                )
            if "repair_retrieval_context" in attempt.metadata:
                strategy["retrieval_context"] = _compact_context(
                    attempt.metadata["repair_retrieval_context"]
                )
            strategies.append(strategy)
        return strategies

    def _draft_name(self, trace: ExecutionTrace) -> str:
        task_part = trace.task_id or trace.trace_id
        return f"Draft skill for {task_part}"

    def _draft_summary(
        self, trace: ExecutionTrace, steps: list[SkillStepTemplate]
    ) -> str:
        tools = ", ".join(_dedupe([step.tool_name for step in steps]))
        return f"Skill draft extracted from successful trace {trace.trace_id}: {tools}"


def _looks_path_like(value: str) -> bool:
    lowered = value.lower()
    return (
        "/" in value
        or "\\" in value
        or lowered.startswith("file:")
        or any(
            lowered.endswith(suffix)
            for suffix in (
                ".gpkg",
                ".shp",
                ".tif",
                ".tiff",
                ".png",
                ".jpg",
                ".jpeg",
                ".qgz",
                ".qgs",
                ".geojson",
                ".json",
                ".csv",
            )
        )
    )


def _has_hint(key: str, hints: tuple[str, ...]) -> bool:
    return any(hint in key for hint in hints)


def _compact_context(context: Any) -> Any:
    if not isinstance(context, dict):
        return context
    compact = {
        key: context[key]
        for key in ("query", "query_source", "tool_name", "error_type")
        if key in context
    }
    records = context.get("records")
    if isinstance(records, list):
        compact["records"] = [
            {
                key: record[key]
                for key in ("api_name", "source", "summary", "signature")
                if isinstance(record, dict) and key in record
            }
            for record in records[:3]
        ]
    return compact


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped
