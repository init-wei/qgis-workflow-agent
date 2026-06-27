from pathlib import Path

from agent.tools.tool_contracts import ToolCallPlan
from agent.tools.tool_policy import CodePolicy, PathPolicy, ToolPolicy
from agent.tools.tool_registry import ToolRegistry


def test_path_policy_allows_workspace_shared_output(tmp_path):
    workspace = tmp_path
    allowed_path = workspace / "shared" / "output" / "buffer.gpkg"

    decision = PathPolicy(workspace_root=workspace).evaluate_path(str(allowed_path))

    assert decision.allowed is True


def test_path_policy_rejects_path_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside" / "buffer.gpkg"

    decision = PathPolicy(workspace_root=workspace).evaluate_path(str(outside))

    assert decision.allowed is False
    assert decision.reason == "path_outside_workspace"


def test_path_policy_rejects_parent_escape(tmp_path):
    workspace = tmp_path / "workspace"

    decision = PathPolicy(workspace_root=workspace).evaluate_path(
        str(workspace / ".." / "outside.gpkg")
    )

    assert decision.allowed is False


def test_path_policy_rejects_nested_processing_output_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    call = ToolCallPlan(
        step_id=1,
        tool_name="execute_processing",
        arguments={
            "algorithm": "native:buffer",
            "parameters": {"OUTPUT": str(tmp_path / "outside" / "out.gpkg")},
        },
        risk_level="medium",
        requires_review=False,
    )

    decision = PathPolicy(workspace_root=workspace).evaluate_call(call)

    assert decision.allowed is False
    assert decision.reason == "path_outside_workspace"


def test_path_policy_rejects_windows_absolute_path_strings(tmp_path):
    decision = PathPolicy(workspace_root=tmp_path).evaluate_path("C:/bad/out.png")

    assert decision.allowed is False
    assert decision.reason == "path_outside_workspace"


def test_path_policy_detects_windows_absolute_paths_on_any_host(tmp_path):
    policy = PathPolicy(workspace_root=tmp_path)

    assert policy.is_absolute_path_string("C:/bad/out.png") is True
    assert policy.is_absolute_path_string("C:\\bad\\out.png") is True
    assert policy.is_absolute_path_string("shared/output/out.png") is False


def test_code_policy_rejects_execute_code_without_prior_approval():
    call = ToolCallPlan(
        step_id=1,
        tool_name="execute_code",
        arguments={"code": "print('hi')"},
        risk_level="high",
        requires_review=False,
    )

    decision = CodePolicy().evaluate(call, approved=False)

    assert decision.allowed is False
    assert decision.reason == "execute_code_requires_review"


def test_code_policy_allows_execute_code_only_when_reviewed_and_approved():
    call = ToolCallPlan(
        step_id=1,
        tool_name="execute_code",
        arguments={"code": "print('hi')"},
        risk_level="high",
        requires_review=True,
    )

    decision = CodePolicy().evaluate(call, approved=True)

    assert decision.allowed is True


def test_tool_policy_rejects_tool_not_in_allowed_scope(tmp_path):
    registry = ToolRegistry.from_mcp_tools([{"name": "ping"}, {"name": "remove_layer"}])
    call = ToolCallPlan(
        step_id=1,
        tool_name="remove_layer",
        arguments={},
        risk_level="medium",
        requires_review=False,
    )

    decision = ToolPolicy(registry=registry, workspace_root=tmp_path).evaluate(
        call, allowed_tools=["ping"], approved=False
    )

    assert decision.allowed is False
    assert decision.reason == "tool_not_allowed"


def test_tool_policy_allows_low_risk_registered_tool_with_safe_path(tmp_path):
    registry = ToolRegistry.from_mcp_tools([{"name": "capture_map_canvas"}])
    path = Path(tmp_path) / "shared" / "screenshots" / "map.png"
    call = ToolCallPlan(
        step_id=1,
        tool_name="capture_map_canvas",
        arguments={"path": str(path)},
        risk_level="low",
        requires_review=False,
    )

    decision = ToolPolicy(registry=registry, workspace_root=tmp_path).evaluate(
        call, allowed_tools=["capture_map_canvas"], approved=False
    )

    assert decision.allowed is True
