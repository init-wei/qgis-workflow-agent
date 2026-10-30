#!/usr/bin/env python3
"""Policy gates for planned MCP tool calls."""

from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Iterator

from agent.tools.tool_contracts import PolicyDecision, ToolCallPlan
from agent.tools.tool_registry import ToolRegistry
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import (
    call_output_paths, new_output_path, new_project_output_path,
)

PATH_ARGUMENT_KEYS = {
    "path",
    "paths",
    "output",
    "outputs",
    "output_path",
    "output_paths",
    "output_file",
    "output_files",
    "filename",
}


class PathPolicy:
    """Restrict local file paths to the workspace."""

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()

    def evaluate_call(self, call: ToolCallPlan) -> PolicyDecision:
        for path in self._iter_path_arguments(call.arguments):
            decision = self.evaluate_path(path)
            if not decision.allowed:
                return decision
        for path in call_output_paths(call.tool_name, call.arguments):
            try:
                new_output_path(path, self.workspace_root)
            except ValueError as exc:
                return PolicyDecision(allowed=False, reason="unsafe_output_path", message=str(exc))
        if call.tool_name == "save_project":
            try:
                new_project_output_path(call.arguments.get("path"), self.workspace_root)
            except (TypeError, ValueError) as exc:
                return PolicyDecision(allowed=False, reason="unsafe_output_path", message=str(exc))
        return PolicyDecision(allowed=True)

    def evaluate_path(self, raw_path: str) -> PolicyDecision:
        candidate = Path(raw_path)
        if self.is_absolute_path_string(raw_path) and not candidate.is_absolute():
            return PolicyDecision(
                allowed=False,
                reason="path_outside_workspace",
                message=f"Path is outside workspace: {raw_path}",
            )
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

    def is_absolute_path_string(self, raw_path: str) -> bool:
        return Path(raw_path).is_absolute() or PureWindowsPath(raw_path).is_absolute()

    def _iter_path_arguments(self, value: Any, key: str | None = None) -> Iterator[str]:
        is_path_key = key is not None and key.lower() in PATH_ARGUMENT_KEYS
        if isinstance(value, str):
            if is_path_key:
                yield value
            return

        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                nested_key_text = str(nested_key)
                yield from self._iter_path_arguments(nested_value, nested_key_text)
            return

        if isinstance(value, list):
            for item in value:
                yield from self._iter_path_arguments(item, key)


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
        if call.tool_name == "save_project" and (
            call.risk_level != "high" or not call.requires_review or not approved
        ):
            return PolicyDecision(
                allowed=False,
                reason="high_risk_requires_review",
                message="Saving a project requires a high-risk declaration and explicit review approval.",
                requires_review=True,
            )
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
            self.risk_policy.evaluate(
                call if call.tool_name == "save_project" else normalized_call,
                approved=approved,
            ),
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
