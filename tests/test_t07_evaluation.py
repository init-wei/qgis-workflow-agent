"""T07 evaluation CLI keeps the fixed experiment denominator and raw evidence."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/evaluate_t07.py'


def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=ROOT,
                          env={**os.environ, 'PYTHON_DOTENV_DISABLED': '1', 'DATABASE_URL': ''},
                          text=True, capture_output=True)


def definition(tmp_path):
    path = tmp_path / 'definition.json'
    requests = [{'request_id': f'{family}-{scenario}-{phrase}', 'scenario_id': f'{family}-{scenario}',
                 'family': family, 'split': 'heldout', 'message': f'固定请求{family}', 'datasets': []}
                for family in ('load', 'reproject', 'buffer', 'clip', 'dissolve', 'statistics')
                for scenario in (1, 2) for phrase in (1, 2)]
    path.write_text(json.dumps({'schema_version': 1, 'requests': requests}), encoding='utf-8')
    return path


def test_public_schedule_freezes_96_abba_slots_without_starting_services(tmp_path):
    output = tmp_path / 'experiment'
    response = cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
                   '--experiment-id', 'offline', '--model', 'fake-model')
    assert response.returncode == 0, response.stderr
    manifest = json.loads((output / 'manifest.json').read_text())
    assert len(manifest['schedule']) == 96
    assert len({slot['slot_id'] for slot in manifest['schedule']}) == 96
    for index in range(0, 96, 4):
        block = manifest['schedule'][index:index + 4]
        assert [(row['arm'], row['repeat']) for row in block] == [('A0', 1), ('A1', 1), ('A1', 2), ('A0', 2)]
        assert len({row['request_id'] for row in block}) == 1
    assert not (output / 'results.jsonl').exists()
    again = cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
                '--experiment-id', 'offline', '--model', 'fake-model')
    assert again.returncode != 0


def test_public_aggregate_preserves_failed_empty_interrupted_not_run_and_missing_usage(tmp_path):
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    manifest = json.loads((output / 'manifest.json').read_text())
    slots = manifest['schedule']
    rows = [
        {**slots[0], 'event': 'started', 'attempt_id': 'empty', 'experiment_id': 'offline'},
        {**slots[0], 'event': 'finished', 'attempt_id': 'empty', 'experiment_id': 'offline',
         'task_id': 't-empty', 'status': 'complete_empty', 'first_plan_valid': True,
         'first_complete': True, 'final_complete': True, 'human_change': None},
        {**slots[1], 'event': 'started', 'attempt_id': 'failed', 'experiment_id': 'offline'},
        {**slots[1], 'event': 'finished', 'attempt_id': 'failed', 'experiment_id': 'offline',
         'task_id': 't-failed', 'status': 'planning_failed', 'first_plan_valid': False,
         'first_complete': False, 'final_complete': False},
        {**slots[2], 'event': 'started', 'attempt_id': 'interrupted', 'experiment_id': 'offline'},
    ]
    (output / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    response = cli('aggregate', '--manifest', output / 'manifest.json')
    assert response.returncode == 0, response.stderr
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['arms']['A0']['planned'] == 48
    assert summary['arms']['A1']['planned'] == 48
    assert summary['arms']['A0']['final_complete'] == {'n': 1, 'N': 48, 'percent': 100 / 48, 'unknown': 47}
    assert summary['statuses']['complete_empty'] == 1
    assert summary['statuses']['planning_failed'] == 1
    assert summary['statuses']['environment_interrupted'] == 1
    assert summary['statuses']['not_run'] == 93
    assert summary['human_change_rate'] == {'n': 0, 'N': 0, 'rate': None, 'percent': None, 'unknown': 0}
    assert summary['usage']['prompt_tokens']['total'] is None
    assert (output / 'results.csv').is_file()


def test_aggregate_rejects_duplicate_final_facts_and_foreign_identity(tmp_path):
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    slot = json.loads((output / 'manifest.json').read_text())['schedule'][0]
    row = {**slot, 'event': 'finished', 'attempt_id': 'duplicate', 'experiment_id': 'offline', 'status': 'execution_failed'}
    (output / 'results.jsonl').write_text((json.dumps(row) + '\n') * 2)
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode != 0
    row['experiment_id'] = 'other-experiment'
    (output / 'results.jsonl').write_text(json.dumps(row) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode != 0


@pytest.mark.parametrize(('first_plan', 'recorded_complete', 'expected'), [(True, True, None), (False, None, False), (None, False, None)])
def test_public_aggregate_unfinished_checkpoint_keeps_first_attempt_provenance(tmp_path, first_plan, recorded_complete, expected):
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    slot = json.loads((output / 'manifest.json').read_text())['schedule'][0]
    row = {**slot, 'event': 'checkpoint', 'attempt_id': 'unfinished', 'experiment_id': 'offline',
           'status': 'complete', 'first_plan_valid': first_plan, 'first_complete': recorded_complete,
           'final_complete': True}
    (output / 'results.jsonl').write_text(json.dumps(row) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode == 0
    import csv
    with (output / 'results.csv').open() as stream:
        actual = next(csv.DictReader(stream))
    assert actual['status'] == 'environment_interrupted' and actual['final_complete'] == ''
    assert actual['first_complete'] == ('' if expected is None else 'False')


def http_experiment(tmp_path, monkeypatch, *, oracle_passed=True, wrong_name=False, unknown=False):
    """A public HTTP double; no project graph, database, GIS or network runs."""
    import importlib.util
    import hashlib
    import httpx

    spec = importlib.util.spec_from_file_location('t07_runner', SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    source = tmp_path / 'points.geojson'
    source.write_text('{"type":"FeatureCollection","features":[]}')
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    requests = [{'request_id': f'{family}-{scenario}-{phrase}', 'scenario_id': f'{family}-{scenario}',
                 'family': family, 'split': 'heldout', 'message': '加载 {points}', 'datasets': ['points'],
                 'expected': {'algorithms': [], 'loads': [{'source': 'input', 'name': '固定名称'}],
                              'output_filenames': []}, 'expected_candidate_families': ['load']}
                for family in ('load', 'reproject', 'buffer', 'clip', 'dissolve', 'statistics')
                for scenario in (1, 2) for phrase in (1, 2)]
    definition_path = tmp_path / 'definition.json'
    definition_path.write_text(json.dumps({'schema_version': 1, 'requests': requests}))
    folder = tmp_path / 'experiment'
    assert runner.main(['schedule', '--definition', str(definition_path), '--output-dir', str(folder),
                        '--experiment-id', 'http-offline', '--model', 'fake-model']) is None
    input_manifest = {'definition_sha256': runner.digest(definition_path),
                      'datasets': {'points': {'filename': source.name, 'layer': 'points', 'sha256': checksum}}}
    frozen = runner.read_json(folder / 'manifest.json')
    for key in ('oracle_sha256', 'generator_sha256', 'snyder_helper_sha256'):
        input_manifest[key] = frozen.get(key)
    (tmp_path / 'manifest.json').write_text(json.dumps(input_manifest))
    plan = {'task_id': 'actual-task', 'revision': 3, 'review_status': 'pending_review',
            'required_inputs': [{'input_id': 'source', 'file_id': 'upload-id', 'path': str(source), 'sha256': checksum}],
            'expected_outputs': [], 'steps': [{'step_id': 1, 'input_refs': ['source'], 'output_refs': []}],
            'tool_calls': [{'step_id': 1, 'tool_name': 'add_layer', 'requires_review': False,
                            'arguments': {'path': str(source), 'name': 'wrong' if wrong_name else '固定名称', 'layer_type': 'vector'}}],
            'metadata': {'iteration': 1, 'skill_candidates': {'enabled': False, 'provided': []}}}
    state = {'stage': 'initial', 'confirmed': None, 'requests': [], 'plan': plan}

    def handler(request):
        path = request.url.path
        body = json.loads(request.content) if request.content and 'json' in request.headers.get('content-type', '') else None
        state['requests'].append((request.method, path, body))
        if path == '/api/sessions' and request.method == 'GET':
            return httpx.Response(200, json=[])
        if path == '/api/upload':
            return httpx.Response(200, json={'file_id': 'upload-id', 'sha256': checksum, 'filename': source.name})
        if path == '/api/sessions':
            return httpx.Response(200, json={'thread_id': 'actual-thread'})
        if path == '/api/chat/stream':
            plan['review_status'] = 'pending_review'
            if state.get('next_layer_name'):
                plan['tool_calls'][0]['arguments']['name'] = state['next_layer_name']
            state['stage'] = 'plan'
            return httpx.Response(200, text='event: start\ndata: {}\n\nevent: human_review_required\ndata: {}\n\n')
        if path == '/api/state/actual-thread':
            pause = {'kind': 'plan_review', 'task_id': 'actual-task', 'revision': 3} if state['stage'] == 'plan' else (
                {'kind': 'result_review', 'task_id': 'actual-task', 'revision': 3, 'review_id': 'current-result-id'}
                if state['stage'] == 'result' and not unknown else None)
            return httpx.Response(200, json={'state': {'task_id': 'actual-task',
                                            'task_plan': None if state['stage'] == 'complete' else plan,
                                            'planning_failure_kind': None,
                                            'planning_attempt_diagnostics': [{'attempt': 1, 'failure_kind': None, 'model_response_received': True}],
                                            'final_summary': '已完成' if state['stage'] == 'complete' else None},
                                            'next': [] if state['stage'] == 'complete' else ['planner_node'] if state['stage'] == 'rejected' else ['pending'],
                                            'can_start_task': state['stage'] == 'complete',
                                            'pause': pause, 'running': False,
                                            'continuation': {'kind': 'resume', 'task_id': 'actual-task', 'revision': 3} if state['stage'] == 'rejected' else None})
        if path == '/api/review':
            assert body['task_id'] == 'actual-task' and body['revision'] == 3
            state['stage'] = 'approved' if body['approved'] else 'rejected'
            if not body['approved']:
                plan['review_status'] = 'rejected'
            return httpx.Response(200, json={'status': 'success'})
        if path == '/api/resume/actual-thread':
            assert state['stage'] in {'approved', 'confirmed'}
            state['stage'] = 'result' if state['stage'] == 'approved' else 'complete'
            return httpx.Response(200, text='event: start\ndata: {}\n\nevent: paused\ndata: {}\n\n')
        if path == '/api/executions/actual-task':
            if state['stage'] == 'plan':
                return httpx.Response(404, json={})
            return httpx.Response(200, json={'execution': {'task_id': 'actual-task',
                'status': 'rejected' if state['stage'] == 'rejected' else 'needs_attention' if unknown else 'success', 'plan': plan,
                'attempts': [] if state['stage'] == 'rejected' else [{'step_id': 1, 'status': 'success', 'observation': {'outcome': 'unknown' if unknown else 'completed'}}],
                'plan_revision': 3, 'inflight_call': None, 'artifacts': [], 'confirmed': state['confirmed']}})
        if path == '/api/confirm-result':
            assert body['review_id'] == 'current-result-id'
            state.update(stage='confirmed', confirmed=body['is_completed'])
            return httpx.Response(200, json={'status': 'success'})
        raise AssertionError(f'Unexpected HTTP call {request.method} {path}')

    monkeypatch.setattr(runner, 'verify_result', lambda *_: {
        'status': 'passed' if oracle_passed else 'failed', 'empty': oracle_passed, 'checks': [],
        'errors': [] if oracle_passed else ['known wrong geometry']})
    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    arguments = ['run', '--manifest', str(folder / 'manifest.json'), '--inputs-dir', str(tmp_path),
                 '--arm', 'A0', '--limit', '1']
    return runner, client, arguments, folder, state, source


def test_public_run_uses_current_review_identity_and_confirms_independent_empty_result(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, source = http_experiment(tmp_path, monkeypatch)
    original = source.read_bytes()
    assert runner.main(arguments, client=client) == 0
    rows = runner.journal(folder / 'results.jsonl')
    final = rows[-1]
    assert final['event'] == 'finished' and final['status'] == 'complete_empty'
    assert final['first_plan_valid'] is True and final['final_complete'] is True
    assert state['confirmed'] is True
    assert source.read_bytes() == original
    assert len([item for item in state['requests'] if item[1] == '/api/chat/stream']) == 1
    final_slot = final['slot_id']
    assert runner.main(arguments + ['--resume'], client=client) == 0
    assert len([row for row in runner.journal(folder / 'results.jsonl')
                if row['slot_id'] == final_slot and row['event'] == 'started']) == 1


def test_public_run_does_not_approve_a_request_mismatched_plan(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)
    assert runner.main(arguments, client=client) == 0
    assert runner.journal(folder / 'results.jsonl')[-1]['status'] == 'invalid_plan'
    assert [body['approved'] for _, path, body in state['requests'] if path == '/api/review'] == [False]
    assert not any(path.startswith('/api/resume') for _, path, _ in state['requests'])
    assert state['confirmed'] is None


def test_public_run_never_confirms_an_oracle_failure_as_success(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, oracle_passed=False)
    assert runner.main(arguments, client=client) == 0
    final = runner.journal(folder / 'results.jsonl')[-1]
    assert final['status'] == 'oracle_failed' and final['false_success'] is True
    assert final['final_complete'] is False and state['confirmed'] is False


def test_public_run_stops_and_refuses_to_replay_unknown_writes(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, unknown=True)
    assert runner.main(arguments, client=client) == 1
    assert runner.journal(folder / 'results.jsonl')[-1]['status'] == 'unknown_outcome'
    count = len([item for item in state['requests'] if item[1] == '/api/chat/stream'])
    assert runner.main(arguments + ['--resume'], client=client) == 1
    assert len([item for item in state['requests'] if item[1] == '/api/chat/stream']) == count
    assert state['confirmed'] is None


def test_usage_raw_null_total_and_actual_zero_remain_distinct(tmp_path):
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    slot = json.loads((output / 'manifest.json').read_text())['schedule'][0]
    (output / 'results.jsonl').write_text(json.dumps({**slot, 'experiment_id': 'offline',
        'event': 'finished', 'attempt_id': 'one', 'task_id': 'task', 'status': 'planning_failed',
        'expected_graph_invocations': 1, 'graph_invocation_count_known': True}) + '\n')
    (output / 'usage.jsonl').write_text(json.dumps({'event': 'graph_start', 'task_id': 'task', 'invocation_id': 'graph'}) + '\n' + json.dumps({'event': 'graph_end', 'task_id': 'task', 'invocation_id': 'graph', 'model_run_ids': ['run'], 'model_start_count': 1, 'unidentified_model_events': 0, 'write_error_count': 0}) + '\n' + json.dumps({'event': 'start', 'task_id': 'task', 'run_id': 'run', 'invocation_id': 'graph'}) + '\n' + json.dumps({'event': 'end', 'invocation_id': 'graph', 'usage_source': 'provider_response_metadata.token_usage', 'task_id': 'task', 'run_id': 'run',
        'usage': {'prompt_tokens': 7, 'completion_tokens': 0, 'total_tokens': None,
                  'prompt_cache_hit_tokens': 2, 'prompt_cache_miss_tokens': 5}}) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode == 0
    usage = json.loads((output / 'summary.json').read_text())['usage']
    assert usage['prompt_tokens']['total'] == 7
    assert usage['completion_tokens']['total'] == 0
    assert usage['total_tokens']['total'] is None


def test_managed_run_interleaves_abba_and_only_restarts_for_arm_changes(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    changes = []

    class OwnedBackend:
        def __init__(self, *_):
            self.arm = None
            self.restarts = 0
            self.process = None

        def switch(self, arm):
            if arm != self.arm:
                changes.append(arm)
                self.restarts += 1
            self.arm = arm
            state['plan']['metadata']['skill_candidates']['enabled'] = arm == 'A1'
            state['confirmed'] = None

        def stop(self):
            self.arm = None

    arguments = arguments[:-4] + ['--manage-backend', '--limit', '4']
    assert runner.main(arguments, client=client, backend_factory=OwnedBackend) == 0
    assert changes == ['A0', 'A1', 'A0']
    rows = [row for row in runner.journal(folder / 'results.jsonl') if row['event'] == 'finished']
    assert [(row['arm'], row['repeat']) for row in rows] == [('A0', 1), ('A1', 1), ('A1', 2), ('A0', 2)]
    assert runner.journal(folder / 'runtime.jsonl')[-1]['backend_starts'] == 3


def test_interrupted_model_call_poisoned_fields_are_unknown_not_partial_totals(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('t07_usage_runner', SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    complete = [{'event': 'start', 'task_id': 'task', 'run_id': 'one'},
                {'event': 'end', 'usage_source': 'provider_response_metadata.token_usage', 'task_id': 'task', 'run_id': 'one', 'usage': {'prompt_tokens': 7, 'completion_tokens': 0}}]
    unfinished = {'event': 'start', 'task_id': 'task', 'run_id': 'two'}
    usage = runner.usage_for('task', complete + [unfinished])
    assert usage['fields']['prompt_tokens'] == {'total': None, 'known_partial_sum': 7, 'known_calls': 1, 'expected_calls': 2, 'field_complete': False}
    assert usage['fields']['total_tokens']['total'] is None
    orphan_end = [{'event': 'end', 'usage_source': 'provider_response_metadata.token_usage', 'task_id': 'task', 'run_id': 'orphan', 'usage': {'prompt_tokens': 99}}]
    assert runner.usage_for('task', orphan_end)['fields']['prompt_tokens']['total'] is None


def test_public_rerun_keeps_original_failure_and_links_new_attempt(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)
    assert runner.main(arguments, client=client) == 0
    original = runner.journal(folder / 'results.jsonl')[-1]
    state['next_layer_name'] = '固定名称'
    assert runner.main(arguments + ['--rerun', original['slot_id']], client=client) == 0
    finals = [row for row in runner.journal(folder / 'results.jsonl') if row['event'] == 'finished']
    assert len(finals) == 2 and finals[0]['status'] == 'invalid_plan'
    assert finals[1]['rerun_of'] == original['attempt_id'] and finals[1]['status'] == 'complete_empty'
    summary = runner.read_json(folder / 'summary.json')
    assert summary['reruns'] == 1
    assert summary['arms']['A0']['final_complete']['n'] == 0


def test_pricing_with_missing_cache_split_only_produces_range():
    import importlib.util
    spec = importlib.util.spec_from_file_location('t07_price_runner', SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    rows = [{'event': 'start', 'task_id': 'task', 'run_id': 'one'},
            {'event': 'end', 'usage_source': 'provider_response_metadata.token_usage', 'task_id': 'task', 'run_id': 'one',
             'usage': {'prompt_tokens': 1000000, 'completion_tokens': 0}}]
    pricing = {'source_url': 'https://example.invalid/prices', 'fetched_at': '2026-10-31T00:00:00Z',
               'requested_model': 'fake-model', 'provider_response_models': ['provider-v1'], 'usd_per_million': {'off_peak': {'cache_hit': .003, 'cache_miss': .15, 'output': .6},
                                  'peak': {'cache_hit': .006, 'cache_miss': .3, 'output': 1.2}}}
    for row in rows:
        row['invocation_id'] = 'graph'
        row['requested_model'] = 'fake-model'
        if row['event'] == 'end':
            row['provider_response_model'] = 'provider-v1'
    rows += [{'event': 'graph_start', 'task_id': 'task', 'invocation_id': 'graph'},
             {'event': 'graph_end', 'task_id': 'task', 'invocation_id': 'graph', 'model_run_ids': ['one'],
              'model_start_count': 1, 'unidentified_model_events': 0, 'write_error_count': 0}]
    cost = runner.cost_for(runner.usage_for('task', rows, 1), pricing)
    assert cost['cost_usd'] is None
    assert cost['cost_estimate_range_usd'] == [.003, .3]


def test_disconnected_sse_reads_snapshot_without_repeating_submission(tmp_path, monkeypatch):
    import httpx
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    original_stream = runner.stream_request
    first = True

    def disconnected(*args, **kwargs):
        nonlocal first
        original_stream(*args, **kwargs)
        if first:
            first = False
            raise httpx.ReadError('synthetic disconnection')

    monkeypatch.setattr(runner, 'stream_request', disconnected)
    assert runner.main(arguments, client=client) == 0
    assert len([item for item in state['requests'] if item[1] == '/api/chat/stream']) == 1
    assert runner.journal(folder / 'results.jsonl')[-1]['status'] == 'complete_empty'
    assert any(row.get('transport_interrupted') for row in runner.journal(folder / 'results.jsonl'))


def test_resume_existing_result_pause_keeps_task_and_does_not_repeat_gis(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    assert runner.main(arguments, client=client) == 0
    rows = runner.journal(folder / 'results.jsonl')
    approved_index = next(index for index, row in enumerate(rows) if row.get('approved') is True)
    (folder / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows[:approved_index + 1]))
    state.update(stage='result', confirmed=None)
    before = len(state['requests'])
    assert runner.main(arguments + ['--resume'], client=client) == 0
    new_calls = state['requests'][before:]
    assert not any(path == '/api/chat/stream' or path == '/api/review' or
                   (method == 'POST' and path == '/api/sessions') for method, path, _ in new_calls)
    final = runner.journal(folder / 'results.jsonl')[-1]
    assert final['attempt_id'] == rows[0]['attempt_id']
    assert final['task_id'] == 'actual-task' and final['status'] == 'complete_empty'


def test_exception_and_browser_cases_without_evidence_remain_not_run(tmp_path):
    definition_path = definition(tmp_path)
    spec = json.loads(definition_path.read_text())
    spec.update(exceptions=[{'id': 'unknown-write', 'input': 'timeout', 'expected': 'no replay'}],
                browser=[{'id': 'refresh', 'input': 'refresh page', 'expected': 'same review'}])
    definition_path.write_text(json.dumps(spec))
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition_path, '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode == 0
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['special_cases']['exceptions']['not_run'] == 1
    assert summary['special_cases']['browser']['not_run'] == 1
    (output / 'special-results.jsonl').write_text(json.dumps({'experiment_id': 'offline',
        'case_id': 'refresh', 'group': 'browser', 'status': 'passed'}) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode != 0


@pytest.mark.parametrize('loss', ['producer_error', 'read_error'])
def test_transport_or_producer_error_without_durable_planning_failure_stops_unknown(tmp_path, monkeypatch, loss):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)

    def handler(request):
        if request.url.path == '/api/chat/stream':
            if loss == 'read_error':
                raise httpx.ReadError('transport interrupted before any durable task')
            return httpx.Response(200, text='event: error\ndata: {"message":"producer failed"}\n\n')
        if request.url.path == '/api/state/actual-thread':
            return httpx.Response(200, json={'running': False, 'state': {}, 'pause': None, 'continuation': None})
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 1
    last = runner.journal(folder / 'results.jsonl')[-1]
    assert last['status'] == 'environment_interrupted'
    assert last['first_plan_valid'] is None and last['final_complete'] is None
    assert last['event'] == 'checkpoint'


def test_disconnected_approved_continuation_is_not_a_known_failure_or_idle(tmp_path, monkeypatch):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)

    def handler(request):
        path = request.url.path
        if path.startswith('/api/resume'):
            raise httpx.ReadError('response lost after approved resume')
        if path == '/api/state/actual-thread' and state['stage'] == 'approved':
            return httpx.Response(200, json={'running': False, 'state': {'task_id': 'actual-task', 'task_plan': state['plan']},
                'pause': None, 'continuation': {'kind': 'resume', 'task_id': 'actual-task', 'revision': 3}})
        if path == '/api/executions/actual-task' and state['stage'] == 'approved':
            return httpx.Response(200, json={'execution': {'status': 'approved', 'attempts': []}})
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 1
    last = runner.journal(folder / 'results.jsonl')[-1]
    assert last['status'] in {'environment_interrupted', 'unknown_outcome'} and last['final_complete'] is None
    assert runner.main(arguments + ['--rerun', last['slot_id']], client=client) == 1


def test_aggregate_rejects_unlinked_second_primary_attempt(tmp_path):
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    slot = json.loads((output / 'manifest.json').read_text())['schedule'][0]
    rows = [{**slot, 'experiment_id': 'offline', 'event': 'finished', 'attempt_id': identity,
             'status': 'execution_failed', 'final_complete': False} for identity in ('one', 'two')]
    (output / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode != 0


def test_oracle_unavailable_keeps_result_unconfirmed_and_unknown(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    # Model/GIS success is known; a missing verifier report is infrastructure loss.
    monkeypatch.setattr(runner, 'verify_result', lambda *_: {'status': 'unavailable', 'checks': [], 'errors': []})
    assert runner.main(arguments, client=client) == 1
    final = runner.journal(folder / 'results.jsonl')[-1]
    assert final['status'] == 'environment_interrupted'
    assert final.get('false_success') is None and final['final_complete'] is None
    assert state['confirmed'] is None


def test_dry_run_rejects_catalog_source_inventory_and_snyder_drift(tmp_path, monkeypatch):
    import shutil
    runner, _, _, folder, _, _ = http_experiment(tmp_path, monkeypatch)
    manifest = runner.read_json(folder / 'manifest.json')
    sandbox = tmp_path / 'runtime-copy'
    paths = set(manifest['source_sha256']) | {f'agent/skills/{name}' for name in manifest['templates']}
    paths |= {'uv.lock', 'scripts/build_spatial_oracle.py', 'scripts/build_t07_fixtures.py',
              'tests/fixtures/gis/t07_benchmark_oracle.json'}
    for name in paths:
        original = ROOT / name
        if original.is_file():
            destination = sandbox / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
    monkeypatch.setattr(runner, 'ROOT', sandbox)
    arguments = ['dry-run', '--manifest', str(folder / 'manifest.json')]
    assert isinstance(runner.main(arguments), dict)
    extra_template = sandbox / 'agent/skills/unexpected.json'
    extra_template.write_text((sandbox / 'agent/skills/load.json').read_text())
    assert runner.main(arguments) == 1
    extra_template.unlink()
    extra_source = sandbox / 'agent/unexpected_module.py'
    extra_source.write_text('# post-freeze source\n')
    assert runner.main(arguments) == 1
    extra_source.unlink()
    removed = sandbox / next(iter(manifest['source_sha256']))
    original_bytes = removed.read_bytes()
    removed.unlink()
    assert runner.main(arguments) == 1
    removed.write_bytes(original_bytes)
    helper = sandbox / 'scripts/build_spatial_oracle.py'
    helper.write_bytes(helper.read_bytes() + b'\n# post-freeze mathematical helper\n')
    assert runner.main(arguments) == 1


def test_materialized_helper_mismatch_rejected_before_any_http(tmp_path, monkeypatch):
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    materialization = tmp_path / 'manifest.json'
    manifest = runner.read_json(materialization)
    manifest['snyder_helper_sha256'] = '0' * 64
    materialization.write_text(json.dumps(manifest))
    assert runner.main(arguments, client=client) == 1
    assert not state['requests']


def audited_usage(*, extra_missing_run=False):
    rows = [
        {'event': 'graph_start', 'task_id': 'task', 'invocation_id': 'graph'},
        {'event': 'start', 'task_id': 'task', 'invocation_id': 'graph', 'run_id': 'one'},
        {'event': 'end', 'task_id': 'task', 'invocation_id': 'graph', 'run_id': 'one',
         'usage_source': 'provider_response_metadata.token_usage',
         'usage': {'prompt_tokens': 7, 'completion_tokens': 0, 'total_tokens': None,
                   'prompt_cache_hit_tokens': 2, 'prompt_cache_miss_tokens': 5}},
        {'event': 'graph_end', 'task_id': 'task', 'invocation_id': 'graph',
         'model_run_ids': ['one'], 'model_start_count': 1, 'unidentified_model_events': 0, 'write_error_count': 0},
    ]
    if extra_missing_run:
        rows.append({'event': 'start', 'task_id': 'task', 'invocation_id': 'graph', 'run_id': None})
    return rows


def test_public_aggregate_requires_independent_graph_and_model_journal_completeness(tmp_path):
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    slot = json.loads((output / 'manifest.json').read_text())['schedule'][0]
    result = {**slot, 'experiment_id': 'offline', 'event': 'finished', 'attempt_id': 'one',
              'task_id': 'task', 'status': 'complete', 'planning_attempts': 2,
              'expected_graph_invocations': 1, 'graph_invocation_count_known': True}

    def aggregate(events, changes=None):
        (output / 'results.jsonl').write_text(json.dumps({**result, **(changes or {})}) + '\n')
        (output / 'usage.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
        response = cli('aggregate', '--manifest', output / 'manifest.json')
        assert response.returncode == 0, response.stderr
        return json.loads((output / 'summary.json').read_text())

    # Two planning attempts can include pre-LLM failures; the graph audit, not the
    # attempt count, proves this one model call and the missing raw total.
    complete = aggregate(audited_usage())
    assert complete['journal_integrity_complete'] is True
    assert complete['usage']['prompt_tokens']['total'] == 7
    assert complete['usage']['completion_tokens']['total'] == 0
    assert complete['usage']['total_tokens']['total'] is None
    cases = [
        (audited_usage(extra_missing_run=True), None),
        (audited_usage(), {'expected_graph_invocations': 2}),
        (audited_usage()[:-1], None),
        ([row for row in audited_usage() if row['event'] in {'graph_start', 'graph_end'}], None),
        (audited_usage(), {'expected_graph_invocations': None, 'graph_invocation_count_known': False}),
    ]
    corrupt_summary = audited_usage()
    corrupt_summary[-1]['model_run_ids'].append('lost-entire-model')
    corrupt_summary[-1]['model_start_count'] = 2
    cases.append((corrupt_summary, None))
    writer_failed = audited_usage()
    writer_failed[-1]['write_error_count'] = 1
    cases.append((writer_failed, None))
    for events, changes in cases:
        summary = aggregate(events, changes)
        assert summary['journal_integrity_complete'] is False
        assert summary['usage']['prompt_tokens']['total'] is None
        assert summary['arms']['A0']['cost']['observed_usage_estimate_range_usd'] is None
        assert summary['usage_integrity'][0]['errors']


def test_fixed_counterexample_denominators_are_separate_and_require_explicit_metric(tmp_path):
    path = definition(tmp_path)
    spec = json.loads(path.read_text())
    spec['exceptions'] = [{'id': 'wrong-geometry', 'metrics': ['false_success']},
                          {'id': 'unapproved', 'metrics': ['unapproved_execution']}]
    path.write_text(json.dumps(spec))
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', path, '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    slot = json.loads((output / 'manifest.json').read_text())['schedule'][0]
    (output / 'results.jsonl').write_text(json.dumps({**slot, 'experiment_id': 'offline',
        'event': 'finished', 'attempt_id': 'one', 'status': 'complete', 'false_success': False}) + '\n')
    evidence = output / 'evidence.json'
    evidence.write_text('{\"observation\":true}')
    import hashlib
    (output / 'special-results.jsonl').write_text(json.dumps({'experiment_id': 'offline',
        'group': 'exceptions', 'case_id': 'wrong-geometry', 'status': 'passed',
        'evidence_refs': [{'path': str(evidence), 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}]}) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode == 0
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['normal_claimed_success']['false_success'] == {'n': 0, 'N': 1, 'percent': 0, 'unknown': 0}
    assert summary['fixed_counterexamples']['false_success'] == {'n': 0, 'N': 1, 'percent': None, 'unknown': 1}
    assert summary['fixed_counterexamples']['unapproved_execution']['unknown'] == 1


def test_public_run_missing_oracle_process_report_preserves_pending_confirmation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    runner, client, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    # Restore the actual subprocess boundary, then simulate a crashed checker.
    import importlib.util
    spec = importlib.util.spec_from_file_location('actual_checker_runner', SCRIPT)
    unpatched = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(unpatched)
    monkeypatch.setattr(runner, 'verify_result', unpatched.verify_result)
    monkeypatch.setattr(unpatched.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(returncode=2))
    assert runner.main(arguments, client=client) == 1
    final = runner.journal(folder / 'results.jsonl')[-1]
    assert final['status'] == 'environment_interrupted' and final['final_complete'] is None
    assert final.get('false_success') is None and final['business_claimed_success'] is True
    assert state['confirmed'] is None
    assert runner.read_json(folder / 'summary.json')['normal_claimed_success']['false_success']['unknown'] == 1


def test_untyped_planning_error_exhaustion_remains_unknown(tmp_path, monkeypatch):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)

    def handler(request):
        if request.url.path == '/api/chat/stream':
            return httpx.Response(200, text='event: start\ndata: {}\n\nevent: error\ndata: {}\n\n')
        if request.url.path == '/api/state/actual-thread':
            return httpx.Response(200, json={'running': False, 'state': {
                'task_id': 'actual-task', 'planning_error': 'initial plan validation exhausted', 'retry_count': 2},
                'pause': None, 'continuation': None})
        if request.url.path == '/api/executions/actual-task':
            return httpx.Response(404, json={})
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 1
    final = runner.journal(folder / 'results.jsonl')[-1]
    assert final['status'] == 'environment_interrupted' and final['first_plan_valid'] is None
    assert final['expected_graph_invocations'] == 1 and final['graph_invocation_count_known'] is True


def test_every_http_graph_observation_is_unknown_until_its_start_is_durable(tmp_path, monkeypatch):
    runner, client, arguments, folder, _, _ = http_experiment(tmp_path, monkeypatch)
    stream = runner.stream_request

    def observed(*args, **kwargs):
        last = runner.journal(folder / 'results.jsonl')[-1]
        assert last['graph_invocation_count_known'] is False
        assert last['expected_graph_invocations'] is None and last['graph_invocation_inflight'] is True
        return stream(*args, **kwargs)

    monkeypatch.setattr(runner, 'stream_request', observed)
    assert runner.main(arguments, client=client) == 0
    last = runner.journal(folder / 'results.jsonl')[-1]
    assert last['expected_graph_invocations'] == 3
    assert last['graph_invocation_count_known'] is True


@pytest.mark.parametrize('fault', ['read_error', 'producer_error', 'pause_remaining'])
def test_after_result_confirmation_workflow_must_reach_public_terminal_state(tmp_path, monkeypatch, fault):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)

    def handler(request):
        if request.url.path == '/api/resume/actual-thread' and state['stage'] == 'confirmed':
            state['stage'] = 'reflector_pending'
            if fault == 'read_error':
                raise httpx.ReadError('lost final response')
            return httpx.Response(200, text='event: start\ndata: {}\n\nevent: error\ndata: {}\n\n')
        if request.url.path == '/api/state/actual-thread' and state['stage'] == 'reflector_pending':
            return httpx.Response(200, json={'running': False, 'state': {'task_id': 'actual-task'},
                'next': ['reflector_node'], 'can_start_task': False,
                'pause': {'kind': 'result_review'} if fault == 'pause_remaining' else None,
                'continuation': None if fault == 'pause_remaining' else {'kind': 'resume'}})
        if request.url.path == '/api/sessions' and state['stage'] == 'reflector_pending':
            return httpx.Response(200, json=[{'thread_id': 'actual-thread'}])
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 1
    row = runner.journal(folder / 'results.jsonl')[-1]
    assert row['status'] == 'environment_interrupted' and row['event'] == 'checkpoint'
    assert row['final_complete'] is None
    assert row['oracle_status'] == 'passed' and row['result_confirmed'] is True
    with pytest.raises(ValueError):
        runner.assert_idle(client)


def test_rerun_cannot_skip_another_slots_unresolved_result_checkpoint(tmp_path, monkeypatch):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)
    assert runner.main(arguments, client=original) == 0
    failed = runner.journal(folder / 'results.jsonl')[-1]
    slot = runner.read_json(folder / 'manifest.json')['schedule'][1]
    pending = {**slot, 'experiment_id': 'http-offline', 'attempt_id': 'another-pending', 'attempt_index': 1,
               'rerun_of': None, 'thread_id': 'blocked-thread', 'task_id': 'blocked-task'}
    runner.append(folder / 'results.jsonl', {**pending, 'event': 'started'})
    runner.append(folder / 'results.jsonl', {**pending, 'event': 'checkpoint', 'status': 'environment_interrupted'})

    def handler(request):
        if request.url.path == '/api/sessions' and request.method == 'GET':
            return httpx.Response(200, json=[{'thread_id': 'blocked-thread'}])
        if request.url.path == '/api/state/blocked-thread':
            return httpx.Response(200, json={'running': False, 'state': {'task_id': 'blocked-task'},
                'pause': {'kind': 'result_review'}, 'continuation': None, 'next': ['reflector_node'], 'can_start_task': False})
        if request.url.path == '/api/executions/blocked-task':
            return httpx.Response(200, json={'execution': {'status': 'success', 'confirmed': None, 'attempts': []}})
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    before = len(state['requests'])
    assert runner.main(arguments + ['--rerun', failed['slot_id']], client=client) == 1
    assert not any(method == 'POST' for method, _, _ in state['requests'][before:])
    assert len([row for row in runner.journal(folder / 'results.jsonl') if row['event'] == 'started']) == 2


@pytest.mark.parametrize('fault', ['unknown_group', 'foreign_experiment', 'missing_hash', 'wrong_hash'])
def test_public_aggregate_rejects_unverified_special_metric_observations(tmp_path, fault):
    path = definition(tmp_path)
    definition_value = json.loads(path.read_text())
    definition_value['exceptions'] = [{'id': 'wrong-geometry', 'metrics': ['false_success']}]
    path.write_text(json.dumps(definition_value))
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', path, '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    evidence = tmp_path / 'actual-report.json'
    evidence.write_text('{"known":"observed"}')
    import hashlib
    observation = {'experiment_id': 'offline', 'group': 'exceptions', 'case_id': 'wrong-geometry',
                   'status': 'passed', 'false_success': False,
                   'evidence_refs': [{'path': str(evidence), 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
    if fault == 'unknown_group':
        observation.update(group='unknown-group', experiment_id='foreign')
    elif fault == 'foreign_experiment':
        observation['experiment_id'] = 'foreign'
    elif fault == 'missing_hash':
        observation['evidence_refs'][0].pop('sha256')
    elif fault == 'wrong_hash':
        observation['evidence_refs'][0]['sha256'] = '0' * 64
    (output / 'special-results.jsonl').write_text(json.dumps(observation) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode != 0


def test_public_aggregate_orphan_model_event_poisoning_is_global(tmp_path):
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    slot = json.loads((output / 'manifest.json').read_text())['schedule'][0]
    (output / 'results.jsonl').write_text(json.dumps({**slot, 'experiment_id': 'offline',
        'event': 'finished', 'attempt_id': 'one', 'task_id': 'task', 'status': 'complete',
        'expected_graph_invocations': 1, 'graph_invocation_count_known': True}) + '\n')
    events = audited_usage() + [{'event': 'start', 'run_id': 'orphan-without-owner'}]
    (output / 'usage.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode == 0
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['journal_integrity_complete'] is False
    assert summary['usage']['prompt_tokens']['total'] is None
    assert summary['usage']['prompt_tokens']['known_partial_sum'] == 7


@pytest.mark.parametrize('pause_kind', ['plan_review', 'risk_review', 'result_review', 'continuation', 'running'])
def test_public_rerun_checks_other_finished_slots_public_workflow_state(tmp_path, monkeypatch, pause_kind):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)
    assert runner.main(arguments, client=original) == 0
    failed = runner.journal(folder / 'results.jsonl')[-1]
    other = runner.read_json(folder / 'manifest.json')['schedule'][1]
    runner.append(folder / 'results.jsonl', {**other, 'experiment_id': 'http-offline', 'attempt_id': 'other-finished',
                  'attempt_index': 1, 'event': 'finished', 'thread_id': 'other-thread', 'task_id': 'other-task',
                  'status': 'complete', 'final_complete': True})

    def handler(request):
        if request.url.path == '/api/state/other-thread':
            return httpx.Response(200, json={'running': pause_kind == 'running', 'can_start_task': False,
                'state': {'task_id': 'other-task'}, 'next': ['pending'],
                'pause': {'kind': pause_kind} if pause_kind.endswith('review') else None,
                'continuation': {'kind': 'resume'} if pause_kind == 'continuation' else None})
        if request.url.path == '/api/executions/other-task':
            return httpx.Response(200, json={'execution': {'status': 'success', 'confirmed': True, 'attempts': []}})
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    before = len(state['requests'])
    assert runner.main(arguments + ['--rerun', failed['slot_id']], client=client) == 1
    assert not any(method == 'POST' for method, _, _ in state['requests'][before:])


def test_final_transport_loss_after_public_terminal_completion_does_not_replay(tmp_path, monkeypatch):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)

    def handler(request):
        if request.url.path == '/api/resume/actual-thread' and state['stage'] == 'confirmed':
            original._transport.handle_request(request)
            raise httpx.ReadError('late loss after completion')
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 0
    row = runner.journal(folder / 'results.jsonl')[-1]
    assert row['status'] == 'complete_empty' and row['workflow_finished'] is True
    assert row['transport_interrupted'] is True and row['expected_graph_invocations'] is None
    assert row['graph_invocation_count_known'] is False
    assert len([item for item in state['requests'] if item[1].startswith('/api/resume')]) == 2


def test_special_hash_proves_snapshot_and_groups_cannot_cross_pollute_fixed_metrics(tmp_path):
    import hashlib
    path = definition(tmp_path)
    spec = json.loads(path.read_text())
    spec['exceptions'] = [{'id': 'same-id', 'metrics': ['false_success']}]
    spec['browser'] = [{'id': 'same-id', 'metrics': []}]
    path.write_text(json.dumps(spec))
    output = tmp_path / 'experiment'
    assert cli('schedule', '--definition', path, '--output-dir', output,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    evidence = output / 'record.json'
    evidence.write_text('{"observed":true}')
    observation = {'experiment_id': 'offline', 'group': 'browser', 'case_id': 'same-id', 'status': 'passed',
                   'false_success': False, 'evidence_refs': [{'path': 'record.json', 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
    (output / 'special-results.jsonl').write_text(json.dumps(observation) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode == 0
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['fixed_counterexamples']['false_success']['unknown'] == 1
    assert summary['special_cases']['browser']['passed'] == 1
    observation['group'] = 'exceptions'
    (output / 'special-results.jsonl').write_text(json.dumps(observation) + '\n')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode == 0
    assert json.loads((output / 'summary.json').read_text())['fixed_counterexamples']['false_success']['unknown'] == 0
    evidence.write_text('{"observed":"changed after acceptance"}')
    assert cli('aggregate', '--manifest', output / 'manifest.json').returncode != 0


@pytest.mark.parametrize(('kinds', 'responses', 'exit_code', 'first', 'status'), [
    (['invalid_plan', 'invalid_plan'], [True, True], 0, False, 'planning_failed'),
    (['environment_error', 'invalid_plan'], [False, True], 0, None, 'planning_failed'),
    (['request_rejected', 'request_rejected'], [False, False], 0, None, 'planning_failed'),
    (['invalid_plan', 'environment_error'], [True, False], 1, False, 'environment_interrupted'),
    (['unknown', 'unknown'], [False, False], 1, None, 'environment_interrupted'),
])
def test_structured_planning_provenance_distinguishes_request_plan_and_environment(
        tmp_path, monkeypatch, kinds, responses, exit_code, first, status):
    import httpx
    runner, original, arguments, folder, _, _ = http_experiment(tmp_path, monkeypatch)
    records = [{'attempt': index + 1, 'failure_kind': kind, 'model_response_received': response}
               for index, (kind, response) in enumerate(zip(kinds, responses))]

    def handler(request):
        if request.url.path == '/api/chat/stream':
            return httpx.Response(200, text='event: start\ndata: {}\n\nevent: error\ndata: {}\n\n')
        if request.url.path == '/api/state/actual-thread':
            return httpx.Response(200, json={'running': False, 'can_start_task': True, 'next': [],
                'pause': None, 'continuation': None, 'state': {'task_id': 'actual-task', 'task_plan': None,
                'planning_error': 'opaque detail is not used as a classifier', 'retry_count': 2,
                'planning_failure_kind': kinds[-1], 'planning_attempt_diagnostics': records}})
        if request.url.path == '/api/executions/actual-task':
            return httpx.Response(404, json={})
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == exit_code
    row = runner.journal(folder / 'results.jsonl')[-1]
    assert row['status'] == status and row['first_plan_valid'] is first
    assert row['planning_attempt_diagnostics'] == records
    if status == 'planning_failed':
        assert row['final_complete'] is False and row['failure_class'] == kinds[-1]
    assert row['first_complete'] is (False if first is False else None)


@pytest.mark.parametrize(('prior', 'first'), [('environment_error', None), ('invalid_plan', False)])
def test_second_plan_success_keeps_first_attempt_provenance(tmp_path, monkeypatch, prior, first):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    state['plan']['metadata']['iteration'] = 2
    records = [{'attempt': 1, 'failure_kind': prior, 'model_response_received': prior == 'invalid_plan'},
               {'attempt': 2, 'failure_kind': None, 'model_response_received': True}]

    def handler(request):
        response = original._transport.handle_request(request)
        if request.url.path == '/api/state/actual-thread':
            snapshot = response.json()
            snapshot['state']['planning_attempt_diagnostics'] = records
            return httpx.Response(200, json=snapshot)
        return response

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 0
    row = runner.journal(folder / 'results.jsonl')[-1]
    assert row['first_plan_valid'] is first and row['first_complete'] is first
    assert row['final_complete'] is True


@pytest.mark.parametrize(('prior', 'first'), [
    ('environment_error', None), ('request_rejected', None), ('unknown', None), ('missing', None),
    ('invalid_plan', False), ('valid', True),
])
@pytest.mark.parametrize('outcome', ['contract_rejected', 'gis_failed', 'oracle_failed', 'interrupted',
                                     'oracle_failed_interrupted', 'resume_failed'])
def test_public_failure_outcomes_preserve_first_attempt_unknown(tmp_path, monkeypatch, prior, first, outcome):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(
        tmp_path, monkeypatch, wrong_name=outcome == 'contract_rejected', oracle_passed=not outcome.startswith('oracle_failed'))
    if prior == 'missing':
        records = []
    elif prior == 'valid':
        records = [{'attempt': 1, 'failure_kind': None, 'model_response_received': True}]
    else:
        records = [{'attempt': 1, 'failure_kind': prior, 'model_response_received': prior == 'invalid_plan'},
                   {'attempt': 2, 'failure_kind': None, 'model_response_received': True}]
        state['plan']['metadata']['iteration'] = 2

    def handler(request):
        response = original._transport.handle_request(request)
        if request.url.path == '/api/state/actual-thread':
            snapshot = response.json()
            snapshot['state']['planning_attempt_diagnostics'] = records
            if state['stage'] == 'result' and outcome in {'gis_failed', 'interrupted', 'resume_failed'}:
                snapshot.update(pause=None, next=[])
                if outcome == 'interrupted' or (outcome == 'resume_failed' and not state.get('resume_inspected')):
                    snapshot['continuation'] = {'kind': 'resume', 'task_id': 'actual-task', 'revision': 3}
            if state['stage'] == 'complete' and outcome == 'oracle_failed_interrupted':
                snapshot.update(next=['reflector_node'], can_start_task=False,
                    continuation={'kind': 'resume', 'task_id': 'actual-task', 'revision': 3})
            return httpx.Response(200, json=snapshot)
        if (request.url.path == '/api/executions/actual-task' and state['stage'] == 'result'
                and (outcome == 'gis_failed' or outcome == 'resume_failed' and state.get('resume_inspected'))):
            body = response.json()
            body['execution']['status'] = 'failed'
            return httpx.Response(200, json=body)
        return response

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    interrupted = outcome in {'interrupted', 'oracle_failed_interrupted', 'resume_failed'}
    assert runner.main(arguments, client=client) == (1 if interrupted else 0)
    if outcome == 'resume_failed':
        state['resume_inspected'] = True
        assert runner.main(arguments + ['--resume'], client=client) == 0
        interrupted = False
    row = runner.journal(folder / 'results.jsonl')[-1]
    # A first generated plan that mismatches the request is itself invalid.
    expected_first = False if prior == 'valid' and outcome == 'contract_rejected' else first
    assert row['first_plan_valid'] is expected_first
    expected_complete = False if expected_first is False or (
        expected_first is True and (not interrupted or outcome == 'oracle_failed_interrupted')) else None
    assert row['first_complete'] is expected_complete
    assert row['final_complete'] is (None if interrupted else False)
    for entry in runner.journal(folder / 'results.jsonl'):
        if entry.get('first_plan_valid') is None:
            assert entry.get('first_complete') is None


def test_audited_zero_model_calls_are_explicitly_not_applicable_not_synthetic_raw_zero(tmp_path, monkeypatch):
    runner, _, _, _, _, _ = http_experiment(tmp_path, monkeypatch)
    events = [{'event': 'graph_start', 'task_id': 'task', 'invocation_id': 'graph'},
              {'event': 'graph_end', 'task_id': 'task', 'invocation_id': 'graph', 'model_run_ids': [],
               'model_start_count': 0, 'unidentified_model_events': 0, 'write_error_count': 0}]
    usage = runner.usage_for('task', events, 1)
    assert usage['journal_integrity_complete'] is True and usage['no_model_calls'] is True
    assert all(field['total'] is None for field in usage['fields'].values())
    assert runner.cost_for(usage, None)['cost_status'] == 'not_applicable_no_model_calls'
    assert runner.usage_for('task', [], 1)['no_model_calls'] is False


def bound_pricing():
    return {'source_url': 'https://example.invalid/synthetic-prices', 'fetched_at': '2026-10-31T00:00:00Z',
            'requested_model': 'fake-model', 'provider_response_models': ['provider-v1'],
            'usd_per_million': {'off_peak': {'cache_hit': .003, 'cache_miss': .15, 'output': .6},
                                'peak': {'cache_hit': .006, 'cache_miss': .3, 'output': 1.2}}}


def test_schedule_requires_complete_tariffs_and_binds_requested_model(tmp_path):
    prices = tmp_path / 'prices.json'
    value = bound_pricing()
    value['usd_per_million'].pop('off_peak')
    prices.write_text(json.dumps(value))
    response = cli('schedule', '--definition', definition(tmp_path), '--output-dir', tmp_path / 'frozen',
                   '--experiment-id', 'offline', '--model', 'fake-model', '--pricing', prices)
    assert response.returncode != 0


@pytest.mark.parametrize('missing_model', [True, False])
def test_cost_requires_response_model_and_consistent_per_call_cache_counts(tmp_path, monkeypatch, missing_model):
    runner, _, _, _, _, _ = http_experiment(tmp_path, monkeypatch)
    events = audited_usage()
    events[1]['requested_model'] = 'fake-model'
    events[2].update(requested_model='fake-model', provider_response_model=None if missing_model else 'provider-v1')
    if not missing_model:
        events[2]['usage'].update(prompt_tokens=1000000, prompt_cache_hit_tokens=200000, prompt_cache_miss_tokens=300000)
    usage = runner.usage_for('task', events, 1)
    cost = runner.cost_for(usage, bound_pricing())
    assert cost['cost_estimate_range_usd'] is None
    assert usage['fields']['prompt_tokens']['total'] == (7 if missing_model else 1000000)


def test_public_schedule_freezes_runtime_scripts_inventory(tmp_path):
    folder = tmp_path / 'frozen'
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', folder,
               '--experiment-id', 'offline', '--model', 'fake-model').returncode == 0
    manifest = json.loads((folder / 'manifest.json').read_text())
    for name in ('validate_spatial.py', 'inspect_processing.py', 'inspect_vector.py', 'build_spatial_oracle.py'):
        assert 'scripts/' + name in manifest['source_sha256']


def retain_http_review_records(monkeypatch):
    """Replace only the database seam; records come from production HTTP writes."""
    import copy
    import ui.backend.main as backend
    records = {}

    def save(**values):
        values = copy.deepcopy(values)
        attempts = values.pop('attempts')
        records[values['task_id']] = {'execution': {**values, 'confirmed': None, 'summary': None},
                                      'attempts': attempts, 'artifacts': []}

    monkeypatch.setattr(backend, 'save_task_execution_record', save)
    monkeypatch.setattr(backend, 'get_task_execution_record', lambda task: records.get(task))


@pytest.mark.parametrize('missing_record', [False, True])
def test_refusal_disposition_uses_real_production_graph_state_without_resuming(tmp_path, monkeypatch, missing_record):
    import copy
    from types import SimpleNamespace
    import httpx
    import test_t07_planning_failures as planning
    import ui.backend.main as backend

    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)
    production_root = tmp_path / 'production'
    production_root.mkdir()
    workflow = planning.workflow.__wrapped__(monkeypatch, production_root, SimpleNamespace(param='false'))
    if not missing_record:
        retain_http_review_records(monkeypatch)
    source = production_root / 'source.geojson'
    record = {'file_id': 'upload-id', 'filename': source.name, 'storage_path': source.name,
              'sha256': runner.digest(source),
              'layers': [{'layer_name': 'points', 'crs': 'EPSG:32650', 'geometry': 'Point', 'is_2d': True}]}
    monkeypatch.setattr(backend, 'get_uploaded_file_record', lambda _: record)
    proposal = planning._proposal()
    proposal['required_inputs'][0]['file_id'] = 'upload-id'
    workflow.model.outcomes = [copy.deepcopy(proposal)]
    seen = []

    def handler(request):
        path = request.url.path
        if path in {'/api/chat/stream', '/api/review'} or path.startswith(('/api/state/', '/api/executions/')):
            seen.append((request.method, path))
            response = workflow.client.request(request.method, path, content=request.content,
                                               headers={'content-type': 'application/json'})
            return httpx.Response(response.status_code, content=response.content,
                                  headers={'content-type': response.headers.get('content-type', 'application/json')})
        return original._transport.handle_request(request)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == (1 if missing_record else 0)
    row = runner.journal(folder / 'results.jsonl')[-1]
    real_state = workflow.client.get('/api/state/actual-thread').json()
    assert real_state['next'] == ['planner_node'] and real_state['continuation']['kind'] == 'resume'
    assert real_state['can_start_task'] is False and real_state['state']['task_plan']['review_status'] == 'rejected'
    if missing_record:
        assert row['status'] == 'environment_interrupted' and row['event'] == 'checkpoint'
        assert 'read_only_rejection_disposition' not in row
        assert workflow.model.calls == 1 and not any(path.startswith('/api/resume') for _, path in seen)
        return
    assert row['status'] == 'invalid_plan' and row['final_complete'] is False
    assert row['workflow_state'] == 'paused_replanning_not_resumed'
    assert row['read_only_rejection_disposition']['facts']['task_id'] == real_state['state']['task_id']
    assert runner.main(arguments + ['--resume'], client=client) == 0
    assert workflow.model.calls == 1 and not any(path.startswith('/api/resume') for _, path in seen)


def isolated_runtime(runner, manifest, sandbox):
    import shutil
    paths = set(manifest['source_sha256']) | {f'agent/skills/{name}' for name in manifest['templates']}
    paths |= {'uv.lock', 'scripts/build_spatial_oracle.py', 'scripts/build_t07_fixtures.py',
              'tests/fixtures/gis/t07_benchmark_oracle.json', 'scripts/validate_spatial.py',
              'scripts/inspect_processing.py', 'scripts/inspect_vector.py'}
    for name in paths:
        source = ROOT / name
        if source.is_file():
            destination = sandbox / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


@pytest.mark.parametrize('stage', ['arm_switch', 'approval', 'oracle', 'after_confirmation'])
def test_frozen_runtime_drift_during_batch_stops_before_more_work(tmp_path, monkeypatch, stage):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch)
    sandbox = tmp_path / 'runtime-copy'
    isolated_runtime(runner, runner.read_json(folder / 'manifest.json'), sandbox)
    monkeypatch.setattr(runner, 'ROOT', sandbox)
    target = sandbox / 'scripts/validate_spatial.py'
    changed = False

    def change():
        nonlocal changed
        if not changed:
            changed = True
            target.write_bytes(target.read_bytes() + b'\n# isolated in-run drift\n')

    class OwnedBackend:
        def __init__(self, *_):
            self.arm = None
            self.restarts = 0
            self.process = None

        def switch(self, arm):
            if self.arm != arm:
                self.restarts += 1
                if arm == 'A1' and stage == 'arm_switch':
                    change()
            self.arm = arm
            state['plan']['metadata']['skill_candidates']['enabled'] = arm == 'A1'
            state['confirmed'] = None

        def stop(self):
            self.arm = None

    def handler(request):
        response = original._transport.handle_request(request)
        if request.url.path == '/api/review' and stage == 'approval':
            change()
        if request.url.path == '/api/confirm-result' and stage == 'after_confirmation':
            change()
        return response

    if stage == 'oracle':
        def oracle(*_):
            change()
            return {'status': 'passed', 'empty': True, 'checks': [], 'errors': []}
        monkeypatch.setattr(runner, 'verify_result', oracle)
    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    args = arguments[:-4] + ['--manage-backend', '--limit', '2']
    assert runner.main(args, client=client, backend_factory=OwnedBackend) == 1
    records = runner.journal(folder / 'results.jsonl')
    if stage == 'arm_switch':
        assert len([row for row in records if row['event'] == 'started']) == 1
        assert records[-1]['status'] == 'complete_empty'
    else:
        assert records[-1]['status'] == 'environment_interrupted'
        assert records[-1]['comparison_status'] == 'incomparable'
        assert records[-1]['final_complete'] is None
        if stage == 'approval':
            assert not any(path.startswith('/api/resume') for _, path, _ in state['requests'])
        if stage in {'approval', 'oracle'}:
            assert state['confirmed'] is None
        if stage == 'after_confirmation':
            assert state['confirmed'] is True and records[-1]['result_confirmed'] is True


@pytest.mark.parametrize('rejected', [False, True])
def test_historical_read_only_plans_use_real_graph_proof_and_are_not_modified(tmp_path, monkeypatch, rejected):
    from types import SimpleNamespace
    import httpx
    import test_t07_planning_failures as planning
    runner, _, _, _, _, _ = http_experiment(tmp_path, monkeypatch)
    production = tmp_path / 'historical-production'
    production.mkdir()
    workflow = planning.workflow.__wrapped__(monkeypatch, production, SimpleNamespace(param='false'))
    retain_http_review_records(monkeypatch)
    _, snapshot = planning._submit(workflow, [planning._proposal()], thread='historical')
    plan = snapshot['state']['task_plan']
    if rejected:
        assert workflow.client.post('/api/review', json={'thread_id': 'historical', 'task_id': plan['task_id'],
            'revision': plan['revision'], 'approved': False, 'advise': 'fixed read-only refusal'}).status_code == 200
        snapshot = workflow.client.get('/api/state/historical').json()
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == '/api/sessions':
            return httpx.Response(200, json=[{'thread_id': 'historical'}])
        if request.url.path == '/api/state/historical':
            return httpx.Response(200, json=snapshot)
        if request.url.path.startswith('/api/executions/'):
            result = workflow.client.get(request.url.path)
            return httpx.Response(result.status_code, json=result.json())
        raise AssertionError(request.url.path)

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    runner.assert_idle(client)
    assert all(method == 'GET' for method, _ in seen)
    assert workflow.model.calls == 1 and workflow.client.get('/api/state/historical').json() == snapshot
    if rejected:
        missing_client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(
            lambda request: httpx.Response(404, json={}) if request.url.path.startswith('/api/executions/')
            else handler(request)))
        with pytest.raises(ValueError):
            runner.assert_idle(missing_client)
    # A single known attempt, or a revision/identity inconsistency, voids the exception.
    unsafe = {'status': 'rejected', 'task_id': plan['task_id'], 'plan_revision': plan['revision'],
              'plan': snapshot['state']['task_plan'], 'attempts': [{'status': 'blocked'}], 'artifacts': [],
              'inflight_call': None, 'confirmed': None}
    assert runner.read_only_plan_facts(snapshot, unsafe) is None


@pytest.mark.parametrize('status', ['success', 'failed', 'reconciled', 'approved', 'needs_attention'])
def test_own_rejection_cannot_use_other_business_status_as_zero_execution(tmp_path, monkeypatch, status):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)

    def handler(request):
        response = original._transport.handle_request(request)
        if request.url.path == '/api/executions/actual-task' and state['stage'] == 'rejected':
            record = response.json()
            record['execution']['status'] = status
            return httpx.Response(200, json=record)
        return response

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 1
    row = runner.journal(folder / 'results.jsonl')[-1]
    assert row['status'] == 'environment_interrupted' and row['final_complete'] is None
    assert 'read_only_rejection_disposition' not in row
    assert not any(path.startswith('/api/resume') for _, path, _ in state['requests'])


@pytest.mark.parametrize('record_fault', ['missing', 'null', 'missing_inflight', 'wrong_revision'])
def test_public_rejection_missing_business_record_stops_and_preserves_http_evidence(tmp_path, monkeypatch, record_fault):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)

    def handler(request):
        response = original._transport.handle_request(request)
        if request.url.path == '/api/executions/actual-task' and state['stage'] == 'rejected':
            if record_fault == 'missing':
                return httpx.Response(404, json={'detail': 'rejected record missing'})
            body = response.json()
            if record_fault == 'null':
                body['execution'] = None
            elif record_fault == 'missing_inflight':
                del body['execution']['inflight_call']
            elif record_fault == 'wrong_revision':
                body['execution']['plan_revision'] = 4
            return httpx.Response(200, json=body)
        return response

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments, client=client) == 1
    row = runner.journal(folder / 'results.jsonl')[-1]
    assert row['event'] == 'checkpoint' and row['status'] == 'environment_interrupted'
    assert row['final_complete'] is None and 'read_only_rejection_disposition' not in row
    evidence = Path(row['evidence_dir']) / 'rejected-execution-http.json'
    captured = json.loads(evidence.read_text())
    assert captured['http_status'] == (404 if record_fault == 'missing' else 200)
    if record_fault == 'missing':
        assert captured['body'] == {'detail': 'rejected record missing'}
        assert not (Path(row['evidence_dir']) / 'rejected-execution.json').exists()
    assert json.loads((Path(row['evidence_dir']) / 'rejected-state.json').read_text())['state']['task_plan']['review_status'] == 'rejected'
    assert runner.main(arguments + ['--resume'], client=client) == 1
    assert len([entry for entry in runner.journal(folder / 'results.jsonl') if entry['event'] == 'started']) == 1
    assert not any(path.startswith('/api/resume') for _, path, _ in state['requests'])


def test_rejection_exception_rechecks_identity_and_saved_evidence_hash(tmp_path, monkeypatch):
    import httpx
    runner, original, arguments, folder, state, _ = http_experiment(tmp_path, monkeypatch, wrong_name=True)
    assert runner.main(arguments, client=original) == 0
    row = runner.journal(folder / 'results.jsonl')[-1]
    assert row['workflow_state'] == 'paused_replanning_not_resumed'

    def handler(request):
        response = original._transport.handle_request(request)
        if request.url.path == '/api/state/actual-thread':
            snapshot = response.json()
            snapshot['state']['task_plan']['revision'] = 4
            snapshot['continuation']['revision'] = 4
            return httpx.Response(200, json=snapshot)
        return response

    client = httpx.Client(base_url='http://127.0.0.1:9000', transport=httpx.MockTransport(handler))
    assert runner.main(arguments + ['--resume'], client=client) == 1
    proof = Path(row['read_only_rejection_disposition']['evidence_refs'][0]['path'])
    proof.write_text('{}')
    assert runner.main(arguments + ['--resume'], client=original) == 1
    assert len([entry for entry in runner.journal(folder / 'results.jsonl') if entry['event'] == 'started']) == 1


@pytest.mark.parametrize('fault', ['missing_peak', 'missing_model', 'wrong_request', 'empty_allowlist', 'bad_rate'])
def test_schedule_refuses_incomplete_or_unbound_pricing(tmp_path, fault):
    value = bound_pricing()
    if fault == 'missing_peak':
        value['usd_per_million'].pop('peak')
    elif fault == 'missing_model':
        value.pop('requested_model')
    elif fault == 'wrong_request':
        value['requested_model'] = 'other-model'
    elif fault == 'empty_allowlist':
        value['provider_response_models'] = []
    else:
        value['usd_per_million']['peak']['cache_miss'] = True
    price = tmp_path / 'pricing.json'
    price.write_text(json.dumps(value))
    assert cli('schedule', '--definition', definition(tmp_path), '--output-dir', tmp_path / 'frozen',
               '--experiment-id', 'offline', '--model', 'fake-model', '--pricing', price).returncode != 0


def test_cost_model_binding_is_independent_of_raw_token_completeness(tmp_path, monkeypatch):
    runner, _, _, _, _, _ = http_experiment(tmp_path, monkeypatch)
    events = audited_usage()
    events[1]['requested_model'] = 'fake-model'
    events[2].update(requested_model='fake-model', provider_response_model='provider-v1')
    complete = runner.usage_for('task', events, 1)
    assert complete['journal_integrity_complete'] is True
    assert complete['fields']['total_tokens']['field_complete'] is False
    assert complete['fields']['prompt_tokens']['field_complete'] is True
    assert complete['per_model_runs'][0]['provider_response_model'] == 'provider-v1'
    assert runner.cost_for(complete, bound_pricing())['cost_estimate_range_usd'] is not None
    events[2]['provider_response_model'] = 'unquoted-model'
    mismatch = runner.usage_for('task', events, 1)
    assert mismatch['fields']['prompt_tokens']['total'] == 7
    assert runner.cost_for(mismatch, bound_pricing())['cost_estimate_range_usd'] is None
    events[2].update(provider_response_model='provider-v1', requested_model='conflicting-request')
    assert runner.cost_for(runner.usage_for('task', events, 1), bound_pricing())['cost_estimate_range_usd'] is None


def test_owned_backend_probe_is_bounded_and_other_health_cannot_hide_child_failure(tmp_path, monkeypatch):
    runner, _, _, _, _, _ = http_experiment(tmp_path, monkeypatch)
    seen = []

    class Probe:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def settimeout(self, timeout):
            seen.append(('timeout', timeout))
        def connect_ex(self, address):
            assert seen == [('timeout', 2)]
            seen.append(('connect', address))
            return 11

    class Process:
        def __init__(self):
            self.calls = 0
        def poll(self):
            self.calls += 1
            return None if self.calls == 1 else 1

    class Health:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def get(self, _):
            from types import SimpleNamespace
            return SimpleNamespace(is_success=True)

    monkeypatch.setattr(runner.socket, 'socket', lambda: Probe())
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *_args, **_kwargs: Process())
    monkeypatch.setattr(runner.httpx, 'Client', lambda **_: Health())
    manager = runner.Backend('http://127.0.0.1:9000', tmp_path)
    with pytest.raises(RuntimeError):
        manager.switch('A0')
    assert manager.arm is None
    manager.stop()


def test_occupied_port_is_never_taken_over_or_stopped(tmp_path, monkeypatch):
    runner, _, _, _, _, _ = http_experiment(tmp_path, monkeypatch)
    calls = []

    class Occupied:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def settimeout(self, value):
            calls.append(('timeout', value))
        def connect_ex(self, address):
            calls.append(('connect', address))
            return 0

    monkeypatch.setattr(runner.socket, 'socket', lambda: Occupied())
    def forbidden_start(*_args, **_kwargs):
        pytest.fail('An occupied port must never start or take over another backend')
    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden_start)
    manager = runner.Backend('http://127.0.0.1:9000', tmp_path)
    with pytest.raises(ValueError, match='already occupied'):
        manager.switch('A0')
    assert calls == [('timeout', 2), ('connect', ('127.0.0.1', 9000))]
    assert manager.process is None and manager.arm is None and manager.restarts == 0
