from types import SimpleNamespace

from agent.tools.tool_contracts import ToolSpec
from agent.tools.tool_registry import ToolRegistry


def test_registry_marks_execute_code_high_risk_and_review_required():
    registry = ToolRegistry.from_tool_specs(
        [ToolSpec(name="execute_code", description="Run PyQGIS code")]
    )

    spec = registry.get("execute_code")

    assert spec is not None
    assert spec.risk_level == "high"
    assert spec.requires_review is True


def test_registry_can_build_from_mcp_list_tools_shape():
    registry = ToolRegistry.from_mcp_tools(
        [
            {"name": "ping", "description": "Ping QGIS"},
            {"name": "capture_map_canvas", "description": "Capture screenshot"},
        ]
    )

    assert registry.has_tool("ping") is True
    assert registry.get("capture_map_canvas").risk_level == "low"


def test_registry_can_build_from_langchain_tools():
    registry = ToolRegistry.from_langchain_tools(
        [
            SimpleNamespace(name="add_layer", description="Add layer"),
            SimpleNamespace(name="execute_code", description="Execute code"),
        ]
    )

    assert sorted(registry.tool_names()) == ["add_layer", "execute_code"]
    assert registry.get("execute_code").requires_review is True
