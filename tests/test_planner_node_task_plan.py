import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import executor_node as executor_module
from agent.nodes import planner_node as planner_module
from agent.nodes.planner_node import bind_task_outputs, planner_node
from agent.planning.plan_schema import OutputSpec, TaskPlan
from agent.state import create_initial_state
from conftest import fake_spatial_inspector
import ui.backend.main as backend


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
    update = await planner_node(state, processing_inspector=lambda _: {
        'status': 'success', 'result': {'processing_capabilities': []},
    })

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
    update = await planner_node(state, processing_inspector=lambda _: {
        'status': 'success', 'result': {'processing_capabilities': []},
    }, spatial_inspector=fake_spatial_inspector)

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


@pytest.mark.parametrize('repair', [True, False])
def test_planning_retry_receives_validation_feedback_and_still_requires_review(monkeypatch, tmp_path, repair):
    """A rejected model proposal is fed back once, never executed or auto-approved."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'source.geojson'
    source.write_text('{"type":"FeatureCollection","features":[]}')
    file_record = {
        'file_id': 'source', 'filename': source.name, 'storage_path': source.name,
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'layers': [{'layer_name': 'source', 'crs': 'EPSG:32650', 'geometry': 'Polygon',
                    'fields': ['value'], 'field_types': {'value': 'Real'}, 'is_2d': True}],
    }
    prompts, gis_calls = [], []

    class FeedbackModel:
        async def ainvoke(self, messages):
            prompts.append([message.content for message in messages])
            parameters = {'FIELD_NAME': 'value', 'OUTPUT': 'statistics.gpkg'}
            if repair and 'call_input_reference_mismatch' in messages[-1].content:
                parameters['INPUT_LAYER'] = 'model-source'
            return SimpleNamespace(content=json.dumps({
                'task_id': 'model', 'task_summary': 'Statistics', 'risk_level': 'low',
                'review_status': 'approved',
                'required_inputs': [{'input_id': 'source', 'description': 'Source', 'file_id': 'source',
                                     'path': 'model-source'}],
                'expected_outputs': [{'output_id': 'statistics', 'description': 'Statistics',
                                      'path': 'statistics.gpkg'}],
                'steps': [{'step_id': 1, 'description': 'Statistics', 'allowed_tools': ['execute_processing'],
                           'input_refs': ['source'], 'output_refs': ['statistics'],
                           'risk_level': 'low', 'requires_review': False}],
                'tool_calls': [{'step_id': 1, 'tool_name': 'execute_processing', 'arguments': {
                    'algorithm': 'native:basicstatisticsforfields', 'parameters': parameters}}],
            }))

    def processing_inspector(request):
        if request == {'capabilities': True}:
            return {'status': 'success', 'result': {'processing_capabilities': []}}
        return {'status': 'success', 'result': {'processing_validation': [{
            'step_id': call['step_id'], 'valid': True,
            'source_paths': [call['parameters']['INPUT_LAYER']],
            'output_paths': [call['parameters']['OUTPUT']],
        } for call in request]}}

    async def get_gis():
        gis_calls.append('connected')
        raise AssertionError('No GIS connection is allowed before approval')

    model = FeedbackModel()
    graph = build_graph(MemorySaver(), processing_inspector=processing_inspector,
                        spatial_inspector=fake_spatial_inspector)

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(backend, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(backend, 'get_uploaded_file_record', lambda _: file_record)
    monkeypatch.setattr(backend, 'save_task_execution_record', lambda **_: None)
    monkeypatch.setattr(planner_module, '_get_llm', lambda _: model)
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    response = TestClient(backend.app).post('/api/chat/stream', json={
        'thread_id': 'feedback', 'message': 'Calculate value statistics', 'files': ['source'],
    })

    assert len(prompts) == 2
    assert 'call_input_reference_mismatch' not in prompts[0][-1]
    assert 'call_input_reference_mismatch' in prompts[1][-1]
    assert 'Step 1 must consume exactly its declared input references.' in prompts[1][-1]
    assert gis_calls == []
    assert not (tmp_path / 'shared' / 'outputs').exists()
    if repair:
        assert 'event: human_review_required' in response.text
        for block in response.text.strip().split('\n\n'):
            if block.startswith('event: human_review_required\n'):
                plan = json.loads(block.splitlines()[1].removeprefix('data: '))['task_plan']
                break
        assert plan['review_status'] == 'pending_review'
        assert plan['tool_calls'][0]['arguments']['parameters']['INPUT_LAYER'] == str(source)
    else:
        assert 'event: error' in response.text
        assert 'event: human_review_required' not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(('retry_count', 'has_feedback'), [(1, True), (0, False)])
async def test_planning_retry_preserves_human_revision_advice(monkeypatch, tmp_path, retry_count, has_feedback):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'shared/uploads/file-1.gpkg'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'abc')
    captured = []

    class RevisionModel:
        async def ainvoke(self, messages):
            captured.extend(message.content for message in messages)
            return SimpleNamespace(content=_task_plan_json())

    monkeypatch.setattr(planner_module, '_get_llm', lambda _: RevisionModel())
    state = _state('revision-feedback')
    state.update(retry_count=retry_count, planning_revision=2, advise='请保留图层名称',
                 planning_error='Step 1 must consume exactly its declared input references.')
    update = await planner_node(state, processing_inspector=lambda _: {
        'status': 'success', 'result': {'processing_capabilities': []},
    }, spatial_inspector=fake_spatial_inspector)

    assert update['task_plan'].review_status == 'pending_review'
    assert update['task_plan'].revision == 2
    assert '请保留图层名称' in captured[-1]
    assert ('Step 1 must consume exactly its declared input references.' in captured[-1]) is has_feedback
    assert update['retry_count'] == retry_count + 1


@pytest.mark.parametrize("output_id", ["../escape", "/absolute", "bad/name", "", ".."])
def test_output_identity_cannot_escape_its_artifact_directory(output_id):
    plan = TaskPlan(task_id="task-binding", task_summary="Capture", risk_level="low",
                    review_status="pending_review", steps=[],
                    expected_outputs=[OutputSpec(output_id=output_id, description="Image", path="map.png")])

    with pytest.raises(ValueError, match="Output ID"):
        bind_task_outputs(plan)
