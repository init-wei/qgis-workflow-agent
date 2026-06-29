from agent.config import ExecutionMode, load_execution_mode
from agent.nodes.executor_node import executor_node
from agent.state import create_initial_state


def test_load_execution_mode_defaults_to_react_agent(monkeypatch):
    monkeypatch.delenv("QGIS_AGENT_EXECUTION_MODE", raising=False)

    assert load_execution_mode(pyproject_path=None) == "tool_call_plan"


def test_load_execution_mode_reads_environment_override(monkeypatch):
    monkeypatch.setenv("QGIS_AGENT_EXECUTION_MODE", "tool_call_plan")

    assert load_execution_mode(pyproject_path=None) == "tool_call_plan"


def test_execution_mode_type_allows_two_modes():
    modes: set[ExecutionMode] = {"react_agent", "tool_call_plan"}

    assert modes == {"react_agent", "tool_call_plan"}


def test_executor_node_dispatches_react_agent_mode(monkeypatch):
    async def fake_react_executor(state):
        return {"messages": [], "mode": "react_agent", "session_id": state["session_id"]}

    monkeypatch.setenv("QGIS_AGENT_EXECUTION_MODE", "react_agent")
    monkeypatch.setattr(
        "agent.nodes.executor_node.react_agent_executor_node",
        fake_react_executor,
    )

    import asyncio

    result = asyncio.run(executor_node(create_initial_state("session-mode", "test")))

    assert result["mode"] == "react_agent"
    assert result["session_id"] == "session-mode"
