#!/usr/bin/env python3
"""Phase 2 tool call, observation, registry, and policy contracts."""

from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]
ObservationStatus = Literal["success", "error", "malformed", "disconnected"]
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
    requires_review: bool = Field(
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
    requires_review: bool = Field(
        default=False, description="Whether a human approval gate is required"
    )


class ToolSpec(BaseModel):
    """Registry entry for an MCP tool."""

    name: str = Field(description="MCP tool name")
    description: str = Field(default="", description="Tool description")
    risk_level: RiskLevel = Field(default="low", description="Default risk level")
    requires_review: bool = Field(
        default=False, description="Whether the tool requires review by default"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional tool metadata"
    )
