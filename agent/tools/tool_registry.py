#!/usr/bin/env python3
"""Registry for MCP tool metadata and default risk boundaries."""

from typing import Any, Iterable

from agent.tools.tool_contracts import ToolSpec

HIGH_RISK_TOOLS = {"execute_code"}


class ToolRegistry:
    """In-memory registry of available MCP tools."""

    def __init__(self, specs: Iterable[ToolSpec] | None = None):
        self._tools: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    @classmethod
    def from_tool_specs(cls, specs: Iterable[ToolSpec]) -> "ToolRegistry":
        return cls(specs)

    @classmethod
    def from_mcp_tools(cls, tools: Iterable[dict[str, Any]]) -> "ToolRegistry":
        specs = [
            ToolSpec(
                name=str(tool.get("name", "")),
                description=str(tool.get("description", "") or ""),
            )
            for tool in tools
            if tool.get("name")
        ]
        return cls(specs)

    @classmethod
    def from_langchain_tools(cls, tools: Iterable[Any]) -> "ToolRegistry":
        specs = [
            ToolSpec(
                name=str(getattr(tool, "name")),
                description=str(getattr(tool, "description", "") or ""),
            )
            for tool in tools
            if getattr(tool, "name", None)
        ]
        return cls(specs)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in HIGH_RISK_TOOLS:
            spec = spec.model_copy(
                update={"risk_level": "high", "requires_review": True}
            )
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def tool_names(self) -> list[str]:
        return sorted(self._tools)
