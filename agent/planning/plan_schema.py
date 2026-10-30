#!/usr/bin/env python3
"""Strict TaskPlan schema used by the Phase 1 planner compatibility layer."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


RiskLevel = Literal["low", "medium", "high"]
ReviewStatus = Literal["draft", "pending_review", "approved", "rejected"]
IssueSeverity = Literal["warning", "error"]


class InputSpec(BaseModel):
    """Input required before the task can run."""

    model_config = ConfigDict(extra="forbid")

    input_id: str = Field(description="Stable input reference ID")
    description: str = Field(description="Human readable input description")
    path: str | None = Field(default=None, description="File path or URI when known")
    source: str | None = Field(default=None, description="Source description when path is not known")
    file_id: str | None = Field(default=None, description="Server-issued uploaded file ID")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$", description="Server-bound checksum of the original input")
    layer_name: str | None = Field(default=None, description="Selected source layer when a container has multiple layers")


class OutputSpec(BaseModel):
    """Expected artifact produced by the task."""

    model_config = ConfigDict(extra="forbid")

    output_id: str = Field(description="Stable output reference ID")
    description: str = Field(description="Human readable output description")
    path: str | None = Field(default=None, description="Expected output path or URI")


class PlanStep(BaseModel):
    """One planned GIS operation."""

    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(description="Step ID")
    description: str = Field(description="Step description")
    step_type: str = Field(default="qgis_operation", description="Step type")
    candidate_gdal_apis: list[str] = Field(default_factory=list, description="Candidate GDAL APIs")
    candidate_pyqgis_apis: list[str] = Field(default_factory=list, description="Candidate PyQGIS APIs")
    allowed_tools: list[str] = Field(default_factory=list, description="Tools allowed for the step")
    input_refs: list[str] = Field(default_factory=list, description="InputSpec IDs used by the step")
    output_refs: list[str] = Field(default_factory=list, description="OutputSpec IDs produced by the step")
    risk_level: RiskLevel = Field(description="Step risk level")
    requires_review: StrictBool = Field(description="Whether the step requires human review")


class ToolCallPlan(BaseModel):
    """A concrete MCP invocation included in the reviewable TaskPlan."""

    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(description="TaskPlan step ID")
    tool_name: str = Field(description="MCP tool name")
    arguments: dict[str, Any] = Field(description="Exact arguments sent to the MCP tool")
    risk_level: RiskLevel = Field(default="low", description="Call risk level")
    requires_review: StrictBool = Field(default=False, description="Whether this call has an additional risk gate")
    distance_unit: Literal["m"] | None = Field(
        default=None, description="Reviewed physical unit of a buffer DISTANCE; not an MCP argument"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Non-executable call annotations")


class TaskPlan(BaseModel):
    """Strict task-level planning contract."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(description="Stable task plan ID")
    task_summary: str = Field(description="Task summary")
    steps: list[PlanStep] = Field(description="Planned steps")
    risk_level: RiskLevel = Field(description="Overall risk level")
    review_status: ReviewStatus = Field(description="Human review status")
    required_inputs: list[InputSpec] = Field(default_factory=list, description="Required task inputs")
    expected_outputs: list[OutputSpec] = Field(default_factory=list, description="Expected task outputs")
    tool_calls: list[ToolCallPlan] = Field(default_factory=list, description="Exact calls reviewed and executed for the steps")
    revision: int = Field(default=1, ge=1, description="Reviewable version within a task")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Planning metadata")


class PlanValidationIssue(BaseModel):
    """Structured issue emitted by the TaskPlan validator."""

    code: str = Field(description="Machine readable issue code")
    message: str = Field(description="Human readable issue message")
    severity: IssueSeverity = Field(default="error", description="Issue severity")
    step_id: int | None = Field(default=None, description="Related step ID when applicable")
    field: str | None = Field(default=None, description="Related field when applicable")


class PlanValidationReport(BaseModel):
    """Validation result for a TaskPlan."""

    is_valid: bool = Field(description="Whether there are no error-level validation issues")
    issues: list[PlanValidationIssue] = Field(default_factory=list, description="Validation issues")
    requires_review: StrictBool = Field(description="Whether any step or task requires human review")
    summary: str = Field(description="Short validation summary")
