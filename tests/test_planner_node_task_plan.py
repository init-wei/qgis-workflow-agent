import json
from types import SimpleNamespace

import pytest

from agent.nodes import planner_node as planner_module
from agent.nodes.planner_node import bind_task_outputs, planner_node
from agent.planning.plan_schema import OutputSpec, TaskPlan
from agent.state import create_initial_state


class FakeLLM:
    def __init__(self, content: str):
        self.content = content

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=self.content)


def _task_plan_json(allowed_tools: list[str] | None = None, *, include_output=False) -> str:
    allowed_tools = allowed_tools or ["add_layer"]
    output = (
        '[{"output_id":"output","description":"Layer file","path":"shared/outputs/result.gpkg"}]'
        if include_output
        else "[]"
    )
    tool_name = allowed_tools[0]
    arguments = (
        '{"algorithm":"native:buffer","parameters":{"INPUT":"shared/uploads/model-path.gpkg","OUTPUT":"shared/outputs/result.gpkg"}}'
        if tool_name == "execute_processing"
        else '{"path":"model-supplied-path","layer_type":"vector","name":"model-name"}'
    )
    output_ref = '["output"]' if include_output else "[]"
    return f"""
    {{
      "task_id": "model-task-id",
      "task_summary": "Load a vector layer",
      "risk_level": "low",
      "review_status": "approved",
      "required_inputs": [
        {{"input_id":"input","description":"Input vector","file_id":"file-1","path":"model-path","source":"model-source"}}
      ],
      "expected_outputs": {output},
      "steps": [
        {{"step_id":1,"description":"Load vector layer","candidate_gdal_apis":[],"candidate_pyqgis_apis":["QgsProject.addMapLayer"],"allowed_tools":{json.dumps(allowed_tools)},"input_refs":["input"],"output_refs":{output_ref},"risk_level":"low","requires_review":false}}
      ],
      "tool_calls": [
        {{"step_id":1,"tool_name":"{tool_name}","arguments":{arguments},"risk_level":"low","requires_review":false}}
      ],
      "metadata": {{}}
    }}
    """


def _state(session_id: str):
    selected_file = {
        "file_id": "file-1",
        "filename": "input.gpkg",
        "qgis_path": "shared/uploads/file-1.gpkg|layername=input",
        "layer_name": "input",
        "layer_info": {"crs": "EPSG:4326", "geometry": "Point"},
    }
    return create_initial_state(
        session_id,
        "Load the selected vector layer",
        uploaded_files=[selected_file],
    )


@pytest.mark.anyio
async def test_planner_does_not_return_an_approvable_plan_when_validation_fails(monkeypatch):
    monkeypatch.setattr(
        planner_module,
        "_get_llm",
        lambda _state: FakeLLM(
            _task_plan_json(allowed_tools=["execute_processing"], include_output=False)
        ),
    )

    state = _state("session-invalid-plan")
    state["retry_count"] = 1
    update = await planner_node(state)

    assert update["task_plan"] is None
    assert update["planning_error"]
    assert update["retry_count"] == 2
    assert update["plan_validation"].is_valid is False
    assert any(issue.code == "missing_output" for issue in update["plan_validation"].issues)


@pytest.mark.anyio
async def test_planner_binds_selected_file_and_returns_one_pending_task_plan(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "shared/uploads/file-1.gpkg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"abc")
    proposal = json.loads(_task_plan_json())
    proposal["required_inputs"][0]["sha256"] = "0" * 64
    monkeypatch.setattr(
        planner_module,
        "_get_llm",
        lambda _state: FakeLLM(json.dumps(proposal)),
    )

    state = _state("session-valid-plan")
    state["uploaded_files"][0]["sha256"] = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    update = await planner_node(state)

    plan = update["task_plan"]
    assert plan is not None
    assert plan.task_id == state["task_id"]
    assert plan.revision == 1
    assert plan.review_status == "pending_review"
    assert plan.required_inputs[0].file_id == "file-1"
    assert plan.required_inputs[0].path == "shared/uploads/file-1.gpkg|layername=input"
    assert plan.required_inputs[0].source == "input.gpkg"
    assert plan.required_inputs[0].sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert plan.tool_calls[0].arguments == {
        "path": "shared/uploads/file-1.gpkg|layername=input",
        "layer_type": "vector",
        "name": "model-name",
    }
    assert update["plan_validation"].is_valid is True
    assert update["retry_count"] == 1
    assert "draft" not in update
    assert "plan" not in update


@pytest.mark.parametrize("output_id", ["../escape", "/absolute", "bad/name", "", ".."])
def test_output_identity_cannot_escape_its_artifact_directory(output_id):
    plan = TaskPlan(task_id="task-binding", task_summary="Capture", risk_level="low",
                    review_status="pending_review", steps=[],
                    expected_outputs=[OutputSpec(output_id=output_id, description="Image", path="map.png")])

    with pytest.raises(ValueError, match="Output ID"):
        bind_task_outputs(plan)
