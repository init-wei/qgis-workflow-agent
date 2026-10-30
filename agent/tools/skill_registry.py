#!/usr/bin/env python3
"""Git-maintained task examples for planning, with no execution authority."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent.planning.plan_schema import TaskPlan


class SkillTemplateError(ValueError):
    """A template, its variables, or its replacements are invalid."""


class TemplateVariable(BaseModel):
    """A typed value supplied by a selected file or by an illustrative example."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: Literal["string", "number", "integer", "boolean", "string_list"]
    source: Literal["example", "file_id", "file_path", "layer_name", "filename"]
    role: str | None = None
    example: Any = None
    required: bool = True

    @model_validator(mode="after")
    def valid_source(self) -> TemplateVariable:
        if self.source == "example":
            if self.role is not None or self.example is None:
                raise ValueError("Example variables require an example and no file role")
            if not _has_type(self.example, self.type):
                raise ValueError(f"Example for {self.name} must be {self.type}")
        elif not self.role or self.example is not None or self.type != "string":
            raise ValueError("File variables require a role and string type, without an example")
        return self


class InputCondition(BaseModel):
    """File roles are bound in the order selected by the user."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    geometry: Literal["vector", "polygon"]


class Provenance(BaseModel):
    """Real task evidence for the specific operation, not template approval."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    evidence_url: str = Field(min_length=1)
    qgis_version: str = Field(min_length=1)
    validation: str = Field(min_length=1)


class HumanReview(BaseModel):
    """A human review fact tied to a particular version and its content."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reviewer: str = Field(min_length=1)
    reviewed_at: AwareDatetime
    evidence: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegisteredSkill(BaseModel):
    """Versioned, agent-curated planning reference with explicit review facts."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    family: Literal["load", "reproject", "buffer", "clip", "dissolve", "statistics"]
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    aliases: list[str] = Field(min_length=1)
    input_conditions: list[InputCondition] = Field(min_length=1)
    variables: list[TemplateVariable] = Field(min_length=1)
    task_plan_template: dict[str, Any]
    provenance: list[Provenance] = Field(min_length=1)
    curation_status: Literal["agent_curated"]
    review_status: Literal["pending_human_review", "human_reviewed"]
    human_review: HumanReview | None = None

    @model_validator(mode="after")
    def valid_catalog_entry(self) -> RegisteredSkill:
        if any(not term.strip() for term in [*self.tags, *self.aliases]):
            raise ValueError("Tags and aliases cannot be empty")
        names = [item.name for item in self.variables]
        roles = [item.role for item in self.input_conditions]
        if len(names) != len(set(names)) or len(roles) != len(set(roles)):
            raise ValueError("Variable names and file roles must be unique")
        if any(item.role is not None and item.role not in roles for item in self.variables):
            raise ValueError("Each file variable must reference a declared role")
        # Validate every catalog template independently of selected files. A
        # malformed entry must not be hidden by an optional candidate miss.
        example_bindings = {
            variable.name: variable.example if variable.source == "example" else "selected-file-example"
            for variable in self.variables
        }
        try:
            example = TaskPlan.model_validate(render_template(self.task_plan_template, self.variables, example_bindings))
        except (SkillTemplateError, ValidationError) as exc:
            raise ValueError(f"Invalid catalog TaskPlan example: {exc}") from exc
        if example.review_status != "pending_review":
            raise ValueError("A template example must remain pending review")
        if self.review_status == "pending_human_review":
            if self.human_review is not None:
                raise ValueError("Pending skills cannot include a human review fact")
        else:
            review = self.human_review
            if review is None:
                raise ValueError("Human-reviewed skills require review evidence")
            if review.skill_id != self.skill_id or review.version != self.version:
                raise ValueError("Human review must identify this skill and version")
            if review.content_sha256 != skill_content_sha256(self):
                raise ValueError("Human review does not match this skill's content")
        return self


def skill_content_sha256(skill: RegisteredSkill) -> str:
    """Hash normalized catalog content, excluding review facts to avoid a cycle."""

    content = skill.model_dump(mode="json", exclude={"review_status", "human_review"})
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _has_type(value: Any, value_type: str) -> bool:
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    if value_type == "integer":
        return type(value) is int
    if value_type == "boolean":
        return type(value) is bool
    if value_type == "string_list":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return False


def render_template(
    template: Any,
    variables: Sequence[TemplateVariable | dict[str, Any]],
    bindings: Mapping[str, Any],
) -> Any:
    """Replace typed values or reject unbound, malformed, or unsafe placeholders.

    A complete placeholder returns its original value and type; interpolation
    into a larger string accepts string values only. No input is mutated. With
    empty variables/bindings the same function checks a final plan for leftover
    placeholders before server-owned input/output paths are bound.
    """

    try:
        declared = [TemplateVariable.model_validate(item) for item in variables]
    except ValidationError as exc:
        raise SkillTemplateError(f"Invalid template variable: {exc}") from exc
    by_name = {item.name: item for item in declared}
    if len(by_name) != len(declared):
        raise SkillTemplateError("Duplicate template variable")
    if any(not isinstance(name, str) for name in bindings):
        raise SkillTemplateError("Template binding names must be strings")
    unknown = set(bindings) - set(by_name)
    if unknown:
        raise SkillTemplateError(f"Unknown template bindings: {sorted(unknown)}")
    missing = [item.name for item in declared if item.required and item.name not in bindings]
    if missing:
        raise SkillTemplateError(f"Missing template bindings: {missing}")
    for name, value in bindings.items():
        if not _has_type(value, by_name[name].type):
            raise SkillTemplateError(f"Binding {name} must be {by_name[name].type}")
        _reject_unresolved(value)

    def value_for(name: str) -> Any:
        if name not in by_name or name not in bindings:
            raise SkillTemplateError(f"Unknown or missing template variable: {name}")
        return bindings[name]

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if isinstance(key, str) and "${" in key:
                    raise SkillTemplateError("Template placeholders are not allowed in keys")
                result[key] = visit(child)
            return result
        if isinstance(value, list):
            return [visit(child) for child in value]
        if isinstance(value, str):
            full = _PLACEHOLDER.fullmatch(value)
            if full:
                result = deepcopy(value_for(full.group(1)))
            else:
                def interpolate(match: re.Match[str]) -> str:
                    replacement = value_for(match.group(1))
                    if not isinstance(replacement, str):
                        raise SkillTemplateError("Only strings can be interpolated within text")
                    return replacement
                result = _PLACEHOLDER.sub(interpolate, value)
            _reject_unresolved(result)
            return result
        return deepcopy(value)

    return visit(template)


