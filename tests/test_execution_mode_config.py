from typing import get_args

import pytest

from agent.config import ExecutionMode, load_execution_mode


def test_load_execution_mode_defaults_to_tool_call_plan(monkeypatch):
    monkeypatch.delenv("QGIS_AGENT_EXECUTION_MODE", raising=False)

    assert load_execution_mode(pyproject_path=None) == "tool_call_plan"


def test_load_execution_mode_reads_environment_override(monkeypatch):
    monkeypatch.setenv("QGIS_AGENT_EXECUTION_MODE", "tool_call_plan")

    assert load_execution_mode(pyproject_path=None) == "tool_call_plan"


def test_execution_mode_type_only_allows_controlled_executor():
    assert set(get_args(ExecutionMode)) == {"tool_call_plan"}


def test_load_execution_mode_rejects_removed_react_agent_mode(monkeypatch):
    monkeypatch.setenv("QGIS_AGENT_EXECUTION_MODE", "react_agent")

    with pytest.raises(ValueError, match="tool_call_plan"):
        load_execution_mode(pyproject_path=None)
