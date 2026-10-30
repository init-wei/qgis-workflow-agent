"""T03 behavior at the HTTP and external GIS adapter seams."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import planner_node as planner_module
from agent.planning.plan_schema import TaskPlan
from conftest import fake_spatial_inspector
import ui.backend.main as backend


def event(response, name):
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0] == f"event: {name}":
            return json.loads(lines[1].removeprefix("data: "))
    raise AssertionError(f"{name} not found in {response.text}")


def test_planning_receives_installed_capabilities_and_selected_layer_metadata(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "points.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}')
    record = {
        "file_id": "file-1", "filename": source.name, "storage_path": source.name,
        "layers": [{"layer_name": "points", "crs": "EPSG:4326", "geometry": "Point",
                    "feature_count": 3, "is_2d": True}],
    }
    inspector_requests = []

    def processing_inspector(request):
        inspector_requests.append(request)
        return {"status": "success", "result": {
            "qgis_version": "test-QGIS",
            "processing_capabilities": [{"algorithm": "native:buffer", "available": True,
                                         "parameters": [{"name": "DISTANCE", "type": "distance"}]}],
        }}

    class Model:
        messages = []

        async def ainvoke(self, messages):
            self.messages.append(messages)
            return SimpleNamespace(content=json.dumps({
                "task_id": "model-id", "task_summary": "Load", "risk_level": "low",
                "review_status": "pending_review", "required_inputs": [{
                    "input_id": "input", "description": "Points", "file_id": "file-1",
                    "path": str(source),
                }], "expected_outputs": [], "steps": [{
                    "step_id": 1, "description": "Load points", "allowed_tools": ["add_layer"],
                    "input_refs": ["input"], "output_refs": [], "risk_level": "low", "requires_review": False,
                }], "tool_calls": [{"step_id": 1, "tool_name": "add_layer", "arguments": {
                    "path": str(source), "layer_type": "vector"}}],
            }))

    model = Model()
    graph = build_graph(MemorySaver(), processing_inspector=processing_inspector)

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: record)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: model)

    response = TestClient(backend.app).post("/api/chat/stream", json={
        "thread_id": "facts", "message": "Load the selected layer", "files": ["file-1"],
    })
    plan = event(response, "human_review_required")["task_plan"]
    assert plan["review_status"] == "pending_review"
    prompt = "\n".join(str(message.content) for message in model.messages[0])
    assert "test-QGIS" in prompt and "native:buffer" in prompt and "DISTANCE" in prompt
    assert "EPSG:4326" in prompt and "feature_count" in prompt
    schema = TaskPlan.model_json_schema()
    assert json.dumps(schema, ensure_ascii=False) in prompt
    assert schema['$defs']['OutputSpec']['additionalProperties'] is False
    assert set(schema['$defs']['OutputSpec']['properties']) == {'output_id', 'description', 'path'}
    assert inspector_requests == [{"capabilities": True}]


@pytest.mark.parametrize(('mode', 'error', 'writes'), [
    ('coordinate', 'coordinate', 1),
    ('crs', 'crs', 1),
    ('radius', 'radius', 2),
    ('tampered', 'changed', 1),
    ('unavailable', 'unavailable', 1),
    ('valid', None, 3),
    ('project', None, 4),
    ('project_sidecar', 'needs attention', 3),
    ('english_layer', None, 3),
    ('english_each_feature', None, 3),
    ('repeated_distance_name', None, 3),
    ('conflicting_distance_name', '距离', 0),
    ('distance_choices', '距离', 0),
    ('distance_choices_comma', '距离', 0),
    ('distance_choices_parenthesis', '距离', 0),
    ('distance_range', '距离', 0),
    ('omitted_repeated_buffer', '距离', 0),
    ('omitted_repeated_buffer_english', '距离', 0),
    ('named_omitted_chinese', '距离', 0),
    ('spaced_name_omitted_chinese', '距离', 0),
    ('named_omitted_english', '距离', 0),
    ('unnamed_quotes_omitted_chinese', '距离', 0),
    ('unnamed_quotes_omitted_english', '距离', 0),
    ('chinese_layer', None, 3),
    ('scale_distance', '单位', 0),
    ('scale_distance_english', '单位', 0),
    ('square_meters', '单位', 0),
    ('square_unicode', '单位', 0),
    ('square_words', '单位', 0),
    ('square_multiplication', '单位', 0),
    ('square_chinese', '单位', 0),
    ('swapped_buffers', '距离', 0),
    ('ordered_buffers', None, -2),
    ('repeated_buffers', None, -2),
    ('named_repeated_chinese', None, -2),
    ('spaced_name_repeated_chinese', None, -2),
    ('named_repeated_english', None, -2),
    ('unnamed_quotes_repeated_chinese', None, -2),
    ('unnamed_quotes_repeated_english', None, -2),
    ('preflight_unavailable', 'unavailable', 0),
    ('original_wal', 'sqlite', 0),
    ('produced_wal', 'sqlite', 1),
    ('validation_wal', 'sqlite', 1),
    ('consumption_wal', 'sqlite', 1),
    ('unspecified_unit', '单位', 0),
    ('missing_unit_field', 'unit', 0),
    ('unit_in_filename', '单位', 0),
    ('buffer_in_filename', '单位', 0),
    ('buffer_in_chinese_filename', '单位', 0),
    ('chinese_compact', None, 3),
    ('chinese_output_filename', None, 3),
    ('unit_in_crs', '单位', 0),
    ('degrees', '度', 0),
    ('wrong_distance', '距离', 0),
    ('kilometres', None, 3),
    ('advice', None, -1),
])
def test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, mode, error, writes):
    """A successful GIS call must not authorize a downstream operation when its data is wrong."""
    import hashlib
    from pathlib import Path
    from agent.nodes import executor_node as executor_module
    from agent.tools import execution_guard

    monkeypatch.chdir(tmp_path)
    source = tmp_path / ("points.gpkg" if mode == "original_wal" else "points.geojson")
    source.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"id": 1},
        "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
    }]}))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    record = {
        "file_id": "file-1", "filename": source.name, "storage_path": source.name, "sha256": before,
        "layers": [{"layer_name": "points", "crs": "EPSG:4326", "geometry": "Point",
                    "feature_count": 1, "is_2d": True}],
    }
    records, calls, inspections, mcp_requests, mcp_connections = {}, [], [], [], []
    tampered = False

    def processing_result(requests):
        return {"status": "success", "result": {"processing_validation": [{
            "step_id": request["step_id"], "valid": True,
            "source_paths": [request["parameters"]["INPUT"]],
            "output_paths": [request["parameters"]["OUTPUT"]],
        } for request in requests]}}

    def processing_inspector(request):
        if request == {"capabilities": True}:
            return {"status": "success", "result": {"qgis_version": "test",
                                                       "processing_capabilities": []}}
        return processing_result(request)

    def spatial_inspector(request):
        inspections.append(request)
        if request["mode"] == "preflight":
            if mode == 'preflight_unavailable' and len(inspections) == 2:
                raise OSError('inspector disappeared after approval')
            return fake_spatial_inspector(request)
        if mode == 'unavailable':
            raise OSError('External inspector unavailable')
        if request['call']['tool_name'] == 'add_layer':
            assert request['observation']['feature_count'] == 1
            assert request['observation']['source'] == next(iter(request['input_paths'].values()))
            return {'status': 'passed', 'checks': ['loaded_feature_count'], 'errors': [],
                    'facts': {'statistics': {'kind': 'feature_count', 'feature_count': 1}}}
        output = Path(next(iter(request["output_paths"].values())))
        if mode == 'validation_wal':
            Path(str(output) + '-wal').write_bytes(b'active transaction')
        content = json.loads(output.read_text())
        geometry = content["features"][0]["geometry"]
        errors = []
        if content['crs']['properties']['name'] != 'EPSG:32650':
            errors.append('Output CRS is wrong')
        if geometry['type'] == 'Point' and geometry['coordinates'] != [450000, 4410000]:
            errors.append('Reprojected coordinate differs from expected location')
        if geometry['type'] == 'Polygon' and geometry['coordinates'][0][0][0] != 449900:
            errors.append('Buffer radius differs from 100 meters')
        check = 'target_crs' if geometry['type'] == 'Point' else 'buffer_geometry_and_attributes'
        return {"status": "failed" if errors else "passed", "checks": [{"check": check}],
                "errors": errors, "facts": {"output": {"crs": "EPSG:32650"}}}

    class Model:
        async def ainvoke(self, messages):
            distance = 200 if mode == 'advice' and '改成200米' in messages[-1].content else 100
            return SimpleNamespace(content=json.dumps({
                "task_id": "model-id", "task_summary": "Reproject and buffer", "risk_level": "low",
                "review_status": "pending_review", "required_inputs": [{
                    "input_id": "source", "description": "Points", "file_id": "file-1", "path": "input"}],
                "expected_outputs": [
                    {"output_id": "projected", "description": "Projected", "path": "projected.geojson"},
                    {"output_id": "buffered", "description": "Buffered", "path": "buffered.geojson"}],
                "steps": [
                    {"step_id": 1, "description": "Reproject", "allowed_tools": ["execute_processing"],
                     "input_refs": ["source"], "output_refs": ["projected"], "risk_level": "low", "requires_review": False},
                    {"step_id": 2, "description": "Buffer", "allowed_tools": ["execute_processing"],
                     "input_refs": ["projected"], "output_refs": ["buffered"], "risk_level": "low", "requires_review": False},
                    {"step_id": 3, "description": "View", "allowed_tools": ["add_layer"],
                     "input_refs": ["buffered"], "output_refs": [], "risk_level": "low", "requires_review": False}],
                "tool_calls": [
                    {"step_id": 1, "tool_name": "execute_processing", "arguments": {
                        "algorithm": "native:reprojectlayer", "parameters": {
                            "INPUT": "input", "TARGET_CRS": "EPSG:32650", "OUTPUT": "projected.geojson"}}},
                    {"step_id": 2, "tool_name": "execute_processing", "arguments": {
                        "algorithm": "native:buffer", "parameters": {
                            "INPUT": "projected.geojson", "DISTANCE": distance, "SEGMENTS": 8,
                            "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2,
                            "DISSOLVE": False, "SEPARATE_DISJOINT": False, "OUTPUT": "buffered.geojson"}},
                     "distance_unit": None if mode == 'missing_unit_field' else 'm'},
                    {"step_id": 3, "tool_name": "add_layer", "arguments": {
                        "path": "buffered.geojson", "layer_type": "vector", "name": "buffer"}}],
            }))

    class GIS:
        async def list_tools(self):
            return [{"name": name} for name in ["get_qgis_info", "execute_processing", "add_layer", "save_project"]]

        async def call_tool(self, name, args):
            mcp_requests.append((name, args))
            if name == "get_qgis_info":
                return processing_result(args["processing_requests"])
            calls.append((name, args))
            if name == "execute_processing":
                path = Path(args["parameters"]["OUTPUT"])
                path.parent.mkdir(parents=True)
                if args['algorithm'] == 'native:reprojectlayer':
                    geometry = {'type': 'Point', 'coordinates': [0, 0] if mode == 'coordinate'
                                else [450000, 4410000]}
                else:
                    extent = 50 if mode == 'radius' else 100
                    geometry = {'type': 'Polygon', 'coordinates': [[
                        [450000 - extent, 4410000 - extent], [450000 + extent, 4410000 - extent],
                        [450000 + extent, 4410000 + extent], [450000 - extent, 4410000 + extent],
                        [450000 - extent, 4410000 - extent],
                    ]]}
                path.write_text(json.dumps({"type": "FeatureCollection", "crs": {
                    "type": "name", "properties": {"name": "EPSG:4326" if mode == 'crs'
                                                     else "EPSG:32650"}}, "features": [{
                    "type": "Feature", "properties": {"id": 1},
                    "geometry": geometry}]}))
                if mode == 'produced_wal':
                    Path(str(path) + '-wal').write_bytes(b'active transaction')
                return {"status": "success", "result": {"algorithm": args["algorithm"],
                                                           "result": {"OUTPUT": str(path)}}}
            if name == 'save_project':
                from zipfile import ZipFile
                path = Path(args['path'])
                path.parent.mkdir(parents=True)
                with ZipFile(path, 'w') as archive:
                    archive.writestr('project.qgs', '<qgis/>')
                return {'status': 'success', 'result': {'success': True, 'path': str(path), 'layer_count': 1}}
            return {"status": "success", "result": {"id": "buffer-layer", "source": args['path'],
                                                        "feature_count": 1, "crs": "EPSG:32650",
                                                        "geometry": "Polygon", "fields": ["id"]}}

    if mode in {'project', 'project_sidecar'}:
        original_invoke = Model.ainvoke

        async def project_invoke(self, messages):
            response = await original_invoke(self, messages)
            plan = json.loads(response.content.replace('projected.geojson', 'projected.gpkg').replace('buffered.geojson', 'buffered.gpkg'))
            plan['expected_outputs'].append({'output_id': 'project', 'description': 'Project copy', 'path': 'project.qgz'})
            plan['steps'].append({'step_id': 4, 'description': 'Save project', 'allowed_tools': ['save_project'],
                                  'input_refs': [], 'output_refs': ['project'], 'risk_level': 'high', 'requires_review': True})
            plan['tool_calls'].append({'step_id': 4, 'tool_name': 'save_project', 'arguments': {'path': 'project.qgz'},
                                       'risk_level': 'high', 'requires_review': True})
            response.content = json.dumps(plan)
            return response

        Model.ainvoke = project_invoke

    if mode.endswith('_wal'):
        original_invoke = Model.ainvoke

        async def gpkg_invoke(self, messages):
            response = await original_invoke(self, messages)
            response.content = response.content.replace('projected.geojson', 'projected.gpkg').replace('buffered.geojson', 'buffered.gpkg')
            return response

        Model.ainvoke = gpkg_invoke

    if mode in {'swapped_buffers', 'ordered_buffers', 'repeated_buffers', 'named_repeated_chinese',
                'named_repeated_english', 'unnamed_quotes_repeated_chinese', 'unnamed_quotes_repeated_english',
                'spaced_name_repeated_chinese'}:
        original_invoke = Model.ainvoke

        async def two_buffers(self, messages):
            response = await original_invoke(self, messages)
            plan = json.loads(response.content)
            plan['tool_calls'][1]['arguments']['parameters']['DISTANCE'] = 200 if mode == 'swapped_buffers' else 100
            second = json.loads(json.dumps(plan['tool_calls'][1]))
            second.update(step_id=3)
            second['arguments']['parameters'].update(INPUT='buffered.geojson', OUTPUT='second.geojson',
                                                      DISTANCE=200 if mode == 'ordered_buffers' else 100)
            plan['expected_outputs'].append({'output_id': 'second', 'description': 'Second buffer', 'path': 'second.geojson'})
            plan['steps'][2].update(step_id=4, input_refs=['second'])
            plan['tool_calls'][2].update(step_id=4, arguments={'path': 'second.geojson', 'layer_type': 'vector'})
            plan['steps'].insert(2, {'step_id': 3, 'description': 'Second buffer', 'allowed_tools': ['execute_processing'],
                                     'input_refs': ['buffered'], 'output_refs': ['second'], 'risk_level': 'low', 'requires_review': False})
            plan['tool_calls'].insert(2, second)
            response.content = json.dumps(plan)
            return response

        Model.ainvoke = two_buffers

    graph = build_graph(MemorySaver(), processing_inspector=processing_inspector,
                        spatial_inspector=spatial_inspector)

    async def get_graph():
        return graph

    async def get_gis():
        mcp_connections.append(True)
        return GIS()

    def save_record(**entry):
        nonlocal tampered
        records[entry["task_id"]] = {"confirmed": None, "summary": None, **entry}
        if mode in {'tampered', 'consumption_wal'} and not tampered and entry['status'] == 'running' and not entry.get('inflight_call'):
            for attempt in entry['attempts']:
                for artifact in attempt['artifacts']:
                    if artifact.get('spatial_validation', {}).get('status') == 'passed':
                        path = Path(artifact['path'])
                        if mode == 'consumption_wal':
                            Path(str(path) + '-wal').write_bytes(b'active transaction')
                            tampered = True
                            continue
                        data = json.loads(path.read_text())
                        data['features'][0]['geometry']['coordinates'] = [0, 0]
                        path.write_text(json.dumps(data))
                        tampered = True

    def get_record(task_id):
        entry = records[task_id]
        from agent.tools.execution_guard import is_task_active
        if entry['status'] == 'running' and not is_task_active(task_id):
            entry['status'] = 'needs_attention'
        return {"execution": entry, "attempts": entry["attempts"], "artifacts": [
            artifact for attempt in entry["attempts"] for artifact in attempt["artifacts"]]}

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: record)
    monkeypatch.setattr(backend, "save_task_execution_record", save_record)
    monkeypatch.setattr(backend, "get_task_execution_record", get_record)
    monkeypatch.setattr(backend, 'list_task_executions_for_session', lambda session_id: list(records.values()))
    monkeypatch.setattr(executor_module, "save_task_execution_record", save_record)
    monkeypatch.setattr(executor_module, "get_mcp_client", get_gis)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: Model())
    execution_guard._unknown_tasks.clear()
    client = TestClient(backend.app)
    requests = {
        'unspecified_unit': 'Reproject, buffer 100, and view',
        'unit_in_filename': 'Use grid_100m.geojson, reproject, buffer 100, and view',
        'buffer_in_filename': 'Use buffer100m.geojson, reproject, buffer, and view',
        'buffer_in_chinese_filename': '使用100米缓冲.geojson，先重投影，再缓冲，最后查看',
        'chinese_compact': '先重投影，再做100米缓冲，最后查看',
        'chinese_output_filename': '先重投影到 EPSG:32650，再缓冲100米并保存为out.gpkg，最后查看',
        'unit_in_crs': 'Use a 100m projection (EPSG:32650), reproject, buffer 100, and view',
        'degrees': 'Reproject, buffer 100 degrees, and view',
        'wrong_distance': 'Reproject, buffer 200 meters, and view',
        'kilometres': 'Reproject, buffer 0.1 kilometres, and view',
        'english_layer': 'Please reproject to EPSG:32650, then buffer the selected layer by 100 meters and display it',
        'english_each_feature': 'Reproject, buffer each feature by 100 meters, and view',
        'repeated_distance_name': '请重投影到 EPSG:32650，缓冲100米，仅加载最终结果，名称为“T03 验证后100米缓冲”。',
        'conflicting_distance_name': '请重投影到 EPSG:32650，缓冲100米，仅加载最终结果，名称为“T03 验证后200米缓冲”。',
        'distance_choices': 'Reproject, buffer 100 meters or 200 meters, and view',
        'distance_choices_comma': 'Reproject, buffer 100 meters, or 200 meters, and view',
        'distance_choices_parenthesis': 'Reproject, buffer 100 meters (or 200 meters), and view',
        'distance_range': 'Reproject, buffer 100 meters to 200 meters, and view',
        'omitted_repeated_buffer': '先重投影，先缓冲100米，再缓冲100米，最后查看',
        'omitted_repeated_buffer_english': 'Reproject, buffer 100 meters, then buffer 100 meters, and view',
        'named_omitted_chinese': '先重投影，先缓冲100米，名称为“100米缓冲”后再缓冲100米，最后查看',
        'spaced_name_omitted_chinese': '先重投影，先缓冲100米，名称为 “中间结果”后缓冲100米，最后查看',
        'named_omitted_english': 'Reproject, buffer 100 meters, called "100 meters buffer" then buffer 100 meters, and view',
        'unnamed_quotes_omitted_chinese': '先重投影，先缓冲100米，名称为中间结果接着缓冲100米，最后查看',
        'unnamed_quotes_omitted_english': 'Reproject, buffer 100 meters, called first then buffer 100 meters, and view',
        'chinese_layer': '先重投影到 EPSG:32650，再对所选图层进行 100 米的缓冲并显示',
        'scale_distance': '先重投影，再缓冲 200；地图比例尺距离 100 米，最后查看。',
        'scale_distance_english': 'Reproject, buffer 200, display a scale bar distance 100 meters, and view.',
        'square_meters': 'Reproject, buffer 100 m2, and view',
        'square_unicode': 'Reproject, buffer 100 m², and view',
        'square_words': 'Reproject, buffer 100 meters squared, and view',
        'square_multiplication': 'Reproject, buffer 100 m * 2, and view',
        'square_chinese': '先重投影，再缓冲 100 平方米，最后查看',
        'swapped_buffers': 'Reproject, buffer 100 meters, then buffer 200 meters, and view',
        'ordered_buffers': 'Reproject, buffer 100 meters, then buffer 200 meters, and view',
        'repeated_buffers': '先重投影，先缓冲100米，再缓冲100米，最后查看',
        'named_repeated_chinese': '先重投影，先缓冲100米，名称为“100米缓冲”后再缓冲100米，最后查看',
        'spaced_name_repeated_chinese': '先重投影，先缓冲100米，名称为 “中间结果”后缓冲100米，最后查看',
        'named_repeated_english': 'Reproject, buffer 100 meters, called "100 meters buffer" then buffer 100 meters, and view',
        'unnamed_quotes_repeated_chinese': '先重投影，先缓冲100米，名称为中间结果接着缓冲100米，最后查看',
        'unnamed_quotes_repeated_english': 'Reproject, buffer 100 meters, called first then buffer 100 meters, and view',
    }
    response = client.post("/api/chat/stream", json={"thread_id": "wrong-coordinate",
                 "message": requests.get(mode, "Reproject, buffer 100 meters, and view"), "files": ["file-1"]})
    if writes == 0 and mode not in {'preflight_unavailable', 'original_wal'}:
        assert error in event(response, 'error')['message'].lower()
        assert calls == [] and mcp_requests == [] and mcp_connections == []
        return
    plan = event(response, "human_review_required")["task_plan"]
    assert calls == [] and mcp_requests == [] and mcp_connections == []
    if writes == -2:
        distances = [call['arguments']['parameters']['DISTANCE'] for call in plan['tool_calls']
                     if call['arguments'].get('algorithm') == 'native:buffer']
        assert distances == ([100, 200] if mode == 'ordered_buffers' else [100, 100])
        return
    if mode == 'advice':
        assert client.post('/api/review', json={
            'thread_id': 'wrong-coordinate', 'task_id': plan['task_id'], 'revision': plan['revision'],
            'approved': False, 'advise': '改成200米',
        }).status_code == 200
        revised = event(client.post('/api/resume/wrong-coordinate'), 'human_review_required')['task_plan']
        assert revised['task_id'] == plan['task_id'] and revised['revision'] == plan['revision'] + 1
        assert revised['tool_calls'][1]['distance_unit'] == 'm'
        assert revised['tool_calls'][1]['arguments']['parameters']['DISTANCE'] == 200
        assert calls == []
        return
    if mode == 'original_wal':
        Path(str(source) + '-wal').write_bytes(b'active transaction')
    assert client.post("/api/review", json={"thread_id": "wrong-coordinate", "task_id": plan["task_id"],
                                              "revision": plan["revision"], "approved": True}).status_code == 200
    response = client.post("/api/resume/wrong-coordinate")
    if mode in {'project', 'project_sidecar'}:
        risk = event(response, 'risk_review_required')
        assert [name for name, _ in calls] == ['execute_processing', 'execute_processing', 'add_layer']
        # A page reload reads the same risk decision; approving it must not make
        # the loaded layer's existing source into a new file needing a checksum.
        assert client.get('/api/state/wrong-coordinate').json()['pause']['review_id'] == risk['review_id']
        if mode == 'project_sidecar':
            Path(calls[1][1]['parameters']['OUTPUT'] + '-wal').write_bytes(b'active transaction')
        assert client.post('/api/risk-review', json={
            'thread_id': 'wrong-coordinate', 'task_id': plan['task_id'], 'revision': plan['revision'],
            'review_id': risk['review_id'], 'status': 'approved',
        }).status_code == 200
        response = client.post('/api/resume/wrong-coordinate')
        if mode == 'project_sidecar':
            assert 'needs attention' in event(response, 'error')['message']
            assert 'sqlite' in event(response, 'error')['message'].lower()
            assert [name for name, _ in calls] == ['execute_processing', 'execute_processing', 'add_layer']
            result = client.get(f"/api/executions/{plan['task_id']}").json()['execution']
            assert result['status'] == 'needs_attention'
            completed = result['attempts'][:3]
            assert all(attempt['observation']['status'] == 'success' and
                       attempt['observation']['outcome'] == 'completed' for attempt in completed)
            assert client.post('/api/resume/wrong-coordinate').status_code == 409
            assert len(calls) == 3
            return
    if error:
        assert error in event(response, "error")["message"].lower()
    else:
        event(response, 'result_review_required')
    assert len(calls) == writes
    if mode in {'preflight_unavailable', 'original_wal'}:
        result = client.get(f"/api/executions/{plan['task_id']}").json()['execution']
        assert result['status'] == 'failed' and result['attempts'] == []
        assert error in result['summary'].lower()
        history = client.get('/api/sessions/wrong-coordinate/history').json()
        assert error in json.dumps(history).lower()
        assert client.get('/api/state/wrong-coordinate').json()['can_start_task'] is True
        assert mcp_requests == [] and mcp_connections == []
        return
    assert mcp_requests[0][0] == 'get_qgis_info'
    assert len(mcp_requests[0][1]['processing_requests']) == 2
    result = client.get(f"/api/executions/{plan['task_id']}").json()["execution"]
    assert result["status"] == ("failed" if error else "success") and result["confirmed"] is None
    attempt = result["attempts"][writes - 1] if error else result['attempts'][1]
    assert attempt["observation"]["status"] == 'success'
    assert attempt["observation"]["outcome"] == "completed"
    artifact = attempt['artifacts'][0]
    if mode == 'produced_wal':
        assert attempt['observation']['raw_response']['status'] == 'success'
        assert len([entry for entry in inspections if entry['mode'] == 'validate']) == 0
        return
    assert artifact['spatial_validation']['status'] == ('passed' if not error or mode in {'tampered', 'consumption_wal'} else 'failed')
    assert artifact['deliverable'] is (error is None)
    if mode in {'tampered', 'consumption_wal'}:
        assert artifact['integrity_validation']['status'] == 'failed'
    if writes >= 2:
        assert calls[1][1]['parameters']['INPUT'] == calls[0][1]['parameters']['OUTPUT']
        assert 'distance_unit' not in calls[1][1]
    if not error:
        assert calls[2][1]['path'] == calls[1][1]['parameters']['OUTPUT']
        load = next(item for item in result['attempts'] if item['tool_call']['tool_name'] == 'add_layer')
        assert load['observation']['data']['source'] == calls[1][1]['parameters']['OUTPUT']
        assert load['artifacts'][0]['kind'] == 'layer'
        assert load['artifacts'][0]['spatial_validation']['status'] == 'passed'
        assert len(result['artifacts']) == (4 if mode == 'project' else 3)
        expected_modes = ['preflight', 'preflight', 'validate', 'validate', 'validate']
        assert [item['mode'] for item in inspections] == expected_modes + (['preflight'] if mode == 'project' else [])
    if mode == 'coordinate':
        assert client.get('/api/state/wrong-coordinate').json()['can_start_task'] is True
        prior_requests = list(mcp_requests)
        next_plan = event(client.post('/api/chat/stream', json={
            'thread_id': 'wrong-coordinate', 'message': 'Reproject, buffer 100 meters, and view',
            'files': ['file-1'],
        }), 'human_review_required')['task_plan']
        assert next_plan['task_id'] != plan['task_id']
        assert mcp_requests == prior_requests
        assert records[plan['task_id']]['status'] == 'failed'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
