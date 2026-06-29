import json
from types import SimpleNamespace

import pytest

from agent.nodes import planner_node as planner_module
from agent.nodes.planner_node import planner_node
from agent.state import create_initial_state


class FakeLLM:
    def __init__(self, content: str):
        self.content = content

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=self.content)


def _task_plan_json(expected_outputs: str, allowed_tools: list[str] | None = None) -> str:
    allowed_tools = allowed_tools or ["load_vector_layer"]
    return f"""
    {{
      "task_id": "task-planner-test",
      "task_summary": "Load a vector layer",
      "risk_level": "low",
      "review_status": "pending_review",
      "required_inputs": [
        {{"input_id": "input", "description": "Input vector", "path": "data/input.gpkg"}}
      ],
      "expected_outputs": {expected_outputs},
      "steps": [
        {{
          "step_id": 1,
          "description": "Load vector layer",
          "candidate_gdal_apis": [],
          "candidate_pyqgis_apis": ["QgsProject.addMapLayer"],
          "allowed_tools": {json.dumps(allowed_tools)},
          "input_refs": ["input"],
          "output_refs": ["layer"],
          "risk_level": "low",
          "requires_review": false
        }}
      ],
      "metadata": {{}}
    }}
    """


@pytest.mark.anyio
async def test_planner_node_does_not_return_approvable_draft_when_validation_fails(monkeypatch):
    monkeypatch.setattr(
        planner_module,
        "_get_llm",
        lambda _state: FakeLLM(_task_plan_json("[]", allowed_tools=["execute_processing"])),
    )
    monkeypatch.setattr(planner_module, "save_execution_log", lambda *args, **kwargs: None)

    state = create_initial_state("session-invalid-plan", "加载矢量图层")
    state["retry_count"] = 1

    update = await planner_node(state)

    assert update["draft"] is None
    assert update["task_plan_draft"] is None
    assert update["status"] is False
    assert update["plan_validation"].is_valid is False
    assert any(issue.code == "missing_output" for issue in update["plan_validation"].issues)


@pytest.mark.anyio
async def test_planner_node_sets_tool_selection_from_task_plan_allowed_tools(monkeypatch):
    monkeypatch.setattr(
        planner_module,
        "_get_llm",
        lambda _state: FakeLLM(
            _task_plan_json(
                '[{"output_id": "layer", "description": "Loaded layer", "path": "memory:layer"}]',
                allowed_tools=["load_vector_layer", "capture_map_canvas"],
            )
        ),
    )
    monkeypatch.setattr(planner_module, "save_execution_log", lambda *args, **kwargs: None)

    state = create_initial_state("session-valid-plan", "加载矢量图层")
    state["retry_count"] = 1

    update = await planner_node(state)

    assert update["draft"] is not None
    assert update["tool_selection"] == ["capture_map_canvas", "load_vector_layer"]
