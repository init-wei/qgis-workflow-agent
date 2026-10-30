"""T05 public workflow and policy boundaries with a fake GIS seam."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import executor_node as executor_module
from agent.nodes import planner_node as planner_module
from agent.tools import execution_guard
from agent.tools.tool_contracts import ToolCallPlan
from agent.tools.tool_policy import PathPolicy
from conftest import fake_spatial_inspector
from qgis_mcp_server.qgis_mcp_plugin.processing_policy import validate_processing_request
import ui.backend.main as backend


def _event(response, name):
    for block in response.text.strip().split('\n\n'):
        lines = block.splitlines()
        if lines and lines[0] == f'event: {name}':
            return json.loads(lines[1].removeprefix('data: '))
    raise AssertionError(f'{name} not found in {response.text}')


@pytest.mark.parametrize('key', ['INPUT', 'INPUT_LAYER', 'OVERLAY'])
def test_processing_sources_cannot_escape_workspace_even_with_layer_selector(tmp_path, key):
    workspace = tmp_path / 'workspace'
    call = ToolCallPlan(step_id=1, tool_name='execute_processing', arguments={
        'algorithm': 'native:clip', 'parameters': {key: str(tmp_path / 'outside.gpkg') + '|layername=areas'}})
    assert not PathPolicy(workspace).evaluate_call(call).allowed
    call.arguments['parameters'][key] = str(workspace / 'source.gpkg') + '|layername=areas'
    assert PathPolicy(workspace).evaluate_call(call).allowed
    call.arguments['parameters'][key] = 'https://example.org/file.gpkg|layername=areas'
    assert not PathPolicy(workspace).evaluate_call(call).allowed
    call.arguments['parameters'][key] = str(workspace / 'source.gpkg') + '|layername=areas|subset=1'
    assert not PathPolicy(workspace).evaluate_call(call).allowed


@pytest.mark.parametrize(('algorithm', 'parameters'), [
    ('native:dissolve', {'INPUT': 'in.gpkg', 'OUTPUT': 'out.gpkg'}),
    ('native:dissolve', {'INPUT': 'in.gpkg', 'FIELD': [], 'OUTPUT': 'out.gpkg'}),
    ('native:dissolve', {'INPUT': 'in.gpkg', 'FIELD': [''], 'SEPARATE_DISJOINT': False, 'OUTPUT': 'out.gpkg'}),
    ('native:basicstatisticsforfields', {'INPUT_LAYER': 'in.gpkg', 'FIELD_NAME': 'value'}),
    ('native:basicstatisticsforfields', {'INPUT_LAYER': 'in.gpkg', 'FIELD_NAME': 'value', 'OUTPUT': 'out.csv'}),
    ('qgis:statisticsbycategories', {'INPUT': 'in.gpkg', 'CATEGORIES_FIELD_NAME': [], 'VALUES_FIELD_NAME': 'value', 'OUTPUT': 'out.gpkg'}),
    ('qgis:statisticsbycategories', {'INPUT': 'in.gpkg', 'CATEGORIES_FIELD_NAME': ['group'], 'VALUES_FIELD_NAME': '', 'OUTPUT': 'out.gpkg'}),
    ('qgis:exportaddgeometrycolumns', {'INPUT': 'in.gpkg', 'CALC_METHOD': 1, 'OUTPUT': 'out.gpkg'}),
    ('qgis:exportaddgeometrycolumns', {'INPUT': 'in.gpkg', 'CALC_METHOD': True, 'OUTPUT': 'out.gpkg'}),
    ('native:clip', {'INPUT': 'in.gpkg', 'OVERLAY': 'mask.gpkg', 'OUTPUT': 'out.geojson'}),
    ('native:dissolve', {'INPUT': 'in.gpkg', 'FIELD': [], 'SEPARATE_DISJOINT': False, 'OUTPUT': 'out.geojson'}),
    ('qgis:exportaddgeometrycolumns', {'INPUT': 'in.gpkg', 'CALC_METHOD': 0, 'OUTPUT': 'out.geojson'}),
])
def test_processing_policy_rejects_unreviewable_or_unsafe_options(algorithm, parameters):
    with pytest.raises(ValueError):
        validate_processing_request(algorithm, parameters)


def test_processing_policy_accepts_explicit_supported_forms():
    for algorithm, parameters in [
        ('native:dissolve', {'INPUT': 'in.gpkg', 'FIELD': [], 'SEPARATE_DISJOINT': False, 'OUTPUT': 'out.gpkg'}),
        ('native:dissolve', {'INPUT': 'in.gpkg', 'FIELD': ['group', 'class'], 'SEPARATE_DISJOINT': True, 'OUTPUT': 'out.gpkg'}),
        ('native:basicstatisticsforfields', {'INPUT_LAYER': 'in.gpkg', 'FIELD_NAME': 'value', 'OUTPUT': 'out.gpkg'}),
        ('qgis:statisticsbycategories', {'INPUT': 'in.gpkg', 'CATEGORIES_FIELD_NAME': ['group'], 'VALUES_FIELD_NAME': 'value', 'OUTPUT': 'out.gpkg'}),
        ('qgis:exportaddgeometrycolumns', {'INPUT': 'in.gpkg', 'CALC_METHOD': 0, 'OUTPUT': 'out.gpkg'}),
    ]:
        validate_processing_request(algorithm, parameters)


@pytest.mark.parametrize('bad_count', [False, True])
def test_public_load_count_has_independent_report_and_never_creates_file_artifact(monkeypatch, tmp_path, bad_count):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / 'source.geojson'
    source.write_text('{"type":"FeatureCollection","features":[]}')
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    record = {'file_id': 'file-1', 'filename': source.name, 'storage_path': source.name,
              'sha256': checksum, 'layers': [{'layer_name': 'source', 'crs': 'EPSG:32650',
                  'geometry': 'Polygon', 'feature_count': 3, 'fields': ['value'], 'is_2d': True}]}
    records, calls, inspections = {}, [], []

    class Model:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps({
                'task_id': 'model', 'task_summary': 'Load and count', 'risk_level': 'low',
                'review_status': 'pending_review', 'required_inputs': [
                    {'input_id': 'source', 'file_id': 'file-1', 'description': 'Source', 'path': 'source'}],
                'expected_outputs': [], 'steps': [{'step_id': 1, 'description': 'Load and count',
                    'allowed_tools': ['add_layer'], 'input_refs': ['source'], 'output_refs': [],
                    'risk_level': 'low', 'requires_review': False}],
                'tool_calls': [{'step_id': 1, 'tool_name': 'add_layer', 'arguments': {
                    'path': 'source', 'layer_type': 'vector'}}],
            }))

    def spatial_inspector(request):
        inspections.append(request)
        if request['mode'] == 'preflight':
            return fake_spatial_inspector(request)
        assert request['observation']['source'] == str(source)
        assert request['input_paths'] == {'source': str(source)}
        errors = ['feature count differs'] if request['observation']['feature_count'] != 3 else []
        return {'status': 'failed' if errors else 'passed', 'checks': ['feature_count'],
                'errors': errors, 'facts': {'statistics': {'kind': 'feature_count', 'field': None,
                    'group_fields': [], 'feature_count': 3, 'valid_value_count': None,
                    'null_value_count': None, 'sum': None, 'min': None, 'max': None,
                    'unit': None, 'empty': False, 'no_valid_values': False,
                    'groups': [], 'omitted_groups': []}}}

    class GIS:
        async def list_tools(self):
            return [{'name': 'add_layer'}]

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {'status': 'success', 'result': {'id': 'layer-1', 'source': arguments['path'],
                'crs': 'EPSG:32650', 'geometry': 'Polygon', 'feature_count': 4 if bad_count else 3,
                'fields': ['value']}}

    graph = build_graph(MemorySaver(), processing_inspector=lambda _: {'status': 'success',
                        'result': {'processing_capabilities': []}}, spatial_inspector=spatial_inspector)

    async def get_graph():
        return graph

    async def get_gis():
        return GIS()

    def save_record(**entry):
        records[entry['task_id']] = {'confirmed': None, 'summary': None, **entry}

    def get_record(task_id):
        entry = records[task_id]
        return {'execution': entry, 'attempts': entry['attempts'], 'artifacts': [
            item for attempt in entry['attempts'] for item in attempt['artifacts']]}

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(backend, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(backend, 'get_uploaded_file_record', lambda _: record)
    monkeypatch.setattr(backend, 'save_task_execution_record', save_record)
    monkeypatch.setattr(backend, 'get_task_execution_record', get_record)
    monkeypatch.setattr(executor_module, 'save_task_execution_record', save_record)
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    monkeypatch.setattr(planner_module, '_get_llm', lambda _: Model())
    execution_guard._unknown_tasks.clear()
    client = TestClient(backend.app)
    review = _event(client.post('/api/chat/stream', json={'thread_id': 'count',
        'message': 'Load and count the selected layer', 'files': ['file-1']}), 'human_review_required')
    plan = review['task_plan']
    assert calls == []
    assert client.post('/api/review', json={'thread_id': 'count', 'task_id': plan['task_id'],
        'revision': plan['revision'], 'approved': True}).status_code == 200
    response = client.post('/api/resume/count')
    result_event = _event(response, 'error' if bad_count else 'result_review_required')
    if not bad_count:
        assert '输出文件' not in result_event['execution_summary']
    execution = client.get(f"/api/executions/{plan['task_id']}").json()['execution']
    assert execution['status'] == ('failed' if bad_count else 'success')
    attempt = execution['attempts'][0]
    assert attempt['observation']['status'] == 'success'
    assert attempt['observation']['outcome'] == 'completed'
    assert attempt['observation']['data']['source'] == str(source)
    artifact, = attempt['artifacts']
    assert artifact['kind'] == 'layer' and artifact['source_ref'] == 'source'
    assert artifact['path'] == str(source) and artifact['layer_id'] == 'layer-1'
    assert artifact['deliverable'] is (not bad_count)
    assert artifact['spatial_validation']['status'] == ('failed' if bad_count else 'passed')
    assert execution['confirmed'] is None
    assert len(calls) == 1 and hashlib.sha256(source.read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize(('mode', 'written'), [
    ('valid', 4), ('bad_crs', 1), ('bad_groups', 2), ('bad_area', 3),
    ('bad_summary', 4), ('bad_null_summary', 4), ('tampered_area', 3),
    ('wrong_field', 0), ('field_collision', 0), ('wrong_unit', 0), ('bad_group_parameter', 0),
    ('risk_producer', 4), ('review_revision', -1), ('bad_late_values', 3), ('preflight_mutation', 3),
    ('missing_summary', 0), ('wrong_summary_step', 0), ('wrong_summary_field', 0), ('wrong_summary_unit', 0),
])
def test_public_chain_consumes_verified_producers_and_stops_on_wrong_results(monkeypatch, tmp_path, mode, written):
    """The fake GIS seam supplies verdicts; execution, binding, and persistence stay real."""
    monkeypatch.chdir(tmp_path)
    records = {}
    selected = {}
    for file_id in ('source', 'mask'):
        path = tmp_path / f'{file_id}.geojson'
        path.write_text('{"type":"FeatureCollection","features":[]}')
        selected[file_id] = {'file_id': file_id, 'filename': path.name, 'storage_path': path.name,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'layers': [{
                'layer_name': file_id, 'crs': 'EPSG:32650', 'geometry': 'Polygon',
                'feature_count': 3 if file_id == 'source' else 1, 'fields': ['grp', 'value'], 'is_2d': True}]}
    definitions = [
        ('native:clip', {'INPUT': 'source-ref', 'OVERLAY': 'mask-ref', 'OUTPUT': 'clip.gpkg'}, ['source', 'mask'], 'clip'),
        ('native:dissolve', {'INPUT': 'clip.gpkg', 'FIELD': ['grp'], 'SEPARATE_DISJOINT': False,
                             'OUTPUT': 'dissolved.gpkg'}, ['clip'], 'dissolved'),
        ('qgis:exportaddgeometrycolumns', {'INPUT': 'dissolved.gpkg', 'CALC_METHOD': 0,
                                          'OUTPUT': 'area.gpkg'}, ['dissolved'], 'area'),
        ('native:basicstatisticsforfields', {'INPUT_LAYER': 'area.gpkg', 'FIELD_NAME': 'area',
                                             'OUTPUT': 'summary.gpkg'}, ['area'], 'summary'),
    ]
    if mode == 'bad_group_parameter':
        definitions[1][1]['FIELD'] = ['']
    calls, inspections, connections = [], [], []

    class Model:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps({
                'task_id': 'model', 'task_summary': 'Clip, dissolve, area and statistics', 'risk_level': 'low',
                'review_status': 'pending_review', 'metadata': {'spatial_preflight': {
                    'task_id': 'model-forged', 'revision': 99, 'review_steps': [{'unit': 'forged'}]}},
                'required_inputs': [{'input_id': ref, 'description': ref, 'file_id': ref, 'path': f'{ref}-ref'}
                                    for ref in ('source', 'mask')],
                'expected_outputs': [{'output_id': output, 'description': output, 'path': params['OUTPUT']}
                                     for _, params, _, output in definitions],
                'steps': [{'step_id': index, 'description': algorithm, 'allowed_tools': ['execute_processing'],
                           'input_refs': refs, 'output_refs': [output],
                           'risk_level': 'high' if mode == 'risk_producer' and index == 3 else 'low',
                           'requires_review': mode == 'risk_producer' and index == 3}
                          for index, (algorithm, _, refs, output) in enumerate(definitions, 1)],
                'tool_calls': [{'step_id': index, 'tool_name': 'execute_processing',
                    'risk_level': 'high' if mode == 'risk_producer' and index == 3 else 'low',
                    'requires_review': mode == 'risk_producer' and index == 3, 'arguments': {
                    'algorithm': algorithm, 'parameters': params}}
                    for index, (algorithm, params, _, _) in enumerate(definitions, 1)],
            }))

    def processing_result(requests):
        if requests == {'capabilities': True}:
            return {'status': 'success', 'result': {'processing_capabilities': []}}
        return {'status': 'success', 'result': {'processing_validation': [{
            'step_id': request['step_id'], 'valid': True,
            'source_paths': [value for key, value in request['parameters'].items()
                             if key in {'INPUT', 'INPUT_LAYER', 'OVERLAY'}],
            'output_paths': [request['parameters']['OUTPUT']],
        } for request in requests]}}

    def spatial_inspector(request):
        inspections.append(request)
        if request['mode'] == 'preflight':
            preflight_plan = request['plan']
            if (mode == 'bad_late_values' and len(preflight_plan['tool_calls']) == 1
                    and preflight_plan['tool_calls'][0]['step_id'] == 4):
                assert request['input_facts']['area']['algorithm'] == 'qgis:exportaddgeometrycolumns'
                return {'status': 'failed', 'checks': [], 'errors': ['non-finite value'], 'facts': {}}
            if (mode == 'preflight_mutation' and len(preflight_plan['tool_calls']) == 1
                    and preflight_plan['tool_calls'][0]['step_id'] == 4):
                Path(preflight_plan['required_inputs'][0]['path']).write_text('changed during preflight')
            errors = [mode] if mode in {'wrong_field', 'field_collision', 'wrong_unit'} else []
            review_steps = [{
                'step_id': call['step_id'], 'tool': call['tool_name'],
                'algorithm': call['arguments']['algorithm'],
                'input_crs': [{'ref': ref, 'crs': 'EPSG:32650'} for ref in step['input_refs']],
                'output_crs': None if call['step_id'] == 4 else 'EPSG:32650',
                'input_kind': 'spatial', 'output_kind': 'table' if call['step_id'] == 4 else 'spatial',
                'geometry': None if call['step_id'] == 4 else 'Polygon',
                'field': 'area' if call['step_id'] >= 3 else None,
                'field_type': 'Real' if call['step_id'] >= 3 else None,
                'group_fields': ['grp'] if call['step_id'] == 2 else [],
                'unit': 'm2' if call['step_id'] >= 3 else None,
            } for step, call in zip(preflight_plan['steps'], preflight_plan['tool_calls'])]
            if mode == 'missing_summary':
                return {'status': 'passed', 'checks': [], 'errors': [], 'facts': {}}
            if mode == 'wrong_summary_step':
                review_steps[0]['step_id'] = 99
            if mode == 'wrong_summary_field':
                review_steps[3]['field'] = 'value'
            if mode == 'wrong_summary_unit':
                review_steps[2]['unit'] = None
            return {'status': 'failed' if errors else 'passed', 'checks': [], 'errors': errors,
                    'facts': {'review_steps': review_steps}}
        algorithm = request['call']['arguments']['algorithm']
        bad_algorithm = {'bad_crs': 'native:clip', 'bad_groups': 'native:dissolve',
                         'bad_area': 'qgis:exportaddgeometrycolumns',
                         'bad_summary': 'native:basicstatisticsforfields',
                         'bad_null_summary': 'native:basicstatisticsforfields'}.get(mode)
        errors = [mode] if algorithm == bad_algorithm else []
        facts = {'algorithm': algorithm}
        if algorithm in {'native:dissolve', 'qgis:exportaddgeometrycolumns', 'native:basicstatisticsforfields'}:
            ref = next(iter(request['input_paths']))
            expected_producer = {'clip': 'native:clip', 'dissolved': 'native:dissolve',
                                 'area': 'qgis:exportaddgeometrycolumns'}[ref]
            assert request['input_facts'][ref]['algorithm'] == expected_producer
        if algorithm == 'qgis:exportaddgeometrycolumns' and not errors:
            facts.update(algorithm=algorithm, field_units={'area': 'm2', 'perimeter': 'm'})
        if algorithm == 'native:basicstatisticsforfields':
            assert request['input_facts']['area']['algorithm'] == 'qgis:exportaddgeometrycolumns'
            assert request['input_facts']['area']['field_units'] == {'area': 'm2', 'perimeter': 'm'}
            facts['statistics'] = {'kind': 'numeric', 'field': 'area', 'group_fields': [],
                'feature_count': 2, 'valid_value_count': 2, 'null_value_count': 0,
                'sum': 12, 'min': 2, 'max': 10, 'unit': 'm2', 'empty': False,
                'no_valid_values': False, 'groups': [], 'omitted_groups': []}
        return {'status': 'failed' if errors else 'passed', 'checks': [algorithm], 'errors': errors, 'facts': facts}

    class GIS:
        async def list_tools(self):
            return [{'name': 'execute_processing'}, {'name': 'get_qgis_info'}]

        async def call_tool(self, name, arguments):
            if name == 'get_qgis_info':
                return processing_result(arguments['processing_requests'])
            calls.append(arguments)
            path = Path(arguments['parameters']['OUTPUT'])
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'fake_gis_output': len(calls)}))
            return {'status': 'success', 'result': {'algorithm': arguments['algorithm'],
                                                   'result': {'OUTPUT': str(path)}}}

    graph = build_graph(MemorySaver(), processing_inspector=processing_result, spatial_inspector=spatial_inspector)

    async def get_graph():
        return graph

    async def get_gis():
        connections.append(True)
        return GIS()

    tampered = False

    def save_record(**entry):
        nonlocal tampered
        records[entry['task_id']] = {'confirmed': None, 'summary': None, **entry}
        if mode == 'tampered_area' and not tampered and entry['status'] == 'running' and not entry.get('inflight_call'):
            for attempt in entry['attempts']:
                if attempt['step_id'] == 3 and attempt['artifacts']:
                    Path(attempt['artifacts'][0]['path']).write_text('changed after area validation')
                    tampered = True

    def get_record(task_id):
        entry = records[task_id]
        return {'execution': entry, 'attempts': entry['attempts'], 'artifacts': [
            item for attempt in entry['attempts'] for item in attempt['artifacts']]}

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(backend, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(backend, 'get_uploaded_file_record', lambda file_id: selected[file_id])
    monkeypatch.setattr(backend, 'save_task_execution_record', save_record)
    monkeypatch.setattr(backend, 'get_task_execution_record', get_record)
    monkeypatch.setattr(executor_module, 'save_task_execution_record', save_record)
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    monkeypatch.setattr(planner_module, '_get_llm', lambda _: Model())
    execution_guard._unknown_tasks.clear()
    client = TestClient(backend.app)
    response = client.post('/api/chat/stream', json={'thread_id': 'chain',
        'message': 'Clip, dissolve by grp, and calculate area statistics', 'files': ['source', 'mask']})
    if not written:
        _event(response, 'error')
        assert calls == [] and connections == []
        return
    plan = _event(response, 'human_review_required')['task_plan']
    assert not calls and not connections
    snapshot = plan['metadata']['spatial_preflight']
    assert snapshot['task_id'] == plan['task_id'] and snapshot['revision'] == plan['revision']
    assert [item['step_id'] for item in snapshot['review_steps']] == [1, 2, 3, 4]
    assert snapshot['review_steps'][2]['field'] == 'area'
    assert snapshot['review_steps'][2]['field_type'] == 'Real'
    assert snapshot['review_steps'][2]['unit'] == 'm2'
    assert snapshot['review_steps'][3]['input_crs'] == [{'ref': 'area', 'crs': 'EPSG:32650'}]
    assert snapshot['review_steps'][3]['output_kind'] == 'table'
    assert 'spatial_preflight' not in inspections[0]['plan']['metadata']
    if mode == 'review_revision':
        assert client.post('/api/review', json={'thread_id': 'chain', 'task_id': plan['task_id'],
            'revision': plan['revision'], 'approved': False, 'advise': 'Revise the plan'}).status_code == 200
        revised = _event(client.post('/api/resume/chain'), 'human_review_required')['task_plan']
        assert revised['task_id'] == plan['task_id'] and revised['revision'] == plan['revision'] + 1
        assert revised['metadata']['spatial_preflight']['task_id'] == revised['task_id']
        assert revised['metadata']['spatial_preflight']['revision'] == revised['revision']
        assert revised['metadata']['spatial_preflight']['review_steps'] == snapshot['review_steps']
        assert len([request for request in inspections if request['mode'] == 'preflight']) == 2
        assert not calls and not connections
        return
    assert client.post('/api/review', json={'thread_id': 'chain', 'task_id': plan['task_id'],
        'revision': plan['revision'], 'approved': True}).status_code == 200
    response = client.post('/api/resume/chain')
    if mode == 'risk_producer':
        risk = _event(response, 'risk_review_required')
        assert len(calls) == 2
        assert client.post('/api/risk-review', json={'thread_id': 'chain', 'task_id': plan['task_id'],
            'revision': plan['revision'], 'review_id': risk['review_id'], 'status': 'approved'}).status_code == 200
        response = client.post('/api/resume/chain')
    successful = mode in {'valid', 'risk_producer'}
    _event(response, 'result_review_required' if successful else 'error')
    execution = client.get(f"/api/executions/{plan['task_id']}").json()['execution']
    assert execution['plan']['metadata']['spatial_preflight'] == snapshot
    assert execution['status'] == ('success' if successful else 'failed')
    assert execution['confirmed'] is None and len(calls) == written
    for i in range(1, written):
        source_key = 'INPUT_LAYER' if i == 3 else 'INPUT'
        assert calls[i]['parameters'][source_key] == calls[i - 1]['parameters']['OUTPUT']
    assert calls[0]['parameters']['INPUT'] == str(tmp_path / 'source.geojson')
    assert calls[0]['parameters']['OVERLAY'] == str(tmp_path / 'mask.geojson')
    last = execution['attempts'][-1]
    assert last['observation']['status'] == 'success' and last['observation']['outcome'] == 'completed'
    artifact, = last['artifacts']
    assert artifact['kind'] == 'path' and artifact['deliverable'] is (successful or mode == 'bad_late_values')
    assert artifact['spatial_validation']['status'] == (
        'passed' if successful or mode in {'tampered_area', 'bad_late_values', 'preflight_mutation'} else 'failed')
    if mode in {'tampered_area', 'preflight_mutation'}:
        assert artifact['integrity_validation']['status'] == 'failed'
    if successful:
        assert artifact['spatial_validation']['facts']['statistics']['unit'] == 'm2'
        assert artifact['spatial_validation']['facts']['statistics']['sum'] == 12
    if mode == 'risk_producer':
        assert [attempt['status'] for attempt in execution['attempts']] == ['success', 'success', 'blocked', 'success', 'success']
