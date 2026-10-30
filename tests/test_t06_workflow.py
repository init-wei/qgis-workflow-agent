"""Reviewed Skill examples enter the normal public plan-review workflow."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import executor_node as executor_module
from agent.nodes import planner_node as planner_module
from conftest import fake_spatial_inspector
import ui.backend.main as backend


def event(response, name):
    for block in response.text.strip().split('\n\n'):
        lines = block.splitlines()
        if lines and lines[0] == f'event: {name}':
            return json.loads(lines[1].removeprefix('data: '))
    raise AssertionError(f'{name} not found in {response.text}')


def load_plan(source, *, metadata=None):
    return {
        'task_id': 'model-id', 'task_summary': '加载新图层', 'risk_level': 'low',
        'review_status': 'approved', 'required_inputs': [{
            'input_id': 'input', 'description': '选中的新文件', 'file_id': 'new-file',
            'path': str(source),
        }], 'expected_outputs': [], 'steps': [{
            'step_id': 1, 'description': '加载新图层', 'allowed_tools': ['add_layer'],
            'input_refs': ['input'], 'output_refs': [], 'risk_level': 'low', 'requires_review': False,
        }], 'tool_calls': [{
            'step_id': 1, 'tool_name': 'add_layer', 'arguments': {
                'path': str(source), 'layer_type': 'vector', 'name': '用户指定的新名称',
            },
        }], 'metadata': metadata or {},
    }


def setup_workflow(monkeypatch, tmp_path, proposal, *, filename=None, storage_filename='new.geojson', layer_name='new-layer', spatial_inspector=fake_spatial_inspector):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / storage_filename
    source.write_text('{"type":"FeatureCollection","features":[]}')
    record = {
        'file_id': 'new-file', 'filename': filename or source.name, 'storage_path': source.name,
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'layers': [{'layer_name': layer_name, 'crs': 'EPSG:32650', 'geometry': 'Point',
                    'feature_count': 1, 'is_2d': True}],
    }
    prompts = []
    gis_calls = []

    class Model:
        async def ainvoke(self, messages):
            prompts.append('\n'.join(str(message.content) for message in messages))
            return SimpleNamespace(content=json.dumps(proposal(source), ensure_ascii=False))

    async def get_gis():
        gis_calls.append('connected')
        raise AssertionError('GIS must not be contacted before approval')

    def processing_inspector(request):
        if request == {'capabilities': True}:
            return {'status': 'success', 'result': {'processing_capabilities': []}}
        return {'status': 'success', 'result': {'processing_validation': [{
            'step_id': call['step_id'], 'valid': True,
            'source_paths': [value for key, value in call['parameters'].items() if key in {'INPUT', 'INPUT_LAYER', 'OVERLAY'}],
            'output_paths': [call['parameters']['OUTPUT']],
        } for call in request]}}

    graph = build_graph(MemorySaver(), processing_inspector=processing_inspector,
                        spatial_inspector=spatial_inspector)

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(backend, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(backend, 'get_uploaded_file_record', lambda _: record)
    monkeypatch.setattr(backend, 'save_task_execution_record', lambda **_: None)
    monkeypatch.setattr(planner_module, '_get_llm', lambda _: Model())
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    return TestClient(backend.app), source, prompts, gis_calls


def test_matching_candidate_is_a_reference_for_a_new_pending_plan(monkeypatch, tmp_path):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', 'true')
    client, source, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, load_plan)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'skill-load', 'message': '加载图层，名称为用户指定的新名称', 'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']

    assert plan['review_status'] == 'pending_review'
    assert plan['required_inputs'][0]['file_id'] == 'new-file'
    assert plan['tool_calls'][0]['arguments']['path'] == str(source)
    assert plan['tool_calls'][0]['arguments']['name'] == '用户指定的新名称'
    assert len(prompts) == 1 and '"family": "load"' in prompts[0]
    assert '"task_plan"' in prompts[0] and '"new-file"' in prompts[0]
    candidate_facts = plan['metadata']['skill_candidates']
    assert candidate_facts['enabled'] is True
    assert any(item['family'] == 'load' and item['review_status'] == 'pending_human_review'
               and item['skill_id'] and item['version'] and item['provenance']
               for item in candidate_facts['provided'])
    assert gis_calls == []


@pytest.mark.parametrize('place', ['input_path', 'call_path', 'nested_key'])
def test_unresolved_placeholders_are_rejected_before_server_path_binding(monkeypatch, tmp_path, place):
    def proposal(source):
        plan = load_plan(source)
        if place == 'input_path':
            plan['required_inputs'][0]['path'] = '${missing_path}'
        elif place == 'call_path':
            # add_layer binding would normally replace this path with the selected file.
            plan['tool_calls'][0]['arguments']['path'] = '${missing_path}'
        else:
            plan['tool_calls'][0]['metadata'] = {'nested': {'${missing_key}': 'value'}}
        return plan

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    response = client.post('/api/chat/stream', json={
        'thread_id': f'unresolved-{place}', 'message': '加载图层', 'files': ['new-file'],
    })

    error = event(response, 'error')['message']
    assert 'placeholder' in error.lower() or 'variable' in error.lower()
    assert 'event: human_review_required' not in response.text
    assert len(prompts) == 2 and error in prompts[1]
    assert gis_calls == []


def test_hidden_metadata_tool_calls_cannot_bypass_plan_validation(monkeypatch, tmp_path):
    def proposal(source):
        return load_plan(source, metadata={'tool_calls': [{'tool_name': 'remove_layer'}]})

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'hidden-calls', 'message': '加载图层', 'files': ['new-file'],
    })

    assert 'hidden_tool_calls' in event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text
    assert len(prompts) == 2 and 'hidden_tool_calls' in prompts[1]
    assert gis_calls == []


@pytest.mark.parametrize(('enabled', 'query', 'expected_families'), [
    ('true', '加载图层', {'load'}),
    ('true', '请读取选中的文件', set()),
    ('false', '加载图层', set()),
])
def test_candidate_metadata_is_owned_by_server_even_if_model_claims_otherwise(
        monkeypatch, tmp_path, enabled, query, expected_families):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)

    def proposal(source):
        return load_plan(source, metadata={
            'skill_candidates': {'enabled': True, 'provided': [{
                'skill_id': 'invented', 'version': '999', 'family': 'invented',
                'review_status': 'approved', 'provenance': [],
            }]},
            'registered_skill_id': 'invented', 'skill_used': True,
        })

    client, source, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    response = client.post('/api/chat/stream', json={
        'thread_id': f'candidate-{enabled}-{len(expected_families)}', 'message': query, 'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']

    facts = plan['metadata']['skill_candidates']
    assert facts['enabled'] is (enabled == 'true')
    assert {item['family'] for item in facts['provided']} == expected_families
    assert 'invented' not in json.dumps(plan['metadata'])
    assert set(plan['metadata']) == {'skill_candidates', 'iteration', 'spatial_preflight', 'parameter_requirements'}
    assert ('已提供的方案参考示例' in prompts[0]) is bool(expected_families)
    assert plan['review_status'] == 'pending_review'
    assert plan['tool_calls'][0]['arguments']['path'] == str(source)
    assert gis_calls == []


def buffer_plan(source, *, distance=125.5, dissolve=True):
    return {
        'task_id': 'model-id', 'task_summary': '用新距离缓冲', 'risk_level': 'low',
        'review_status': 'approved', 'required_inputs': [{
            'input_id': 'input', 'description': '选中的新文件', 'file_id': 'new-file', 'path': str(source),
        }], 'expected_outputs': [{'output_id': 'buffered', 'description': '缓冲结果', 'path': 'fresh.gpkg'}],
        'steps': [{'step_id': 1, 'description': '缓冲', 'allowed_tools': ['execute_processing'],
                   'input_refs': ['input'], 'output_refs': ['buffered'], 'risk_level': 'low',
                   'requires_review': False}],
        'tool_calls': [{'step_id': 1, 'tool_name': 'execute_processing', 'distance_unit': 'm',
                        'arguments': {'algorithm': 'native:buffer', 'parameters': {
                            'INPUT': str(source), 'DISTANCE': distance, 'SEGMENTS': 12,
                            'END_CAP_STYLE': 0, 'JOIN_STYLE': 0, 'MITER_LIMIT': 2,
                            'DISSOLVE': dissolve, 'SEPARATE_DISJOINT': False, 'OUTPUT': 'fresh.gpkg',
                        }}}],
    }


def test_new_buffer_parameters_keep_types_and_do_not_inherit_sample_values(monkeypatch, tmp_path):
    client, source, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, buffer_plan)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'new-buffer', 'message': '缓冲 125.5 米，融合结果，每象限 12 段', 'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']

    assert '"family": "buffer"' in prompts[0]
    assert '"DISTANCE": 100' in prompts[0] and '"DISSOLVE": false' in prompts[0]
    assert plan['review_status'] == 'pending_review'
    assert plan['required_inputs'][0]['file_id'] == 'new-file'
    arguments = plan['tool_calls'][0]['arguments']
    assert set(arguments) == {'algorithm', 'parameters'}
    parameters = arguments['parameters']
    assert parameters['INPUT'] == str(source)
    assert parameters['DISTANCE'] == 125.5 and type(parameters['DISTANCE']) is float
    assert parameters['DISSOLVE'] is True
    assert parameters['SEGMENTS'] == 12 and type(parameters['SEGMENTS']) is int
    assert parameters['OUTPUT'] == plan['expected_outputs'][0]['path']
    assert gis_calls == []


def test_sample_distance_does_not_authorize_an_unspecified_buffer(monkeypatch, tmp_path):
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: buffer_plan(source, distance=100, dissolve=False),
    )
    response = client.post('/api/chat/stream', json={
        'thread_id': 'missing-distance', 'message': '请缓冲选中图层', 'files': ['new-file'],
    })

    error = event(response, 'error')['message']
    assert '缓冲距离' in error
    assert 'event: human_review_required' not in response.text
    assert len(prompts) == 2 and error in prompts[1]
    assert gis_calls == []


def test_candidate_cannot_fill_missing_dissolve_parameters(monkeypatch, tmp_path):
    def proposal(source):
        return {
            'task_id': 'model-id', 'task_summary': '融合', 'risk_level': 'low',
            'review_status': 'approved', 'required_inputs': [{
                'input_id': 'input', 'description': '选中图层', 'file_id': 'new-file', 'path': str(source),
            }], 'expected_outputs': [{'output_id': 'result', 'description': '结果', 'path': 'result.gpkg'}],
            'steps': [{'step_id': 1, 'description': '融合', 'allowed_tools': ['execute_processing'],
                       'input_refs': ['input'], 'output_refs': ['result'], 'risk_level': 'low',
                       'requires_review': False}],
            'tool_calls': [{'step_id': 1, 'tool_name': 'execute_processing', 'arguments': {
                'algorithm': 'native:dissolve', 'parameters': {
                    'INPUT': str(source), 'SEPARATE_DISJOINT': False, 'OUTPUT': 'result.gpkg',
                },
            }}],
        }

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'missing-field', 'message': '融合选中图层', 'files': ['new-file'],
    })
    error = event(response, 'error')['message']
    assert 'FIELD' in error
    assert 'event: human_review_required' not in response.text
    assert len(prompts) == 2 and error in prompts[1]
    assert gis_calls == []


def test_failed_proposal_feedback_is_cleared_for_new_human_revision(monkeypatch, tmp_path):
    count = 0

    def proposal(source):
        nonlocal count
        count += 1
        plan = load_plan(source)
        if count == 1:
            plan['tool_calls'][0]['arguments']['path'] = '${source_path}'
        elif count == 3:
            plan['tool_calls'][0]['arguments']['name'] = '修订后的名称'
        return plan

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    first = event(client.post('/api/chat/stream', json={
        'thread_id': 'skill-revision', 'message': '加载图层', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert len(prompts) == 2 and 'source_path' in prompts[1]
    assert first['metadata']['iteration'] == 2
    assert client.post('/api/review', json={
        'thread_id': 'skill-revision', 'task_id': first['task_id'], 'revision': first['revision'],
        'approved': False, 'advise': '名称改为修订后的名称',
    }).status_code == 200

    revised = event(client.post('/api/resume/skill-revision'), 'human_review_required')['task_plan']
    assert revised['task_id'] == first['task_id'] and revised['revision'] == first['revision'] + 1
    assert revised['review_status'] == 'pending_review' and revised['metadata']['iteration'] == 1
    assert revised['tool_calls'][0]['arguments']['name'] == '修订后的名称'
    assert '名称改为修订后的名称' in prompts[2]
    assert '上一轮计划未通过服务端检查' not in prompts[2]
    assert client.post('/api/review', json={
        'thread_id': 'skill-revision', 'task_id': first['task_id'], 'revision': first['revision'],
        'approved': True,
    }).status_code == 409
    assert gis_calls == []


def test_actual_candidate_facts_are_visible_in_business_query_after_rejection(monkeypatch, tmp_path):
    records = {}

    def save_record(**record):
        records[record['task_id']] = {**record, 'confirmed': None, 'summary': None}

    def get_record(task_id):
        record = records.get(task_id)
        return {'execution': record, 'attempts': [], 'artifacts': []} if record else None

    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, load_plan)
    monkeypatch.setattr(backend, 'save_task_execution_record', save_record)
    monkeypatch.setattr(backend, 'get_task_execution_record', get_record)
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'skill-history', 'message': '加载图层', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'skill-history', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': '修改图层名称',
    }).status_code == 200

    execution = client.get(f"/api/executions/{plan['task_id']}").json()['execution']
    assert execution['status'] == 'rejected'
    assert execution['plan']['metadata']['skill_candidates'] == plan['metadata']['skill_candidates']
    assert execution['plan']['metadata']['skill_candidates']['provided'][0]['provenance']
    assert execution['attempts'] == [] and gis_calls == []


def parameter_plan(source, algorithm, parameters):
    plan = load_plan(source)
    plan['expected_outputs'] = [{'output_id': 'result', 'description': '结果', 'path': 'result.gpkg'}]
    plan['steps'][0].update(allowed_tools=['execute_processing'], output_refs=['result'])
    input_key = 'INPUT_LAYER' if algorithm == 'native:basicstatisticsforfields' else 'INPUT'
    plan['tool_calls'] = [{'step_id': 1, 'tool_name': 'execute_processing', 'arguments': {
        'algorithm': algorithm, 'parameters': {input_key: str(source), 'OUTPUT': 'result.gpkg', **parameters},
    }}]
    return plan


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize(('query', 'algorithm', 'parameters', 'error_text'), [
    ('请重投影选中图层', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'}, '目标 CRS'),
    ('融合选中图层', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}, '融合'),
    ('请统计选中图层', 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'}, '统计'),
    ('按 grp 分类统计', 'qgis:statisticsbycategories',
     {'CATEGORIES_FIELD_NAME': ['grp'], 'VALUES_FIELD_NAME': 'value'}, '统计'),
])
def test_sample_values_cannot_authorize_required_business_parameters(
        monkeypatch, tmp_path, enabled, query, algorithm, parameters, error_text):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, parameters))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'missing-business-parameter', 'message': query, 'files': ['new-file'],
    })
    error = event(response, 'error')['message']
    assert error_text in error
    assert 'event: human_review_required' not in response.text
    assert len(prompts) == 2 and error in prompts[1]
    assert gis_calls == []


@pytest.mark.parametrize(('query', 'algorithm', 'parameters'), [
    ('请重投影到 EPSG:32651', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32651'}),
    ('Reproject the selected layer to EPSG:32651', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32651'}),
    ('按 grp 字段融合', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}),
    ('Dissolve by grp', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}),
    ('全部融合', 'native:dissolve', {'FIELD': [], 'SEPARATE_DISJOINT': False}),
    ('Dissolve all features', 'native:dissolve', {'FIELD': [], 'SEPARATE_DISJOINT': False}),
    ('统计 value 字段', 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'}),
    ('Calculate value statistics', 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'}),
    ('按 grp 分类统计 value', 'qgis:statisticsbycategories',
     {'CATEGORIES_FIELD_NAME': ['grp'], 'VALUES_FIELD_NAME': 'value'}),
    ('Calculate statistics for value grouped by grp', 'qgis:statisticsbycategories',
     {'CATEGORIES_FIELD_NAME': ['grp'], 'VALUES_FIELD_NAME': 'value'}),
    ('按 grp 分类统计，仅计数', 'qgis:statisticsbycategories', {'CATEGORIES_FIELD_NAME': ['grp']}),
    ('Statistics by grp count only', 'qgis:statisticsbycategories', {'CATEGORIES_FIELD_NAME': ['grp']}),
])
def test_explicit_required_parameters_use_the_normal_review_boundary(
        monkeypatch, tmp_path, query, algorithm, parameters):
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, parameters))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'explicit-parameter', 'message': query, 'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert plan['review_status'] == 'pending_review'
    assert len(prompts) == 1 and gis_calls == []


@pytest.mark.parametrize(('query', 'algorithm', 'parameters'), [
    ('请重投影到 EPSG:32651', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'}),
    ('输入 CRS 为 EPSG:32650，请重投影', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'}),
    ('重投影，名称为“重投影到 EPSG:32650”', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'}),
    ('重投影 mapEPSG:32650.geojson', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'}),
    ('按 kind 融合', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}),
    ('全部融合', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}),
    ('按 grp 融合', 'native:dissolve', {'FIELD': [], 'SEPARATE_DISJOINT': False}),
    ('融合，名称为“按 grp 融合”', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}),
    ('统计 other', 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'}),
    ('统计，名称为“统计 value”', 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'}),
    ('统计 stats_value.geojson', 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'}),
    ('按 kind 分类统计 value', 'qgis:statisticsbycategories',
     {'CATEGORIES_FIELD_NAME': ['grp'], 'VALUES_FIELD_NAME': 'value'}),
    ('按 grp 分类统计 other', 'qgis:statisticsbycategories',
     {'CATEGORIES_FIELD_NAME': ['grp'], 'VALUES_FIELD_NAME': 'value'}),
    ('按 grp 分类统计 value', 'qgis:statisticsbycategories', {'CATEGORIES_FIELD_NAME': ['grp']}),
    ('按 grp 分类统计，仅计数', 'qgis:statisticsbycategories',
     {'CATEGORIES_FIELD_NAME': ['grp'], 'VALUES_FIELD_NAME': 'value'}),
    ('不要按 grp 融合', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}),
    ('重投影到 EPSG:32650 或 EPSG:32651', 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'}),
])
def test_mismatched_or_metadata_only_parameter_is_not_authorized(
        monkeypatch, tmp_path, query, algorithm, parameters):
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, parameters))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'wrong-parameter', 'message': query, 'files': ['new-file'],
    })
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text
    assert len(prompts) in {0, 2} and gis_calls == []


def test_human_revisions_replace_only_the_explicit_choices_and_new_task_clears_them(monkeypatch, tmp_path):
    count = 0

    def proposal(source):
        nonlocal count
        count += 1
        return parameter_plan(source, 'qgis:statisticsbycategories', {
            'CATEGORIES_FIELD_NAME': ['grp' if count == 1 else 'kind'],
            'VALUES_FIELD_NAME': 'amount' if count >= 3 else 'value',
        })

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    first = event(client.post('/api/chat/stream', json={
        'thread_id': 'parameter-revisions', 'message': '按 grp 分类统计 value', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    for advice in ['分组字段改为 kind', '数值字段改为 amount']:
        assert client.post('/api/review', json={
            'thread_id': 'parameter-revisions', 'task_id': first['task_id'], 'revision': first['revision'],
            'approved': False, 'advise': advice,
        }).status_code == 200
        old = first
        first = event(client.post('/api/resume/parameter-revisions'), 'human_review_required')['task_plan']
        assert first['revision'] == old['revision'] + 1
        assert first['metadata']['parameter_requirements']['choices']['statistics'][0]['groups'] == ['kind']
        assert client.post('/api/review', json={
            'thread_id': 'parameter-revisions', 'task_id': old['task_id'], 'revision': old['revision'],
            'approved': True,
        }).status_code == 409
    # A separate task cannot borrow a previous task's human choices.
    response = client.post('/api/chat/stream', json={
        'thread_id': 'new-parameter-task', 'message': '请统计选中图层', 'files': ['new-file'],
    })
    assert 'event: human_review_required' not in response.text
    assert event(response, 'error')['message']
    assert len(prompts) == 5 and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('bad_place', [None, 'input_path', 'call_path', 'output_path', 'field', 'other_source'])
def test_literal_registered_source_name_is_the_only_placeholder_exception(
        monkeypatch, tmp_path, enabled, bad_place):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    filename = 'map${v}.geojson'

    def proposal(source):
        plan = load_plan(source)
        plan['required_inputs'][0]['source'] = filename
        plan['task_summary'] = f'加载 {filename}'
        plan['steps'][0]['description'] = f'显示 {filename}'
        plan['tool_calls'][0]['arguments']['name'] = filename
        if bad_place == 'input_path':
            plan['required_inputs'][0]['path'] = '${bad}'
        elif bad_place == 'call_path':
            plan['tool_calls'][0]['arguments']['path'] = '${bad}'
        elif bad_place == 'output_path':
            plan['expected_outputs'] = [{'output_id': 'result', 'description': 'bad', 'path': '${bad}.gpkg'}]
        elif bad_place == 'field':
            plan = parameter_plan(source, 'native:basicstatisticsforfields', {'FIELD_NAME': '${bad}'})
            plan['required_inputs'][0]['source'] = filename
        elif bad_place == 'other_source':
            plan['required_inputs'][0]['source'] = 'other${v}.geojson'
        return plan

    client, source, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal, filename=filename)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'literal-source', 'message': '加载图层', 'files': ['new-file'],
    })
    if bad_place:
        error = event(response, 'error')['message']
        assert 'placeholder' in error.lower() or 'variable' in error.lower()
        assert 'event: human_review_required' not in response.text
        assert len(prompts) == 2
    else:
        plan = event(response, 'human_review_required')['task_plan']
        assert plan['required_inputs'][0]['source'] == filename
        assert plan['required_inputs'][0]['path'] == str(source)
        assert plan['metadata']['skill_candidates']['provided'] == []
        assert len(prompts) == 1
    assert gis_calls == []


@pytest.mark.parametrize('repair', [True, False])
def test_revision_retry_keeps_checkpoint_choices_and_cannot_inherit_model_metadata(monkeypatch, tmp_path, repair):
    count = 0

    def proposal(source):
        nonlocal count
        count += 1
        plan = parameter_plan(source, 'qgis:statisticsbycategories', {
            'CATEGORIES_FIELD_NAME': ['grp' if count == 1 else 'kind'],
            'VALUES_FIELD_NAME': 'amount' if count >= 3 else 'value',
        })
        plan['metadata'] = {'parameter_requirements': {
            'task_id': 'forged', 'revision': 999,
            'choices': {'stat_groups': [['unrequested']], 'stat_values': ['unrequested']},
        }}
        if count == 3 or (count == 4 and not repair):
            plan['tool_calls'][0]['arguments']['parameters']['INPUT'] = '${unresolved}'
        return plan

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    first = event(client.post('/api/chat/stream', json={
        'thread_id': 'retry-choices', 'message': '按 grp 分类统计 value', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    for advice in ['分组字段改为 kind', '数值字段改为 amount']:
        assert client.post('/api/review', json={
            'thread_id': 'retry-choices', 'task_id': first['task_id'], 'revision': first['revision'],
            'approved': False, 'advise': advice,
        }).status_code == 200
        response = client.post('/api/resume/retry-choices')
        if count <= 2 or repair:
            first = event(response, 'human_review_required')['task_plan']
    if repair:
        assert first['revision'] == 3 and first['metadata']['iteration'] == 2
        requirements = first['metadata']['parameter_requirements']
        assert requirements['task_id'] == first['task_id'] and requirements['revision'] == 3
        assert requirements['choices']['statistics'][0]['groups'] == ['kind']
        assert requirements['choices']['statistics'][0]['values'] == ['amount']
    else:
        assert 'unresolved' in event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
        assert client.post('/api/review', json={
            'thread_id': 'retry-choices', 'task_id': first['task_id'], 'revision': first['revision'],
            'approved': True,
        }).status_code == 409
    assert len(prompts) == 4 and 'kind' in prompts[3]
    assert 'unrequested' not in prompts[3]
    assert gis_calls == []


@pytest.mark.parametrize(('query', 'algorithm', 'parameters'), [
    ('按 地类 字段融合', 'native:dissolve', {'FIELD': ['地类'], 'SEPARATE_DISJOINT': False}),
    ('按 grp、kind 融合', 'native:dissolve', {'FIELD': ['grp', 'kind'], 'SEPARATE_DISJOINT': False}),
    ('统计 金额 字段', 'native:basicstatisticsforfields', {'FIELD_NAME': '金额'}),
    ('Statistics for "land and value"', 'native:basicstatisticsforfields', {'FIELD_NAME': 'land and value'}),
    ('Dissolve by "a,b"', 'native:dissolve', {'FIELD': ['a,b'], 'SEPARATE_DISJOINT': False}),
])
def test_field_original_names_and_lists_are_preserved(monkeypatch, tmp_path, query, algorithm, parameters):
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, parameters))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'field-original-name', 'message': query, 'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert plan['review_status'] == 'pending_review'
    assert len(prompts) == 1 and gis_calls == []


def test_failed_task_choices_are_cleared_when_same_session_starts_another_task(monkeypatch, tmp_path):
    count = 0

    def proposal(source):
        nonlocal count
        count += 1
        plan = parameter_plan(source, 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'})
        if count <= 2:
            plan['tool_calls'][0]['arguments']['parameters']['INPUT_LAYER'] = '${bad}'
        return plan

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    first = client.post('/api/chat/stream', json={
        'thread_id': 'same-session-new-task', 'message': '统计 value', 'files': ['new-file'],
    })
    assert 'bad' in event(first, 'error')['message']
    response = client.post('/api/chat/stream', json={
        'thread_id': 'same-session-new-task', 'message': '请统计选中图层', 'files': ['new-file'],
    })
    assert '统计' in event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text
    assert len(prompts) == 4 and gis_calls == []


@pytest.mark.parametrize(('query', 'allowed'), [
    ('目标 CRS 为 EPSG:32650。请重投影到 EPSG:32650', True),
    ('Target CRS: EPSG:32650. Reproject to EPSG:32650', True),
    ('目标 CRS 为 EPSG:32651。请重投影到 EPSG:32650', False),
    ('输入 CRS 为 EPSG:32650。请重投影', False),
    ('重投影，显示名：“重投影到 EPSG:32650”', False),
    ('Reproject, display name: "Reproject to EPSG:32650"', False),
])
def test_repeated_target_declarations_and_literal_names_have_distinct_meanings(monkeypatch, tmp_path, query, allowed):
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path,
        lambda source: parameter_plan(source, 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'}))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'repeated-target', 'message': query, 'files': ['new-file'],
    })
    if allowed:
        assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
        assert len(prompts) == 1
    else:
        assert '目标 CRS' in event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text and len(prompts) == 2
    assert gis_calls == []


def test_buffer_distance_revision_survives_a_later_unrelated_human_opinion(monkeypatch, tmp_path):
    count = 0

    def proposal(source):
        nonlocal count
        count += 1
        return buffer_plan(source, distance=100 if count == 1 else 200)

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'buffer-choice-context', 'message': '缓冲100米', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    for advice in ['缓冲距离改为200米', '仅修改结果名称']:
        assert client.post('/api/review', json={
            'thread_id': 'buffer-choice-context', 'task_id': plan['task_id'], 'revision': plan['revision'],
            'approved': False, 'advise': advice,
        }).status_code == 200
        plan = event(client.post('/api/resume/buffer-choice-context'), 'human_review_required')['task_plan']
    assert plan['revision'] == 3
    assert plan['tool_calls'][0]['arguments']['parameters']['DISTANCE'] == 200
    assert len(prompts) == 3 and gis_calls == []


@pytest.mark.parametrize('advice', ['缓冲200', '缓冲距离改为200', '缓冲100米或200米'])
def test_unclear_buffer_opinion_exhausts_two_attempts_and_new_task_cannot_borrow_old_distance(
        monkeypatch, tmp_path, advice):
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: buffer_plan(source, distance=100))
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'buffer-opinion-missing', 'message': '缓冲100米', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'buffer-opinion-missing', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': advice,
    }).status_code == 200
    response = client.post('/api/resume/buffer-opinion-missing')
    assert '距离' in event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and len(prompts) == 3
    assert client.post('/api/review', json={
        'thread_id': 'buffer-opinion-missing', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': True,
    }).status_code == 409
    new_task = client.post('/api/chat/stream', json={
        'thread_id': 'buffer-opinion-missing', 'message': '请缓冲选中图层', 'files': ['new-file'],
    })
    assert '缓冲距离' in event(new_task, 'error')['message']
    assert 'event: human_review_required' not in new_task.text
    assert len(prompts) == 5 and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('query', ['不要全部融合', '不按 grp 字段融合', '不重投影到 EPSG:32650'])
def test_negated_business_operation_never_authorizes_its_positive_plan(monkeypatch, tmp_path, enabled, query):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    algorithm = 'native:reprojectlayer' if '重投影' in query else 'native:dissolve'
    parameters = {'TARGET_CRS': 'EPSG:32650'} if '重投影' in query else {
        'FIELD': ['grp'] if 'grp' in query else [], 'SEPARATE_DISJOINT': False,
    }
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, parameters))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'negative-second', 'message': query, 'files': ['new-file'],
    })
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


@pytest.mark.parametrize(('query', 'advice', 'proposal'), [
    ('重投影到 EPSG:32650', '目标换成 EPSG:32651',
     lambda source: parameter_plan(source, 'native:reprojectlayer', {'TARGET_CRS': 'EPSG:32650'})),
    ('缓冲100米', '请改到200米', lambda source: buffer_plan(source, distance=100)),
])
def test_changed_necessary_parameter_cannot_silently_inherit_old_value(monkeypatch, tmp_path, query, advice, proposal):
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'changed-second', 'message': query, 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'changed-second', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': advice,
    }).status_code == 200
    response = client.post('/api/resume/changed-second')
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_explicit_buffer_dissolve_cannot_be_overwritten_by_sample_false(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: buffer_plan(source, dissolve=False))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'buffer-bool-second', 'message': '缓冲125.5米，融合结果，每象限12段', 'files': ['new-file'],
    })
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


def same_family_plan(source, algorithm, parameter_sets):
    plan = parameter_plan(source, algorithm, parameter_sets[0])
    first_step, first_call = plan['steps'][0], plan['tool_calls'][0]
    plan['steps'], plan['tool_calls'], plan['expected_outputs'] = [], [], []
    for index, parameters in enumerate(parameter_sets, 1):
        output = f'result{index}'
        step = {**first_step, 'step_id': index, 'output_refs': [output]}
        call = {**first_call, 'step_id': index, 'arguments': {
            'algorithm': algorithm, 'parameters': {**first_call['arguments']['parameters'],
                                                 **parameters, 'OUTPUT': f'{output}.gpkg'},
        }}
        plan['steps'].append(step)
        plan['tool_calls'].append(call)
        plan['expected_outputs'].append({'output_id': output, 'description': output, 'path': f'{output}.gpkg'})
    return plan


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_two_explicit_statistics_keep_their_own_ordered_fields(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, lambda source: same_family_plan(
        source, 'native:basicstatisticsforfields', [{'FIELD_NAME': 'value1'}, {'FIELD_NAME': 'value2'}]))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'stats-sequence-second', 'message': '统计 value1 字段，然后统计 value2 字段',
        'files': ['new-file'],
    })
    assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    assert gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('tool_input', ['add_layer', 'INPUT', 'INPUT_LAYER', 'OVERLAY'])
@pytest.mark.parametrize('invalid', [None, 'input_path', 'layer_name', 'call_path', 'field', 'output', 'unrelated_metadata', 'task_summary', 'step_description', 'input_description', 'display_name'])
def test_registered_layer_and_direct_input_paths_are_literals_only_at_their_proven_locations(
        monkeypatch, tmp_path, enabled, tool_input, invalid):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    layer = 'parcel${v}'

    def proposal(source):
        qgis_path = f'{source}|layername={layer}'
        if tool_input == 'add_layer':
            plan = load_plan(qgis_path)
        else:
            algorithm = {'INPUT': 'native:buffer', 'INPUT_LAYER': 'native:basicstatisticsforfields',
                         'OVERLAY': 'native:clip'}[tool_input]
            if tool_input == 'INPUT':
                plan = buffer_plan(qgis_path, distance=100, dissolve=False)
            else:
                params = {'FIELD_NAME': 'value'} if tool_input == 'INPUT_LAYER' else {'OVERLAY': qgis_path}
                plan = parameter_plan(qgis_path, algorithm, params)
        plan['required_inputs'][0]['layer_name'] = layer
        plan['required_inputs'][0]['source'] = 'registered.gpkg'
        plan['task_summary'] = f'处理 registered.gpkg 中的 {layer}'
        plan['steps'][0]['description'] = f'加载 {layer}'
        plan['required_inputs'][0]['description'] = f'原图层 {layer}'
        if tool_input == 'add_layer':
            plan['tool_calls'][0]['arguments']['name'] = layer
        if tool_input == 'OVERLAY':
            plan['required_inputs'].append({**plan['required_inputs'][0], 'input_id': 'mask'})
            plan['steps'][0]['input_refs'] = ['input', 'mask']
        if invalid == 'input_path':
            plan['required_inputs'][0]['path'] = '${bad}'
        elif invalid == 'layer_name':
            plan['required_inputs'][0]['layer_name'] = 'parcel${bad}'
        elif invalid == 'call_path':
            if tool_input == 'add_layer':
                plan['tool_calls'][0]['arguments']['path'] = '${bad}'
            else:
                plan['tool_calls'][0]['arguments']['parameters'][tool_input] = '${bad}'
        elif invalid == 'field':
            plan['tool_calls'][0]['arguments']['FIELD'] = '${bad}'
        elif invalid == 'output':
            plan['expected_outputs'] = [{'output_id': 'bad', 'description': 'bad', 'path': '${bad}.gpkg'}]
        elif invalid == 'unrelated_metadata':
            plan['metadata'] = {'not_an_input': qgis_path}
        elif invalid == 'task_summary':
            plan['task_summary'] += ' ${bad}'
        elif invalid == 'step_description':
            plan['steps'][0]['description'] += ' ${bad}'
        elif invalid == 'input_description':
            plan['required_inputs'][0]['description'] += ' ${bad}'
        elif invalid == 'display_name':
            plan['tool_calls'][0]['arguments']['name'] = 'parcel${bad}'
        return plan

    client, source, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, proposal, storage_filename='registered.gpkg', layer_name=layer)
    query = {'add_layer': '加载图层', 'INPUT': '缓冲100米，不融合',
             'INPUT_LAYER': '统计 value 字段', 'OVERLAY': '裁剪选中的图层'}[tool_input]
    response = client.post('/api/chat/stream', json={
        'thread_id': 'registered-layer-second', 'message': query, 'files': ['new-file'],
    })
    if invalid:
        error = event(response, 'error')['message']
        assert 'variable' in error.lower() or 'placeholder' in error.lower()
        assert 'event: human_review_required' not in response.text and len(prompts) == 2
    else:
        plan = event(response, 'human_review_required')['task_plan']
        assert plan['required_inputs'][0]['layer_name'] == layer
        assert plan['required_inputs'][0]['path'] == f'{source}|layername={layer}'
        assert plan['metadata']['skill_candidates']['provided'] == []
        assert len(prompts) == 1
    assert gis_calls == []


@pytest.mark.parametrize(('query', 'algorithm', 'parameters'), [
    ('统计 value1 字段，然后统计 value2 字段', 'native:basicstatisticsforfields',
     [{'FIELD_NAME': 'value1'}, {'FIELD_NAME': 'value2'}]),
    ('按 grp 融合，然后按 kind 融合', 'native:dissolve',
     [{'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}, {'FIELD': ['kind'], 'SEPARATE_DISJOINT': False}]),
    ('按 grp 分类统计 value1，然后按 kind 分类统计 value2', 'qgis:statisticsbycategories',
     [{'CATEGORIES_FIELD_NAME': ['grp'], 'VALUES_FIELD_NAME': 'value1'},
      {'CATEGORIES_FIELD_NAME': ['kind'], 'VALUES_FIELD_NAME': 'value2'}]),
    ('重投影到 EPSG:32650，然后重投影到 EPSG:32651', 'native:reprojectlayer',
     [{'TARGET_CRS': 'EPSG:32650'}, {'TARGET_CRS': 'EPSG:32651'}]),
])
@pytest.mark.parametrize('change', ['correct', 'swap', 'omit', 'repeat'])
def test_repeated_families_match_count_order_and_each_parameters(monkeypatch, tmp_path, query, algorithm, parameters, change):
    proposed = list(parameters)
    if change == 'swap':
        proposed.reverse()
    elif change == 'omit':
        proposed = proposed[:1]
    elif change == 'repeat':
        proposed = [proposed[0], proposed[0]]
    client, _, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: same_family_plan(source, algorithm, proposed))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'repeated-families-second', 'message': query, 'files': ['new-file'],
    })
    if change == 'correct':
        assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
        assert len(prompts) == 1
    else:
        assert event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text and len(prompts) == 2
    assert gis_calls == []


@pytest.mark.parametrize(('query', 'dissolve', 'allowed'), [
    ('缓冲100米，不融合', False, True),
    ('缓冲100米，不融合', True, False),
    ('Buffer 100 meters, do not dissolve', False, True),
    ('Buffer 100 meters, do not dissolve', True, False),
    ('Buffer 100 meters, dissolve results', True, True),
    ('Buffer 100 meters, dissolve results', False, False),
    ('缓冲100米', False, True),
    ('缓冲100米', True, True),
    ('不要缓冲100米', False, False),
])
def test_explicit_buffer_boolean_is_checked_but_unspecified_option_stays_optional(
        monkeypatch, tmp_path, query, dissolve, allowed):
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: buffer_plan(source, distance=100, dissolve=dissolve))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'buffer-mode-second', 'message': query, 'files': ['new-file'],
    })
    if allowed:
        assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    else:
        assert event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    assert gis_calls == []


@pytest.mark.parametrize(('query', 'advice', 'old', 'new'), [
    ('重投影到 EPSG:32650', '目标换成 EPSG:32651', 'EPSG:32650', 'EPSG:32651'),
    ('缓冲100米', '请改到200米', 100, 200),
])
def test_common_explicit_edit_verbs_can_produce_a_new_valid_pending_revision(monkeypatch, tmp_path, query, advice, old, new):
    count = 0

    def proposal(source):
        nonlocal count
        count += 1
        value = old if count == 1 else new
        return (parameter_plan(source, 'native:reprojectlayer', {'TARGET_CRS': value})
                if isinstance(value, str) else buffer_plan(source, distance=value))

    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'edit-verb-positive', 'message': query, 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'edit-verb-positive', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': advice,
    }).status_code == 200
    revised = event(client.post('/api/resume/edit-verb-positive'), 'human_review_required')['task_plan']
    assert revised['revision'] == 2 and revised['review_status'] == 'pending_review'
    assert len(prompts) == 2 and gis_calls == []


@pytest.mark.parametrize('advice', ['目标另外选 EPSG:32651', '统计 value 换成 other'])
def test_unrecognized_parameter_edit_requires_clarification_instead_of_old_choices(monkeypatch, tmp_path, advice):
    algorithm = 'native:reprojectlayer' if 'EPSG' in advice else 'native:basicstatisticsforfields'
    params = {'TARGET_CRS': 'EPSG:32650'} if 'EPSG' in advice else {'FIELD_NAME': 'value'}
    query = '重投影到 EPSG:32650' if 'EPSG' in advice else '统计 value'
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, params))
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'unknown-edit-second', 'message': query, 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'unknown-edit-second', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': advice,
    }).status_code == 200
    response = client.post('/api/resume/unknown-edit-second')
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


def test_two_actual_reprojection_steps_with_same_target_are_not_duplicate_declarations(monkeypatch, tmp_path):
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, lambda source: same_family_plan(
        source, 'native:reprojectlayer', [{'TARGET_CRS': 'EPSG:32650'}, {'TARGET_CRS': 'EPSG:32650'}]))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'two-target-operations',
        'message': '目标 CRS 为 EPSG:32650。重投影到 EPSG:32650，然后重投影到 EPSG:32650',
        'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert plan['metadata']['parameter_requirements']['choices']['targets'] == ['EPSG:32650', 'EPSG:32650']
    assert gis_calls == []


@pytest.mark.parametrize('advice', ['名称换成“重投影到 EPSG:32651”', 'Display name changed to "Reproject to EPSG:32651"'])
@pytest.mark.parametrize('proposed_target', ['EPSG:32650', 'EPSG:32651'])
def test_name_edit_does_not_supply_or_replace_necessary_target(monkeypatch, tmp_path, advice, proposed_target):
    count = 0

    def proposal(source):
        nonlocal count
        count += 1
        return parameter_plan(source, 'native:reprojectlayer', {
            'TARGET_CRS': 'EPSG:32650' if count == 1 else proposed_target,
        })

    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'name-is-not-target-second', 'message': '重投影到 EPSG:32650', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'name-is-not-target-second', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': advice,
    }).status_code == 200
    response = client.post('/api/resume/name-is-not-target-second')
    if proposed_target == 'EPSG:32650':
        assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    else:
        assert event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    assert gis_calls == []


def buffer_then_dissolve_plan(source, *, independent=True):
    plan = buffer_plan(source, dissolve=not independent)
    if independent:
        plan['expected_outputs'].append({'output_id': 'dissolved', 'description': '全部融合', 'path': 'dissolved.gpkg'})
        plan['steps'].append({'step_id': 2, 'description': '全部融合缓冲结果', 'allowed_tools': ['execute_processing'],
                              'input_refs': ['buffered'], 'output_refs': ['dissolved'],
                              'risk_level': 'low', 'requires_review': False})
        plan['tool_calls'].append({'step_id': 2, 'tool_name': 'execute_processing', 'arguments': {
            'algorithm': 'native:dissolve', 'parameters': {'INPUT': 'fresh.gpkg', 'FIELD': [],
            'SEPARATE_DISJOINT': False, 'OUTPUT': 'dissolved.gpkg'}}})
    return plan


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('independent', [True, False])
@pytest.mark.parametrize('query', ['先缓冲125.5米，然后将结果全部融合', '先缓冲125.5米，再将结果全部融合', 'Buffer 125.5 meters, then dissolve all results'])
def test_explicit_then_dissolve_is_a_separate_required_call(monkeypatch, tmp_path, enabled, independent, query):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: buffer_then_dissolve_plan(source, independent=independent))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'buffer-independent-third', 'message': query, 'files': ['new-file'],
    })
    if independent:
        assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    else:
        assert event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    assert gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('query', ['加载图层并统计总数', '加载选中图层并统计要素数量'])
def test_chinese_total_feature_count_uses_explicit_load_plan(monkeypatch, tmp_path, enabled, query):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, load_plan)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'feature-count-third', 'message': query, 'files': ['new-file'],
    })
    assert event(response, 'human_review_required')['task_plan']['tool_calls'][0]['tool_name'] == 'add_layer'
    assert gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize(('query', 'algorithm', 'parameters'), [
    ('统计“不合格”字段', 'native:basicstatisticsforfields', {'FIELD_NAME': '不合格'}),
    ('按“不合格”字段融合', 'native:dissolve', {'FIELD': ['不合格'], 'SEPARATE_DISJOINT': False}),
    ('Statistics for "no" field', 'native:basicstatisticsforfields', {'FIELD_NAME': 'no'}),
])
def test_field_literal_with_negative_word_is_not_operation_negation(monkeypatch, tmp_path, enabled, query, algorithm, parameters):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, parameters))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'field-negative-word-third', 'message': query, 'files': ['new-file'],
    })
    assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    assert gis_calls == []


def reproject_buffer_load_plan(source):
    plan = buffer_plan(source, distance=250, dissolve=False)
    plan['expected_outputs'].insert(0, {'output_id': 'projected', 'description': '重投影', 'path': 'projected.gpkg'})
    plan['steps'][0].update(step_id=2, input_refs=['projected'])
    plan['tool_calls'][0].update(step_id=2)
    plan['tool_calls'][0]['arguments']['parameters'].update(INPUT='projected.gpkg', SEGMENTS=8)
    plan['steps'].insert(0, {'step_id': 1, 'description': '重投影', 'allowed_tools': ['execute_processing'],
                            'input_refs': ['input'], 'output_refs': ['projected'], 'risk_level': 'low', 'requires_review': False})
    plan['tool_calls'].insert(0, {'step_id': 1, 'tool_name': 'execute_processing', 'arguments': {
        'algorithm': 'native:reprojectlayer', 'parameters': {
            'INPUT': str(source), 'TARGET_CRS': 'EPSG:32650', 'OUTPUT': 'projected.gpkg'}}})
    plan['steps'].append({'step_id': 3, 'description': '加载最终缓冲结果', 'allowed_tools': ['add_layer'],
                          'input_refs': ['buffered'], 'output_refs': [], 'risk_level': 'low', 'requires_review': False})
    plan['tool_calls'].append({'step_id': 3, 'tool_name': 'add_layer', 'arguments': {
        'path': 'fresh.gpkg', 'layer_type': 'vector', 'name': 'T06 新数据250米'}})
    return plan


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_live_buffer_request_summary_mentions_do_not_create_extra_buffer_slots(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, reproject_buffer_load_plan, filename='t06_places.geojson')
    response = client.post('/api/chat/stream', json={
        'thread_id': 'live-buffer-02-third', 'files': ['new-file'],
        'message': '将 t06_places.geojson 先重投影到 EPSG:32650，再对每个点缓冲 250 米，每象限 8 段、不融合（DISSOLVE=false），最后只加载最终缓冲结果查看，显示名为 T06 新数据250米。只计划重投影、缓冲和加载三个步骤，两个空间产物为新的 GPKG。',
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert plan['metadata']['parameter_requirements']['choices']['buffer_dissolve'] == [False]
    assert plan['metadata']['parameter_requirements']['choices']['buffer_distances'] == [250]
    assert len(plan['tool_calls']) == 3 and gis_calls == []


def clip_dissolve_load_plan(source, mask):
    plan = parameter_plan(source, 'native:clip', {'OVERLAY': str(mask)})
    plan['required_inputs'].append({'input_id': 'mask', 'description': '裁剪面', 'file_id': 'mask-file', 'path': str(mask)})
    plan['steps'][0]['input_refs'] = ['input', 'mask']
    plan['steps'].extend([
        {'step_id': 2, 'description': '按zone融合', 'allowed_tools': ['execute_processing'],
         'input_refs': ['result'], 'output_refs': ['dissolved'], 'risk_level': 'low', 'requires_review': False},
        {'step_id': 3, 'description': '加载最终融合结果', 'allowed_tools': ['add_layer'],
         'input_refs': ['dissolved'], 'output_refs': [], 'risk_level': 'low', 'requires_review': False},
    ])
    plan['expected_outputs'].append({'output_id': 'dissolved', 'description': '融合产物', 'path': 'dissolved.gpkg'})
    plan['tool_calls'].extend([
        {'step_id': 2, 'tool_name': 'execute_processing', 'arguments': {
            'algorithm': 'native:dissolve', 'parameters': {'INPUT': 'result.gpkg', 'FIELD': ['zone'],
            'SEPARATE_DISJOINT': False, 'OUTPUT': 'dissolved.gpkg'}}},
        {'step_id': 3, 'tool_name': 'add_layer', 'arguments': {
            'path': 'dissolved.gpkg', 'layer_type': 'vector', 'name': '最终融合结果'}},
    ])
    return plan


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_live_clip_request_result_reference_does_not_add_dissolve_step(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    mask = tmp_path / 'mask.geojson'
    client, source, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: clip_dissolve_load_plan(source, mask))
    mask.write_bytes(source.read_bytes())
    def record(file_id):
        path = mask if file_id == 'mask-file' else source
        return {'file_id': file_id, 'filename': path.name, 'storage_path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'layers': [{
                    'layer_name': 'layer', 'crs': 'EPSG:32650', 'geometry': 'Polygon', 'is_2d': True}]}
    monkeypatch.setattr(backend, 'get_uploaded_file_record', record)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'live-clip-third', 'files': ['new-file', 'mask-file'],
        'message': '以 t06_regions.gpkg 为源，t06_window.gpkg 为裁剪面，先裁剪，再按 zone 字段融合（FIELD=["zone"]，SEPARATE_DISJOINT=false），最后只加载最终融合结果查看。保留分组键，非分组属性来自该组任一原始成员，不做属性求和。只计划这三步，空间输出都为新的 GPKG。',
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert plan['metadata']['parameter_requirements']['choices']['dissolves'] == [['zone']]
    assert len(plan['tool_calls']) == 3 and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_live_statistics_request_header_and_step_summary_do_not_add_statistics(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    def proposal(source):
        plan = same_family_plan(source, 'native:basicstatisticsforfields', [{'FIELD_NAME': 'amount'}, {'FIELD_NAME': 'amount'}])
        second = plan['tool_calls'][1]['arguments']
        second['algorithm'] = 'qgis:statisticsbycategories'
        second['parameters'].pop('INPUT_LAYER')
        second['parameters'].pop('FIELD_NAME')
        second['parameters'].update(INPUT=str(source), VALUES_FIELD_NAME='amount', CATEGORIES_FIELD_NAME=['zone'])
        return plan
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'live-stats-third', 'files': ['new-file'],
        'message': '对 t06_regions.gpkg 做两项统计：先对 amount 字段计算非空数、NULL数、和、最小和最大；再按 zone 字段分类统计同一个 amount 字段，给出每组总数与非空数。只执行这两项统计，各自 OUTPUT 到不同的新 GPKG 表。',
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert plan['metadata']['parameter_requirements']['choices']['statistics'] == [
        {'kind': 'basic', 'values': ['amount'], 'groups': None},
        {'kind': 'category', 'values': ['amount'], 'groups': ['zone']},
    ]
    assert len(plan['tool_calls']) == 2 and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('query', ['加载图层并统计总数', 'Load and count the selected layer', '只执行统计总数'])
def test_total_count_does_not_allow_plan_without_counting_evidence(monkeypatch, tmp_path, enabled, query):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    def proposal(_source):
        return {'task_id': 'model', 'task_summary': 'ping', 'risk_level': 'low', 'review_status': 'pending_review',
                'required_inputs': [], 'expected_outputs': [], 'steps': [{
                    'step_id': 1, 'description': 'ping', 'allowed_tools': ['ping'],
                    'input_refs': [], 'output_refs': [], 'risk_level': 'low', 'requires_review': False}],
                'tool_calls': [{'step_id': 1, 'tool_name': 'ping', 'arguments': {}}]}
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    response = client.post('/api/chat/stream', json={'thread_id': 'count-evidence-third', 'message': query, 'files': []})
    assert 'feature_count' in event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_total_count_does_not_authorize_sample_numeric_field(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, lambda source: parameter_plan(
        source, 'native:basicstatisticsforfields', {'FIELD_NAME': 'value'}))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'count-not-field-third', 'message': '统计要素总数', 'files': ['new-file'],
    })
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


@pytest.mark.parametrize('query', ['加载图层并统计总数', '加载选中图层并统计要素数量'])
@pytest.mark.parametrize('bad_count', [False, True])
def test_chinese_count_approval_uses_real_observation_and_independent_readback_seam(monkeypatch, tmp_path, query, bad_count):
    # Reuse T05's public approval/execution scenario and its independently fixed
    # count (3); only translate the public user message, leaving all assertions.
    from test_t05_workflow import test_public_load_count_has_independent_report_and_never_creates_file_artifact
    original_post = TestClient.post
    observed_queries = []
    def post(client, url, *args, **kwargs):
        body = kwargs.get('json')
        if url == '/api/chat/stream' and body and body.get('message') == 'Load and count the selected layer':
            kwargs['json'] = {**body, 'message': query}
            observed_queries.append(query)
        return original_post(client, url, *args, **kwargs)
    monkeypatch.setattr(TestClient, 'post', post)
    test_public_load_count_has_independent_report_and_never_creates_file_artifact(monkeypatch, tmp_path, bad_count)
    assert observed_queries == [query]


@pytest.mark.parametrize(('query', 'algorithm', 'params', 'allowed'), [
    ('统计‘不合格’字段', 'native:basicstatisticsforfields', {'FIELD_NAME': '不合格'}, True),
    ('统计不合格字段', 'native:basicstatisticsforfields', {'FIELD_NAME': '不合格'}, True),
    ('按不合格字段融合', 'native:dissolve', {'FIELD': ['不合格'], 'SEPARATE_DISJOINT': False}, True),
    ('No statistics for "no" field', 'native:basicstatisticsforfields', {'FIELD_NAME': 'no'}, False),
    ('不按“不合格”字段融合', 'native:dissolve', {'FIELD': ['不合格'], 'SEPARATE_DISJOINT': False}, False),
    ('不统计“不合格”字段', 'native:basicstatisticsforfields', {'FIELD_NAME': '不合格'}, False),
    ('“不要全部融合”', 'native:dissolve', {'FIELD': [], 'SEPARATE_DISJOINT': False}, False),
])
def test_only_confirmed_field_literals_are_shielded_from_operation_negation(monkeypatch, tmp_path, query, algorithm, params, allowed):
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, params))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'field-mask-boundary-third', 'message': query, 'files': ['new-file'],
    })
    if allowed:
        assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    else:
        assert event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    assert gis_calls == []


@pytest.mark.parametrize('independent', [True, False])
def test_explicit_new_dissolve_without_mode_still_requires_clarification(monkeypatch, tmp_path, independent):
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: buffer_then_dissolve_plan(source, independent=independent))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'missing-mode-after-then', 'message': '先缓冲125.5米，然后融合', 'files': ['new-file'],
    })
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_explicit_buffer_segments_cannot_be_overwritten_by_model_sample(monkeypatch, tmp_path, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    def proposal(source):
        plan = buffer_plan(source, distance=125.5, dissolve=True)
        plan['tool_calls'][0]['arguments']['parameters']['SEGMENTS'] = 8
        return plan
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'segments-explicit-third', 'message': '缓冲125.5米，融合结果，每象限12段', 'files': ['new-file'],
    })
    assert 'SEGMENTS' in event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


@pytest.mark.parametrize(('query', 'algorithm', 'params'), [
    ('统计 amount 字段。统计表输出为 report.gpkg。', 'native:basicstatisticsforfields', {'FIELD_NAME': 'amount'}),
    ('统计表输出字段', 'native:basicstatisticsforfields', {'FIELD_NAME': '表输出'}),
    ('按 grp 字段融合。融合结果输出为 result.gpkg。', 'native:dissolve', {'FIELD': ['grp'], 'SEPARATE_DISJOINT': False}),
    ('按“结果输出”字段融合', 'native:dissolve', {'FIELD': ['结果输出'], 'SEPARATE_DISJOINT': False}),
])
def test_output_noun_reference_is_not_an_operation_but_field_operand_is(monkeypatch, tmp_path, query, algorithm, params):
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, params))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'output-noun-final-third', 'message': query, 'files': ['new-file'],
    })
    assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    assert gis_calls == []


def two_buffer_segments_plan(source, segments):
    parameters = []
    for distance, count in zip([100, 200], segments):
        params = buffer_plan(source, distance=distance, dissolve=False)['tool_calls'][0]['arguments']['parameters']
        params['SEGMENTS'] = count
        parameters.append(params)
    plan = same_family_plan(source, 'native:buffer', parameters)
    for call in plan['tool_calls']:
        call['distance_unit'] = 'm'
    return plan


@pytest.mark.parametrize(('segments', 'explicit', 'allowed'), [
    ([8, 12], True, True), ([12, 8], True, False), ([8, 8], True, False),
    ([8, 12], False, True), ([12, 8], False, True),
])
def test_explicit_buffer_segments_match_order_while_unspecified_options_remain_optional(
        monkeypatch, tmp_path, segments, explicit, allowed):
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: two_buffer_segments_plan(source, segments))
    query = ('缓冲100米，每象限8段，然后缓冲200米，每象限12段' if explicit else '缓冲100米，然后缓冲200米')
    response = client.post('/api/chat/stream', json={
        'thread_id': 'segments-ordered-third', 'message': query, 'files': ['new-file'],
    })
    if allowed:
        plan = event(response, 'human_review_required')['task_plan']
        if explicit:
            assert plan['metadata']['parameter_requirements']['choices']['buffer_segments'] == [8, 12]
    else:
        assert 'SEGMENTS' in event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    assert gis_calls == []


def test_segments_human_revision_survives_distance_and_name_revisions(monkeypatch, tmp_path):
    count = 0
    def proposal(source):
        nonlocal count
        count += 1
        plan = buffer_plan(source, distance=100 if count <= 2 else 200, dissolve=False)
        plan['tool_calls'][0]['arguments']['parameters']['SEGMENTS'] = 12 if count == 1 else 8
        return plan
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'segments-revisions-third', 'message': '缓冲100米，每象限12段，不融合', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    for advice in ['SEGMENTS=8', '缓冲距离改为200米', '仅修改结果名称']:
        assert client.post('/api/review', json={
            'thread_id': 'segments-revisions-third', 'task_id': plan['task_id'], 'revision': plan['revision'],
            'approved': False, 'advise': advice,
        }).status_code == 200
        plan = event(client.post('/api/resume/segments-revisions-third'), 'human_review_required')['task_plan']
    choices = plan['metadata']['parameter_requirements']['choices']
    assert choices['buffer_segments'] == [8] and choices['buffer_distances'] == [200]
    assert choices['buffer_dissolve'] == [False] and plan['revision'] == 4
    assert gis_calls == []


def test_segments_failure_exhausts_two_attempts_and_new_task_does_not_inherit_it(monkeypatch, tmp_path):
    count = 0
    def proposal(source):
        nonlocal count
        count += 1
        plan = buffer_plan(source, distance=100, dissolve=False)
        plan['tool_calls'][0]['arguments']['parameters']['SEGMENTS'] = 16 if count >= 4 else 12
        return plan
    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'segments-new-task-third', 'message': '缓冲100米，每象限12段', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'segments-new-task-third', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': '每象限8段',
    }).status_code == 200
    failed = client.post('/api/resume/segments-new-task-third')
    assert 'SEGMENTS' in event(failed, 'error')['message'] and len(prompts) == 3
    response = client.post('/api/chat/stream', json={
        'thread_id': 'segments-new-task-third', 'message': '缓冲100米', 'files': ['new-file'],
    })
    new_plan = event(response, 'human_review_required')['task_plan']
    assert 'buffer_segments' not in new_plan['metadata']['parameter_requirements']['choices']
    assert new_plan['tool_calls'][0]['arguments']['parameters']['SEGMENTS'] == 16
    assert len(prompts) == 4 and gis_calls == []


@pytest.mark.parametrize('opinion', ['每象限8段或12段', 'SEGMENTS=8.5', '每象限段数改成八段'])
def test_ambiguous_or_unparsed_segments_edit_cannot_silently_keep_previous_integer(monkeypatch, tmp_path, opinion):
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: buffer_plan(source, distance=100, dissolve=False))
    plan = event(client.post('/api/chat/stream', json={
        'thread_id': 'segments-unknown-edit-third', 'message': '缓冲100米，每象限12段', 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'segments-unknown-edit-third', 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': opinion,
    }).status_code == 200
    response = client.post('/api/resume/segments-unknown-edit-third')
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize(('query', 'algorithm', 'parameters'), [
    ('统计 amount 字段，并统计要素总数', 'native:basicstatisticsforfields', {'FIELD_NAME': 'amount'}),
    ('按 zone 字段分类统计 amount 字段，并统计要素总数', 'qgis:statisticsbycategories',
     {'CATEGORIES_FIELD_NAME': ['zone'], 'VALUES_FIELD_NAME': 'amount'}),
])
def test_explicit_field_statistics_supply_total_from_their_verified_source_without_loading(
        monkeypatch, tmp_path, enabled, query, algorithm, parameters):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: parameter_plan(source, algorithm, parameters))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'field-stat-total-count', 'message': query, 'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert len(plan['tool_calls']) == 1 and plan['tool_calls'][0]['tool_name'] == 'execute_processing'
    assert gis_calls == []


def source_count_then_buffer_plan(source, include_source):
    plan = buffer_plan(source, distance=100, dissolve=True)
    if include_source:
        plan['steps'][0]['step_id'] = 2
        plan['tool_calls'][0]['step_id'] = 2
        first = load_plan(source)
        plan['steps'].insert(0, first['steps'][0])
        plan['tool_calls'].insert(0, first['tool_calls'][0])
    last_id = len(plan['steps']) + 1
    plan['steps'].append({'step_id': last_id, 'description': '加载缓冲结果', 'allowed_tools': ['add_layer'],
                          'input_refs': ['buffered'], 'output_refs': [], 'risk_level': 'low', 'requires_review': False})
    plan['tool_calls'].append({'step_id': last_id, 'tool_name': 'add_layer', 'arguments': {
        'path': 'fresh.gpkg', 'layer_type': 'vector', 'name': '缓冲结果'}})
    return plan


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('include_source', [True, False])
def test_independent_count_requires_its_source_not_any_final_loaded_layer(monkeypatch, tmp_path, enabled, include_source):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    client, _, _, gis_calls = setup_workflow(
        monkeypatch, tmp_path, lambda source: source_count_then_buffer_plan(source, include_source))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'independent-count-source', 'files': ['new-file'],
        'message': '先加载源图层并统计要素数量，然后缓冲100米并融合，最后加载缓冲结果',
    })
    if include_source:
        assert event(response, 'human_review_required')['task_plan']['review_status'] == 'pending_review'
    else:
        assert event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    assert gis_calls == []


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('keep_load', [False, True])
def test_real_statistics_review_can_remove_loading_without_negating_requested_statistics(monkeypatch, tmp_path, enabled, keep_load):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    count = 0
    def proposal(source):
        nonlocal count
        count += 1
        plan = same_family_plan(source, 'native:basicstatisticsforfields', [{'FIELD_NAME': 'amount'}, {'FIELD_NAME': 'amount'}])
        category = plan['tool_calls'][1]['arguments']
        category['algorithm'] = 'qgis:statisticsbycategories'
        category['parameters'].pop('INPUT_LAYER')
        category['parameters'].pop('FIELD_NAME')
        category['parameters'].update(INPUT=str(source), VALUES_FIELD_NAME='amount', CATEGORIES_FIELD_NAME=['zone'])
        if count == 1 or keep_load:
            for step, call in zip(plan['steps'], plan['tool_calls']):
                step['step_id'] += 1
                call['step_id'] += 1
            load = load_plan(source)
            plan['steps'].insert(0, load['steps'][0])
            plan['tool_calls'].insert(0, load['tool_calls'][0])
        return plan
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    first = event(client.post('/api/chat/stream', json={
        'thread_id': 'real-statistics-review-count', 'files': ['new-file'],
        'message': '对 t06_regions.gpkg 做两项统计：先对 amount 字段计算非空数、NULL数、和、最小和最大；再按 zone 字段分类统计同一个 amount 字段，给出每组总数与非空数。只执行这两项统计，各自 OUTPUT 到不同的新 GPKG 表。',
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'real-statistics-review-count', 'task_id': first['task_id'], 'revision': first['revision'],
        'approved': False,
        'advise': '不加载源图层或任何统计表到画布。删除第一步 add_layer。只保留两项 Processing：先 native:basicstatisticsforfields 统计 amount，再 qgis:statisticsbycategories 按 zone 分类统计 amount，分别输出新 GPKG 表。总数由经过验证的输入统计事实提供，无需加载计数。',
    }).status_code == 200
    response = client.post('/api/resume/real-statistics-review-count')
    if keep_load:
        assert 'Processing' in event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    else:
        revised = event(response, 'human_review_required')['task_plan']
        assert revised['revision'] == 2 and len(revised['tool_calls']) == 2
        assert all(call['tool_name'] == 'execute_processing' for call in revised['tool_calls'])
    assert gis_calls == []


@pytest.mark.parametrize('problem', ['wrong_field', 'missing_input', 'wrong_input', 'unknown_file', 'source_count_unavailable'])
@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_statistics_total_keeps_field_reference_and_source_preflight_checks(monkeypatch, tmp_path, problem, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    def proposal(source):
        plan = parameter_plan(source, 'native:basicstatisticsforfields', {'FIELD_NAME': 'amount'})
        if problem == 'wrong_field':
            plan['tool_calls'][0]['arguments']['parameters']['FIELD_NAME'] = 'value'
        elif problem == 'missing_input':
            plan['tool_calls'][0]['arguments']['parameters'].pop('INPUT_LAYER')
        elif problem == 'wrong_input':
            plan['tool_calls'][0]['arguments']['parameters']['INPUT_LAYER'] = str(source.parent / 'unselected.geojson')
        elif problem == 'unknown_file':
            plan['required_inputs'][0]['file_id'] = 'unknown-file'
        return plan
    def spatial_inspector(request):
        if problem == 'source_count_unavailable':
            return {'status': 'failed', 'checks': [], 'errors': ['统计来源总数不可用'], 'facts': {}}
        return fake_spatial_inspector(request)
    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal, spatial_inspector=spatial_inspector)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'stats-source-checks', 'message': '统计 amount 字段，并统计要素总数', 'files': ['new-file'],
    })
    error = event(response, 'error')['message']
    if problem == 'source_count_unavailable':
        assert '统计来源总数不可用' in error
    assert 'event: human_review_required' not in response.text
    assert len(prompts) == 2 and gis_calls == []


def buffer_statistics_plan(source):
    plan = buffer_plan(source, distance=100, dissolve=False)
    plan['expected_outputs'].append({'output_id': 'stats', 'description': '统计', 'path': 'stats.gpkg'})
    plan['steps'].append({'step_id': 2, 'description': '统计amount', 'allowed_tools': ['execute_processing'],
                          'input_refs': ['buffered'], 'output_refs': ['stats'], 'risk_level': 'low', 'requires_review': False})
    plan['tool_calls'].append({'step_id': 2, 'tool_name': 'execute_processing', 'arguments': {
        'algorithm': 'native:basicstatisticsforfields', 'parameters': {
            'INPUT_LAYER': 'fresh.gpkg', 'FIELD_NAME': 'amount', 'OUTPUT': 'stats.gpkg'}}})
    return plan


@pytest.mark.parametrize(('query', 'allowed'), [
    ('缓冲100米，不融合，然后统计 amount 字段，并统计要素总数', True),
    ('先加载源图层并统计要素数量，然后缓冲100米，不融合，再统计 amount 字段', False),
])
def test_statistics_total_is_bound_to_its_input_stage_not_another_input_count(monkeypatch, tmp_path, query, allowed):
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, buffer_statistics_plan)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'count-input-stage', 'message': query, 'files': ['new-file'],
    })
    if allowed:
        plan = event(response, 'human_review_required')['task_plan']
        assert len(plan['tool_calls']) == 2
        assert plan['metadata']['parameter_requirements']['choices']['feature_count_source'] == 'statistics'
    else:
        assert 'feature_count' in event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    assert gis_calls == []


def test_negative_loading_reference_does_not_hide_real_negative_statistics(monkeypatch, tmp_path):
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, lambda source: parameter_plan(
        source, 'native:basicstatisticsforfields', {'FIELD_NAME': 'amount'}))
    response = client.post('/api/chat/stream', json={
        'thread_id': 'view-reference-not-whitelist', 'message': '不加载统计表并不统计 amount 字段', 'files': ['new-file'],
    })
    assert event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []


def processing_pair_plan(source, extra=None):
    plan = same_family_plan(source, 'native:basicstatisticsforfields', [{'FIELD_NAME': 'amount'}, {'FIELD_NAME': 'amount'}])
    category = plan['tool_calls'][1]['arguments']
    category['algorithm'] = 'qgis:statisticsbycategories'
    category['parameters'].pop('INPUT_LAYER')
    category['parameters'].pop('FIELD_NAME')
    category['parameters'].update(INPUT=str(source), VALUES_FIELD_NAME='amount', CATEGORIES_FIELD_NAME=['zone'])
    if extra:
        arguments = ({'path': str(source), 'layer_type': 'vector', 'name': '源'} if extra == 'add_layer'
                     else {'path': 'shot.png'} if extra == 'capture_map_canvas' else {})
        outputs = ['shot'] if extra == 'capture_map_canvas' else []
        plan['steps'].append({'step_id': 3, 'description': extra, 'allowed_tools': [extra],
                              'input_refs': ['input'] if extra == 'add_layer' else [], 'output_refs': outputs,
                              'risk_level': 'low', 'requires_review': False})
        plan['tool_calls'].append({'step_id': 3, 'tool_name': extra, 'arguments': arguments})
        if outputs:
            plan['expected_outputs'].append({'output_id': 'shot', 'description': '截图', 'path': 'shot.png'})
    return plan


_PROCESSING_PAIR_QUERY = '统计 amount 字段，然后按 zone 分类统计 amount 字段，只保留两项 Processing'


@pytest.mark.parametrize('restriction', ['只保留两项 Processing', '只执行两步 Processing', '只计划两步 Processing'])
@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('extra', [None, 'add_layer', 'capture_map_canvas', 'ping'])
def test_processing_only_constraint_survives_name_revision(monkeypatch, tmp_path, extra, restriction, enabled):
    monkeypatch.setenv('QGIS_AGENT_SKILLS_ENABLED', enabled)
    count = 0
    def proposal(source):
        nonlocal count
        count += 1
        return processing_pair_plan(source, extra if count >= 2 else None)
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    first = event(client.post('/api/chat/stream', json={
        'thread_id': 'processing-only-name', 'message': _PROCESSING_PAIR_QUERY.replace('只保留两项 Processing', restriction), 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'processing-only-name', 'task_id': first['task_id'], 'revision': first['revision'],
        'approved': False, 'advise': '名称改为更新后的统计',
    }).status_code == 200
    response = client.post('/api/resume/processing-only-name')
    if extra:
        assert 'Processing' in event(response, 'error')['message']
        assert 'event: human_review_required' not in response.text
    else:
        revised = event(response, 'human_review_required')['task_plan']
        assert revised['metadata']['parameter_requirements']['choices']['processing_only_count'] == 2
    assert gis_calls == []


def test_processing_only_constraint_clears_for_new_task_after_two_failed_attempts(monkeypatch, tmp_path):
    count = 0
    def proposal(source):
        nonlocal count
        count += 1
        return load_plan(source) if count >= 4 else processing_pair_plan(source, 'add_layer' if count >= 2 else None)
    client, _, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, proposal)
    first = event(client.post('/api/chat/stream', json={
        'thread_id': 'processing-only-new-task', 'message': _PROCESSING_PAIR_QUERY, 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'processing-only-new-task', 'task_id': first['task_id'], 'revision': first['revision'],
        'approved': False, 'advise': '仅修改名称',
    }).status_code == 200
    assert 'Processing' in event(client.post('/api/resume/processing-only-new-task'), 'error')['message']
    assert len(prompts) == 3
    response = client.post('/api/chat/stream', json={
        'thread_id': 'processing-only-new-task', 'message': '加载图层', 'files': ['new-file'],
    })
    plan = event(response, 'human_review_required')['task_plan']
    assert 'processing_only_count' not in plan['metadata']['parameter_requirements']['choices']
    assert plan['tool_calls'][0]['tool_name'] == 'add_layer'
    assert len(prompts) == 4 and gis_calls == []


@pytest.mark.parametrize('advice', [
    '只保留两项 Processing，同时保留 add_layer',
    '取消 Processing 限制',
    '只保留三项 Processing',
    '只保留若干项 Processing',
])
def test_conflicting_or_unparsed_processing_only_change_requires_clarification(monkeypatch, tmp_path, advice):
    client, _, _, gis_calls = setup_workflow(monkeypatch, tmp_path, processing_pair_plan)
    first = event(client.post('/api/chat/stream', json={
        'thread_id': 'processing-only-clarification', 'message': _PROCESSING_PAIR_QUERY, 'files': ['new-file'],
    }), 'human_review_required')['task_plan']
    assert client.post('/api/review', json={
        'thread_id': 'processing-only-clarification', 'task_id': first['task_id'], 'revision': first['revision'],
        'approved': False, 'advise': advice,
    }).status_code == 200
    response = client.post('/api/resume/processing-only-clarification')
    assert 'Processing' in event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text and gis_calls == []
