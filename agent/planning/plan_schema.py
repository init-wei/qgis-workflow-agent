#!/usr/bin/env python3
"""Strict TaskPlan schema used by the Phase 1 planner compatibility layer."""

from typing import Any, Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["low", "medium", "high"]
ReviewStatus = Literal["draft", "pending_review", "approved", "rejected"]
IssueSeverity = Literal["warning", "error"]


class InputSpec(BaseModel):
    """Input required before the task can run."""

    input_id: str = Field(description="Stable input reference ID")
    description: str = Field(description="Human readable input description")
    path: str | None = Field(default=None, description="File path or URI when known")
    source: str | None = Field(default=None, description="Source description when path is not known")


class OutputSpec(BaseModel):
    """Expected artifact produced by the task."""

    output_id: str = Field(description="Stable output reference ID")
    description: str = Field(description="Human readable output description")
    path: str | None = Field(default=None, description="Expected output path or URI")


class PlanStep(BaseModel):
    """One planned GIS operation."""

    step_id: int = Field(description="Step ID")
    description: str = Field(description="Step description")
    step_type: str = Field(default="qgis_operation", description="Step type")
    candidate_gdal_apis: list[str] = Field(default_factory=list, description="Candidate GDAL APIs")
    candidate_pyqgis_apis: list[str] = Field(default_factory=list, description="Candidate PyQGIS APIs")
    allowed_tools: list[str] = Field(default_factory=list, description="Tools allowed for the step")
    input_refs: list[str] = Field(default_factory=list, description="InputSpec IDs used by the step")
    output_refs: list[str] = Field(default_factory=list, description="OutputSpec IDs produced by the step")
    risk_level: RiskLevel = Field(description="Step risk level")
    requires_review: bool = Field(description="Whether the step requires human review")


class TaskPlan(BaseModel):
    """Strict task-level planning contract."""

    task_id: str = Field(description="Stable task plan ID")
    task_summary: str = Field(description="Task summary")
    steps: list[PlanStep] = Field(description="Planned steps")
    risk_level: RiskLevel = Field(description="Overall risk level")
    review_status: ReviewStatus = Field(description="Human review status")
    required_inputs: list[InputSpec] = Field(default_factory=list, description="Required task inputs")
    expected_outputs: list[OutputSpec] = Field(default_factory=list, description="Expected task outputs")
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
    requires_review: bool = Field(description="Whether any step or task requires human review")
    summary: str = Field(description="Short validation summary")
