#!/usr/bin/env python3
"""Policy gates for planned MCP tool calls."""

from pathlib import Path
from typing import Iterable

from agent.tools.tool_contracts import PolicyDecision, ToolCallPlan
from agent.tools.tool_registry import ToolRegistry

PATH_ARGUMENT_KEYS = {"path", "output", "output_path", "output_file", "filename"}


class PathPolicy:
    """Restrict local file paths to the workspace."""

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()

    def evaluate_call(self, call: ToolCallPlan) -> PolicyDecision:
        for key, value in call.arguments.items():
            if key in PATH_ARGUMENT_KEYS and isinstance(value, str):
                decision = self.evaluate_path(value)
                if not decision.allowed:
                    return decision
        return PolicyDecision(allowed=True)

    def evaluate_path(self, raw_path: str) -> PolicyDecision:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve()

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            return PolicyDecision(
                allowed=False,
                reason="path_outside_workspace",
                message=f"Path is outside workspace: {raw_path}",
            )

        return PolicyDecision(allowed=True)


class CodePolicy:
    """Policy for arbitrary code execution tools."""

    def evaluate(self, call: ToolCallPlan, approved: bool = False) -> PolicyDecision:
        if call.tool_name != "execute_code":
            return PolicyDecision(allowed=True)
        if not call.requires_review or not approved:
            return PolicyDecision(
                allowed=False,
                reason="execute_code_requires_review",
                message="execute_code requires explicit review approval.",
                requires_review=True,
            )
        return PolicyDecision(allowed=True)


class RiskPolicy:
    """Policy for high-risk non-code tool calls."""

    def evaluate(self, call: ToolCallPlan, approved: bool = False) -> PolicyDecision:
        if call.risk_level == "high" and (not call.requires_review or not approved):
            return PolicyDecision(
                allowed=False,
                reason="high_risk_requires_review",
                message="High-risk tool calls require review approval.",
                requires_review=True,
            )
        return PolicyDecision(allowed=True)


class ToolPolicy:
    """Combined registry, scope, path, code, and risk policy."""

    def __init__(self, registry: ToolRegistry, workspace_root: str | Path):
        self.registry = registry
        self.path_policy = PathPolicy(workspace_root)
        self.code_policy = CodePolicy()
        self.risk_policy = RiskPolicy()

    def evaluate(
        self,
        call: ToolCallPlan,
        allowed_tools: Iterable[str] | None = None,
        approved: bool = False,
    ) -> PolicyDecision:
        spec = self.registry.get(call.tool_name)
        if spec is None:
            return PolicyDecision(
                allowed=False,
                reason="tool_not_registered",
                message=f"Tool is not registered: {call.tool_name}",
            )

        if allowed_tools is not None and call.tool_name not in set(allowed_tools):
            return PolicyDecision(
                allowed=False,
                reason="tool_not_allowed",
                message=f"Tool is outside allowed scope: {call.tool_name}",
            )

        normalized_call = self._merge_registry_defaults(
            call, spec.risk_level, spec.requires_review
        )
        for decision in (
            self.path_policy.evaluate_call(normalized_call),
            self.code_policy.evaluate(normalized_call, approved=approved),
            self.risk_policy.evaluate(normalized_call, approved=approved),
        ):
            if not decision.allowed:
                return decision

        return PolicyDecision(allowed=True)

    def _merge_registry_defaults(
        self,
        call: ToolCallPlan,
        spec_risk_level: str,
        spec_requires_review: bool,
    ) -> ToolCallPlan:
        risk_level = (
            "high" if "high" in {call.risk_level, spec_risk_level} else call.risk_level
        )
        requires_review = call.requires_review or spec_requires_review
        return call.model_copy(
            update={"risk_level": risk_level, "requires_review": requires_review}
        )