def _reject_unresolved(value: Any) -> None:
    if isinstance(value, str) and "${" in value:
        raise SkillTemplateError("Unresolved or malformed template placeholder")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_unresolved(key)
            _reject_unresolved(child)
    elif isinstance(value, list):
        for child in value:
            _reject_unresolved(child)


def _matches_term(query: str, term: str) -> bool:
    normalized = term.casefold().strip()
    if any(char.isascii() and char.isalnum() for char in normalized):
        return re.search(r"(?<![a-z0-9_])" + re.escape(normalized) + r"(?![a-z0-9_])", query) is not None
    return normalized in query


def _valid_file(file: Any, condition: InputCondition) -> bool:
    if not isinstance(file, dict):
        return False
    if any(not isinstance(file.get(key), str) or not file[key].strip()
           for key in ("file_id", "filename", "qgis_path", "layer_name")):
        return False
    metadata = file.get("layer_info")
    if not isinstance(metadata, dict) or metadata.get("is_2d") is False:
        return False
    geometry = metadata.get("geometry")
    if not isinstance(geometry, str):
        return False
    geometry = re.sub(r"[ _-]", "", geometry.casefold())
    known = {"point", "multipoint", "linestring", "multilinestring", "polygon", "multipolygon"}
    if geometry not in known:
        return False
    return condition.geometry == "vector" or geometry in {"polygon", "multipolygon"}


class SkillRegistry:
    """Read-only planning examples from the checked-in catalog."""

    def __init__(self, skills: Sequence[RegisteredSkill]) -> None:
        # Copy and revalidate to keep later mutations by callers from changing
        # a cached template or invalidating its review evidence.
        try:
            self._skills = tuple(RegisteredSkill.model_validate(item.model_dump(mode="json")) for item in skills)
        except ValidationError as exc:
            raise SkillTemplateError(f"Invalid skill catalog: {exc}") from exc
        identities = [(item.skill_id, item.version) for item in self._skills]
        if len(identities) != len(set(identities)):
            raise SkillTemplateError("Duplicate catalog skill ID and version")

    def matching_examples(self, query: str, uploaded_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return illustrative TaskPlans for matching intent and known input roles."""

        normalized_query = query.casefold()
        result: list[dict[str, Any]] = []
        for skill in self._skills:
            if not any(_matches_term(normalized_query, term) for term in [*skill.tags, *skill.aliases]):
                continue
            if len(uploaded_files) < len(skill.input_conditions):
                continue
            selected = uploaded_files[:len(skill.input_conditions)]
            if not all(_valid_file(file, condition) for file, condition in zip(selected, skill.input_conditions)):
                continue
            by_role = {condition.role: file for condition, file in zip(skill.input_conditions, selected)}
            bindings = {}
            file_bindings_safe = True
            for variable in skill.variables:
                if variable.source == "example":
                    bindings[variable.name] = variable.example
                else:
                    file = by_role[variable.role]
                    key = "qgis_path" if variable.source == "file_path" else variable.source
                    value = file[key]
                    if "${" in value:
                        # User file metadata is literal, but this renderer treats
                        # the same characters as template syntax. Omit only the
                        # optional example and leave ordinary planning available.
                        file_bindings_safe = False
                        break
                    bindings[variable.name] = value
            if not file_bindings_safe:
                continue
            rendered = render_template(skill.task_plan_template, skill.variables, bindings)
            try:
                plan = TaskPlan.model_validate(rendered)
            except ValidationError as exc:
                raise SkillTemplateError(f"Invalid TaskPlan example for {skill.skill_id}: {exc}") from exc
            if plan.review_status != "pending_review":
                raise SkillTemplateError("A template example must remain pending review")
            result.append({
                "skill_id": skill.skill_id,
                "version": skill.version,
                "family": skill.family,
                "name": skill.name,
                "summary": skill.summary,
                "review_status": skill.review_status,
                "human_review": skill.human_review.model_dump(mode="json") if skill.human_review else None,
                "task_plan": plan.model_dump(mode="json"),
                "provenance": [item.model_dump(mode="json") for item in skill.provenance],
            })
        return result


@lru_cache(maxsize=1)
def load_skill_registry() -> SkillRegistry:
    """Load the six checked-in templates once for the current process."""

    directory = Path(__file__).resolve().parents[1] / "skills"
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise SkillTemplateError("The checked-in skill catalog is missing")
    try:
        skills = [RegisteredSkill.model_validate(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    except (OSError, ValueError) as exc:
        raise SkillTemplateError(f"Invalid skill catalog: {exc}") from exc
    families = {skill.family for skill in skills}
    if len(skills) != 6 or families != {"load", "reproject", "buffer", "clip", "dissolve", "statistics"}:
        raise SkillTemplateError("The skill catalog must contain one template for each supported family")
    return SkillRegistry(skills)
