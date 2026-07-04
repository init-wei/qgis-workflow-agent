#!/usr/bin/env python3
"""Phase 2 tool call, observation, registry, and policy contracts."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool

RiskLevel = Literal["low", "medium", "high"]
ObservationStatus = Literal["success", "error", "malformed", "disconnected"]
AttemptStatus = Literal["success", "failed", "blocked"]
TraceStatus = Literal["running", "success", "failed"]
EvaluationOutcome = Literal["success", "retry", "abort", "ask_human"]
RepairActionType = Literal["update_arguments", "abort", "ask_human"]
LoopStatus = Literal["success", "failed", "needs_review"]
RiskReviewStatus = Literal["pending", "approved", "rejected", "modified"]
ErrorType = Literal[
    "parameter_error",
    "missing_file",
    "qgis_disconnected",
    "execution_error",
    "malformed_response",
    "unknown_error",
]


class ToolCallPlan(BaseModel):
    """A planned MCP tool call for a task step."""

    step_id: int = Field(description="TaskPlan step ID")
    tool_name: str = Field(description="MCP tool name")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Tool arguments"
    )
    risk_level: RiskLevel = Field(default="low", description="Tool call risk level")
    requires_review: StrictBool = Field(
        default=False, description="Whether this call requires human review"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional planning metadata"
    )


class ToolObservation(BaseModel):
    """Normalized result from a raw MCP tool response."""

    tool_name: str = Field(description="MCP tool name")
    status: ObservationStatus = Field(description="Normalized result status")
    error_type: ErrorType | None = Field(
        default=None, description="Normalized error type"
    )
    message: str | None = Field(
        default=None, description="Human-readable result or error message"
    )
    data: Any = Field(
        default=None, description="Normalized success payload when available"
    )
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list, description="Output artifacts discovered in response"
    )
    raw_response: Any = Field(default=None, description="Original raw MCP response")
    retryable: bool = Field(default=False, description="Whether a retry may help")


class PolicyDecision(BaseModel):
    """Decision emitted by tool, path, code, and risk policies."""

    allowed: bool = Field(description="Whether the call is allowed")
    reason: str = Field(
        default="allowed", description="Machine-readable decision reason"
    )
    message: str | None = Field(default=None, description="Human-readable explanation")
    requires_review: StrictBool = Field(
        default=False, description="Whether a human approval gate is required"
    )


class ToolSpec(BaseModel):
    """Registry entry for an MCP tool."""

    name: str = Field(description="MCP tool name")
    description: str = Field(default="", description="Tool description")
    risk_level: RiskLevel = Field(default="low", description="Default risk level")
    requires_review: StrictBool = Field(
        default=False, description="Whether the tool requires review by default"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional tool metadata"
    )


class HumanFeedback(BaseModel):
    """Human risk-review feedback for a planned tool call."""

    review_id: str = Field(description="Stable risk-review ID")
    status: RiskReviewStatus = Field(description="Risk-review state")
    tool_call: ToolCallPlan = Field(description="Tool call under review")
    reason: str | None = Field(
        default=None, description="Machine-readable review reason"
    )
    comment: str | None = Field(default=None, description="Reviewer comment")
    modified_arguments: dict[str, Any] | None = Field(
        default=None, description="Reviewer-supplied replacement arguments"
    )
    reviewer: str | None = Field(default=None, description="Reviewer identity")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Feedback creation timestamp",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional review metadata"
    )


class StepAttempt(BaseModel):
    """One concrete attempt to execute a planned tool call."""

    attempt_id: str = Field(description="Stable attempt ID")
    step_id: int = Field(description="TaskPlan step ID")
    tool_call: ToolCallPlan = Field(description="Planned tool call")
    observation: ToolObservation = Field(description="Normalized tool observation")
    status: AttemptStatus = Field(description="Attempt outcome")
    started_at: datetime = Field(description="Attempt start timestamp")
    finished_at: datetime = Field(description="Attempt finish timestamp")
    duration_ms: int = Field(description="Attempt duration in milliseconds")
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list, description="Artifacts recorded from observation"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional attempt metadata"
    )


class ExecutionTrace(BaseModel):
    """Replay-friendly trace of controlled step execution attempts."""

    trace_id: str = Field(description="Stable trace ID")
    task_id: str | None = Field(default=None, description="Optional TaskPlan ID")
    attempts: list[StepAttempt] = Field(
        default_factory=list, description="Recorded step attempts"
    )
    status: TraceStatus = Field(default="running", description="Trace status")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional trace metadata"
    )

    def append_attempt(self, attempt: StepAttempt) -> None:
        self.attempts.append(attempt)
        self.status = "success" if attempt.status == "success" else "failed"

    def latest_attempt(self) -> StepAttempt | None:
        if not self.attempts:
            return None
        return self.attempts[-1]


class EvaluationDecision(BaseModel):
    """Decision made after evaluating a step attempt observation."""

    outcome: EvaluationOutcome = Field(description="Evaluation outcome")
    reason: str = Field(description="Machine-readable evaluation reason")
    error_type: ErrorType | None = Field(
        default=None, description="Normalized error type"
    )
    message: str | None = Field(default=None, description="Human-readable message")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional evaluation metadata"
    )


class RepairAction(BaseModel):
    """Deterministic repair action for a failed step attempt."""

    action_type: RepairActionType = Field(description="Repair action type")
    reason: str = Field(description="Machine-readable repair reason")
    updated_arguments: dict[str, Any] = Field(
        default_factory=dict, description="Arguments to use for the next attempt"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional repair metadata"
    )


class LoopResult(BaseModel):
    """Result from a simplified execution/evaluate/repair loop."""

    status: LoopStatus = Field(description="Final loop status")
    trace: ExecutionTrace = Field(description="Execution trace produced by the loop")
    attempts_used: int = Field(description="Number of attempts recorded")
    decision: EvaluationDecision = Field(description="Final evaluation decision")
