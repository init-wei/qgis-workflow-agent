"""Public export CLI and locked official SDK, without GIS or network."""

import hashlib
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/export_t07_langsmith.py'
# langsmith 0.6.4 env/_runtime_env.py:get_release_shas reads these host fields.
# They are intentionally outside the exporter's production runtime allowlist.
SDK_RELEASE_ENV_VARS = (
    'VERCEL_GIT_COMMIT_SHA', 'NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA', 'COMMIT_REF',
    'RENDER_GIT_COMMIT', 'CI_COMMIT_SHA', 'CIRCLE_SHA1', 'CF_PAGES_COMMIT_SHA',
    'REACT_APP_GIT_SHA', 'SOURCE_VERSION', 'GITHUB_SHA', 'TRAVIS_COMMIT',
    'GIT_COMMIT', 'BUILD_VCS_NUMBER', 'bamboo_planRepository_revision',
    'Build.SourceVersion', 'BITBUCKET_COMMIT', 'DRONE_COMMIT_SHA',
    'SEMAPHORE_GIT_SHA', 'BUILDKITE_COMMIT',
)


@pytest.fixture
def isolated_sdk_runtime(monkeypatch):
    """Keep SDK serialization fixtures independent of CI/deployment identity."""
    from langsmith.env._runtime_env import get_release_shas, get_runtime_environment

    with monkeypatch.context() as release_env:
        for name in SDK_RELEASE_ENV_VARS:
            release_env.delenv(name, raising=False)
        get_release_shas.cache_clear()
        get_runtime_environment.cache_clear()
        try:
            yield release_env
        finally:
            # Do not leave a cached sanitized/fake runtime after restoring hosts.
            get_release_shas.cache_clear()
            get_runtime_environment.cache_clear()


def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=ROOT,
                          env={**os.environ, 'PYTHON_DOTENV_DISABLED': '1', 'DATABASE_URL': ''},
                          text=True, capture_output=True)


def frozen_results(folder):
    requests = [{'request_id': f'{family}-{scenario}-{phrase}', 'scenario_id': f'{family}-{scenario}',
                 'family': family, 'message': 'PRIVATE-PROMPT', 'split': 'heldout'}
                for family in ('load', 'reproject', 'buffer', 'clip', 'dissolve', 'statistics')
                for scenario in (1, 2) for phrase in (1, 2)]
    slots = [{**{key: request[key] for key in ('request_id', 'scenario_id', 'family')},
              'slot_id': f"{request['request_id']}:{arm}:r{repeat}", 'arm': arm, 'repeat': repeat,
              'group': 'heldout'} for request in requests
             for arm, repeat in [('A0', 1), ('A1', 1), ('A1', 2), ('A0', 2)]]
    manifest = {'schema_version': 1, 'experiment_id': 'offline', 'phase': 'heldout',
                'requests': requests, 'schedule': slots}
    rows = [{**slot, 'experiment_id': 'offline', 'attempt_id': f'attempt-{i}', 'attempt_index': 1,
             'event': 'finished', 'status': 'complete', 'task_id': f'task-{i}', 'thread_id': f'session-{i}',
             'revision': 1, 'first_plan_valid': True, 'first_complete': True, 'final_complete': True,
             'planning_ms': 150.25, 'execution_ms': 250.5, 'wall_ms': 500.,
             'operator_kind': 'automated_developer_operator', 'human_change': True,
             'prompt': 'PRIVATE-PROMPT', 'geometry': 'PRIVATE-GEOMETRY', 'evidence_dir': '/PRIVATE-PATH',
             'error': 'PRIVATE-SSE'} for i, slot in enumerate(slots)]
    write(folder / 'manifest.json', manifest)
    (folder / 'runtime.jsonl').write_text(json.dumps({'event': 'runner_end'}) + '\n')
    save_results(folder, rows)
    return rows


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True))


def save_results(folder, rows):
    (folder / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    primary = {row['slot_id']: row for row in rows if row.get('attempt_index', 1) == 1}
    from collections import Counter
    arms = {}
    for arm in ('A0', 'A1'):
        selected = [row for row in primary.values() if row['arm'] == arm]
        arms[arm] = {'planned': 48, 'attempted': len(selected),
                     'statuses': dict(Counter(row['status'] for row in selected))}
        for field in ('first_plan_valid', 'first_complete', 'final_complete'):
            arms[arm][field] = {'n': sum(row.get(field) is True for row in selected), 'N': 48,
                                'unknown': 48 - sum(isinstance(row.get(field), bool) for row in selected)}
    write(folder / 'summary.json', {'schema_version': 1, 'experiment_id': 'offline', 'planned': 96,
                                    'attempts': len({row['attempt_id'] for row in rows}),
                                    'statuses': dict(Counter(row['status'] for row in primary.values())),
                                    'comparison_integrity': {'status': 'no_observed_runtime_drift',
                                                             'runtime_drift_count': 0},
                                    'arms': arms})


def exporter():
    spec = importlib.util.spec_from_file_location('t07_langsmith_export', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_dry_run_has_48_paired_examples_and_no_private_text(tmp_path):
    frozen_results(tmp_path)
    output = tmp_path / 'safe.json'
    response = cli('--manifest', tmp_path / 'manifest.json', '--output', output)
    assert response.returncode == 0, response.stderr
    artifact = json.loads(output.read_text())
    assert artifact['ready'] is True
    assert len(artifact['examples']) == 48
    assert {arm: len(rows) for arm, rows in artifact['arms'].items()} == {'A0': 48, 'A1': 48}
    assert artifact['arms']['A0'][0]['metrics']['human_change'] is None
    assert artifact['arms']['A0'][0]['metrics']['execution_ms'] == 250.5
    assert artifact['binding']['manifest_sha256'] == hashlib.sha256((tmp_path / 'manifest.json').read_bytes()).hexdigest()
    for private in ('PRIVATE-PROMPT', 'PRIVATE-GEOMETRY', 'PRIVATE-PATH', 'PRIVATE-SSE'):
        assert private not in output.read_text() + response.stdout + response.stderr
    assert not (tmp_path / 'safe.receipt.json').exists()


def test_unknown_stays_unknown_and_failure_stays_in_primary_denominator(tmp_path):
    rows = frozen_results(tmp_path)
    rows[0].update(status='environment_interrupted', event='checkpoint', first_plan_valid=None,
                   first_complete=None, final_complete=None)
    rows[1].update(status='planning_failed', first_plan_valid=False, first_complete=False, final_complete=False)
    save_results(tmp_path, rows)
    response = cli('--manifest', tmp_path / 'manifest.json', '--output', tmp_path / 'safe.json')
    assert response.returncode == 0, response.stderr
    artifact = json.loads((tmp_path / 'safe.json').read_text())
    assert artifact['ready'] is True
    assert artifact['arms']['A0'][0]['status'] == 'environment_interrupted'
    assert artifact['arms']['A0'][0]['metrics']['final_complete'] is None
    assert artifact['arms']['A1'][0]['metrics']['final_complete'] is False


@pytest.mark.parametrize('corruption', ['duplicate_slot', 'foreign_task', 'summary', 'unsafe_identity'])
def test_cli_rejects_unbound_or_unsafe_evidence(tmp_path, corruption):
    rows = frozen_results(tmp_path)
    if corruption == 'duplicate_slot':
        manifest = json.loads((tmp_path / 'manifest.json').read_text())
        manifest['schedule'][1] = manifest['schedule'][0]
        write(tmp_path / 'manifest.json', manifest)
    elif corruption == 'foreign_task':
        rows[0]['request_id'] = 'foreign'
        save_results(tmp_path, rows)
    elif corruption == 'summary':
        summary = json.loads((tmp_path / 'summary.json').read_text())
        summary['arms']['A0']['final_complete']['n'] -= 1
        write(tmp_path / 'summary.json', summary)
    else:
        rows[0]['task_id'] = 'PRIVATE-PROMPT\n/geometry'
        save_results(tmp_path, rows)
    response = cli('--manifest', tmp_path / 'manifest.json', '--output', tmp_path / 'safe.json')
    assert response.returncode != 0
    assert not (tmp_path / 'safe.json').exists()
    assert 'PRIVATE-' not in response.stderr


def test_cli_missing_task_blocks_execute_and_modified_sources_cannot_reuse_artifact(tmp_path):
    rows = frozen_results(tmp_path)
    rows[0].pop('task_id')
    save_results(tmp_path, rows)
    output = tmp_path / 'safe.json'
    assert cli('--manifest', tmp_path / 'manifest.json', '--output', output).returncode == 0
    assert json.loads(output.read_text())['ready'] is False
    response = cli('--manifest', tmp_path / 'manifest.json', '--output', output, '--execute')
    assert response.returncode != 0
    rows[0]['task_id'] = 'repaired'
    save_results(tmp_path, rows)
    assert cli('--manifest', tmp_path / 'manifest.json', '--output', output).returncode != 0


def test_primary_does_not_turn_into_successful_rerun(tmp_path):
    rows = frozen_results(tmp_path)
    rows[0].update(status='execution_failed', final_complete=False, first_complete=False)
    rows.append({**rows[0], 'attempt_id': 'rerun', 'attempt_index': 2, 'rerun_of': rows[0]['attempt_id'],
                 'task_id': 'rerun-task', 'status': 'complete', 'final_complete': True})
    save_results(tmp_path, rows)
    output = tmp_path / 'safe.json'
    response = cli('--manifest', tmp_path / 'manifest.json', '--output', output)
    assert response.returncode == 0, response.stderr
    artifact = json.loads(output.read_text())
    assert artifact['arms']['A0'][0]['task_id'] == 'task-0'
    assert artifact['arms']['A0'][0]['metrics']['final_complete'] is False
    assert artifact['counts']['reruns'] == 1


def test_active_checkpoint_is_not_certified_as_a_finished_unknown(tmp_path):
    rows = frozen_results(tmp_path)
    rows[0].update(event='checkpoint', status='environment_interrupted', final_complete=None)
    save_results(tmp_path, rows)
    rows[0].pop('status')  # Mid-execution checkpoint, not the runner's explicit interruption fact.
    (tmp_path / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    output = tmp_path / 'safe.json'
    response = cli('--manifest', tmp_path / 'manifest.json', '--output', output)
    assert response.returncode == 0, response.stderr
    artifact = json.loads(output.read_text())
    assert artifact['ready'] is False
    assert artifact['blockers']['unresolved_in_progress'] == 1


@pytest.mark.parametrize('runtime_state', ['drift', 'missing', 'empty', 'invalid', 'summary_missing',
                                          'summary_mismatch', 'declared_incomparable', 'invalid_shape',
                                          'unknown_event', 'invalid_drift_record'])
def test_runtime_evidence_limits_comparison_and_blocks_execute(tmp_path, monkeypatch, runtime_state):
    frozen_results(tmp_path)
    runtime_path = tmp_path / 'runtime.jsonl'
    runtime_path.write_text(json.dumps({'event': 'runner_end'}) + '\n')
    summary_path = tmp_path / 'summary.json'
    summary = json.loads(summary_path.read_text())
    summary['comparison_integrity'] = {'status': 'no_observed_runtime_drift', 'runtime_drift_count': 0}
    if runtime_state == 'drift':
        runtime_path.write_text(json.dumps({'event': 'frozen_runtime_drift', 'comparison_status': 'incomparable',
                                           'slot_id': 'PRIVATE-SLOT', 'diagnostic': 'PRIVATE-PROMPT'}) + '\n')
        summary['comparison_integrity'] = {'status': 'incomparable', 'runtime_drift_count': 1}
    elif runtime_state == 'missing':
        runtime_path.unlink()
    elif runtime_state == 'empty':
        runtime_path.write_text('')
    elif runtime_state == 'invalid':
        runtime_path.write_text('PRIVATE-INVALID-JSON')
    elif runtime_state == 'summary_missing':
        summary.pop('comparison_integrity')
    elif runtime_state == 'summary_mismatch':
        summary['comparison_integrity']['runtime_drift_count'] = 2
    elif runtime_state == 'declared_incomparable':
        summary['comparison_integrity'] = {'status': 'incomparable', 'runtime_drift_count': 0}
    elif runtime_state == 'invalid_shape':
        runtime_path.write_text('[]\n')
    elif runtime_state == 'unknown_event':
        runtime_path.write_text(json.dumps({'event': 'unreviewed_event'}) + '\n')
    elif runtime_state == 'invalid_drift_record':
        runtime_path.write_text(json.dumps({'event': 'frozen_runtime_drift'}) + '\n')
    write(summary_path, summary)
    module = exporter()
    artifact = module.build_artifact(tmp_path / 'manifest.json', summary_path)
    assert artifact['ready'] is False
    assert artifact['comparison_integrity']['status'] in {'unknown', 'incomparable'}
    assert artifact['blockers']
    assert artifact['counts']['primary'] == 96
    assert 'PRIVATE-' not in json.dumps(artifact)
    assert artifact['binding']['runtime_sha256'] == (hashlib.sha256(runtime_path.read_bytes()).hexdigest()
                                                    if runtime_path.exists() else None)
    import agent.observability as telemetry
    monkeypatch.setattr(telemetry, 'configure_langsmith', lambda: pytest.fail('Blocked comparison must stay offline'))
    with pytest.raises(ValueError, match='STOP'):
        module.execute(artifact, tmp_path / 'safe.json')
    assert not (tmp_path / 'safe.receipt.json').exists()
    artifact['ready'] = True
    with pytest.raises(ValueError, match='STOP'):
        module.execute(artifact, tmp_path / 'safe.json')


def test_real_aggregate_drift_cannot_be_exported_as_comparable(tmp_path):
    frozen_results(tmp_path)
    manifest_path = tmp_path / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['review_policy'] = {'max_operator_revisions': 0}
    write(manifest_path, manifest)
    runtime_path = tmp_path / 'runtime.jsonl'
    runtime_path.write_text(json.dumps({'event': 'frozen_runtime_drift', 'comparison_status': 'incomparable',
                                       'slot_id': manifest['schedule'][-1]['slot_id']}) + '\n')
    spec = importlib.util.spec_from_file_location('t07_actual_aggregate', ROOT / 'scripts/evaluate_t07.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    summary = runner.aggregate(argparse.Namespace(manifest=manifest_path, results=None, usage=None,
                                                 output_dir=None, special_results=None))
    assert summary['comparison_integrity'] == {'status': 'incomparable', 'runtime_drift_count': 1}
    output = tmp_path / 'safe.json'
    response = cli('--manifest', manifest_path, '--output', output)
    assert response.returncode == 0, response.stderr
    artifact = json.loads(output.read_text())
    assert artifact['ready'] is False
    assert artifact['comparison_integrity']['status'] == 'incomparable'
    assert artifact['comparison_integrity']['runtime_drift_count'] == 1
    assert artifact['binding']['runtime_sha256'] == hashlib.sha256(runtime_path.read_bytes()).hexdigest()
    assert artifact['blockers']['runtime_drift'] == 1
    assert cli('--manifest', manifest_path, '--output', output, '--execute').returncode != 0


def test_runtime_source_change_cannot_reuse_reviewed_export(tmp_path):
    frozen_results(tmp_path)
    runtime = tmp_path / 'runtime.jsonl'
    runtime.write_text(json.dumps({'event': 'runner_end'}) + '\n')
    summary = json.loads((tmp_path / 'summary.json').read_text())
    summary['comparison_integrity'] = {'status': 'no_observed_runtime_drift', 'runtime_drift_count': 0}
    write(tmp_path / 'summary.json', summary)
    output = tmp_path / 'safe.json'
    assert cli('--manifest', tmp_path / 'manifest.json', '--output', output).returncode == 0
    initial = json.loads(output.read_text())
    assert initial['ready'] is True
    assert initial['comparison_integrity']['status'] == 'no_observed_runtime_drift'
    runtime.write_text(json.dumps({'event': 'runner_end', 'time': '2026-10-31T01:00:00Z'}) + '\n')
    assert cli('--manifest', tmp_path / 'manifest.json', '--output', output).returncode != 0


@pytest.mark.parametrize('failure_at', [None, 'example_write', 'feedback_readback'])
def test_locked_sdk_evaluate_serializes_only_safe_facts_and_business_ids(
        tmp_path, monkeypatch, failure_at, isolated_sdk_runtime):
    """Exercise actual SDK Dataset/Experiment/Run/Feedback serialization at HTTP."""
    import asyncio
    from urllib.parse import urlsplit
    import uuid
    import requests
    import langsmith
    import langsmith.utils
    import langsmith.env
    import socket
    import agent.observability as telemetry

    assert langsmith.__version__ == '0.6.4'
    for key in tuple(os.environ):
        if key.startswith(('LANGSMITH_', 'LANGCHAIN_')) or key == 'OTEL_ENABLED':
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('QGIS_AGENT_LANGSMITH_ENABLED', 'true')
    monkeypatch.setenv('LANGSMITH_API_KEY', 'fake-export-key')
    monkeypatch.setattr(telemetry, '_legacy_conflict', False)
    monkeypatch.setattr(langsmith.env, 'get_git_info', lambda: {
        'author_name': 'PRIVATE-GIT-AUTHOR', 'author_email': 'PRIVATE-EMAIL@example.invalid',
        'remote_url': 'https://PRIVATE-REMOTE.invalid/private.git'})
    langsmith.utils.get_env_var.cache_clear()
    rows = frozen_results(tmp_path)
    rows[0].update(status='environment_interrupted', event='checkpoint', final_complete=None)
    rows[1].update(status='planning_failed', first_plan_valid=False, first_complete=False, final_complete=False)
    rows[1].pop('revision')  # A failed planner has a task, but no approved revision.
    save_results(tmp_path, rows)
    module = exporter()
    artifact = module.build_artifact(tmp_path / 'manifest.json', tmp_path / 'summary.json')
    module.sdk_runtime()  # SDK's cached platform discovery may invoke uname.
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    sent, datasets, projects, examples, runs, feedbacks = [], {}, {}, {}, {}, []
    tenant = str(uuid.uuid4())

    def transport(session, method, url, **kwargs):
        method, path = method.upper(), urlsplit(url).path
        data = kwargs.get('data') or b'{}'
        if isinstance(data, str):
            data = data.encode()
        if path == '/runs/multipart':
            from requests_toolbelt.multipart.decoder import MultipartDecoder
            import re
            payload = {}
            for part in MultipartDecoder(data, kwargs['headers']['Content-Type']).parts:
                name = re.search(rb'name="([^"]+)"', part.headers[b'Content-Disposition']).group(1).decode()
                payload[name] = json.loads(part.content)
        else:
            payload = json.loads(data)
        sent.append((method, path, payload))
        if path == '/datasets' and method == 'GET':
            result = []
        elif path == '/sessions' and method == 'GET':
            result = []
        elif path == '/datasets' and method == 'POST':
            result = {**payload, 'id': str(uuid.uuid4()), 'created_at': '2026-10-31T00:00:00Z'}
            datasets[result['id']] = result
        elif path == '/examples/bulk':
            if failure_at == 'example_write':
                raise requests.ConnectionError('PRIVATE-UNTRUSTED-ERROR')
            for example in payload:
                example['created_at'] = '2026-10-31T00:00:00Z'
                examples[example['id']] = example
            result = payload
        elif path == '/examples' and method == 'GET':
            result = list(examples.values())
        elif path == '/sessions' and method == 'POST':
            result = {**payload, 'start_time': '2026-10-31T00:00:00Z', 'tenant_id': tenant}
            projects[result['id']] = result
        elif path.startswith('/sessions/') and method == 'PATCH':
            result = projects[path.rsplit('/', 1)[1]]
            result.update({key: value for key, value in payload.items() if value is not None})
        elif path == '/runs/batch':
            for item in payload.get('post', []) + payload.get('patch', []):
                runs.setdefault(item['id'], {}).update(item)
            result = {}
        elif path == '/runs/multipart':
            for name, item in payload.items():
                operation, run_id, *field = name.split('.')
                if operation == 'feedback':
                    feedbacks.append(item)
                elif field:
                    runs.setdefault(run_id, {})[field[0]] = item
                else:
                    runs.setdefault(run_id, {}).update(item)
            result = {}
        elif path == '/feedback' and method == 'GET':
            params = kwargs.get('params', {})
            run_ids = {str(value) for value in params['run']}
            matching = [item for item in feedbacks if item['run_id'] in run_ids]
            offset, limit = params.get('offset', 0), params.get('limit', 100)
            result = [{**item, 'created_at': '2026-10-31T00:00:00Z', 'modified_at': '2026-10-31T00:00:00Z'}
                      for item in matching[offset:offset + limit]]
            if failure_at == 'feedback_readback':
                result = []
        elif path == '/feedback':
            feedbacks.append(payload)
            result = payload
        elif path == '/runs/query':
            names = {project['name'] for project_id, project in projects.items() if project_id in payload['session']}
            result = {'runs': [run for run in runs.values() if run.get('session_name') in names],
                      'cursors': {'next': None}}
        else:
            raise AssertionError(f'Unexpected SDK request {method} {path}')
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(result).encode()
        response.url = url
        return response

    monkeypatch.setattr(requests.Session, 'request', transport)
    def forbidden_external(*args, **kwargs):
        raise AssertionError('Exporter must not start a model, GIS process, or network socket')
    monkeypatch.setattr(subprocess, 'Popen', forbidden_external)
    monkeypatch.setattr(socket.socket, 'connect', forbidden_external)
    try:
        if failure_at:
            with pytest.raises(RuntimeError, match='retained receipt'):
                module.execute(artifact, tmp_path / 'safe.json')
            receipt = json.loads((tmp_path / 'safe.receipt.json').read_text())
            assert receipt['state'] == 'needs_inspection'
            assert 'PRIVATE-' not in json.dumps(receipt)
            assert len(projects) == (1 if failure_at == 'feedback_readback' else 0)
            assert receipt['dataset_id'] in datasets
            before = len(sent)
            with pytest.raises(ValueError, match='receipt already exists'):
                module.execute(artifact, tmp_path / 'safe.json')
            assert len(sent) == before
            assert 'update_project' not in telemetry.configure_langsmith().__dict__
            return
        receipt = module.execute(artifact, tmp_path / 'safe.json')
        assert receipt['state'] == 'complete'
        assert {arm: item['imported_runs'] for arm, item in receipt['experiments'].items()} == {'A0': 48, 'A1': 48}
        assert len(examples) == 48
        target_runs = [run for run in runs.values() if run.get('session_name') in
                       {project['name'] for project in projects.values()}]
        assert len(target_runs) == 96
        for run in runs.values():
            assert run.get('inputs', {}) == {}
            assert run.get('outputs', {}) == {}
            assert not run.get('events') and not run.get('serialized') and not run.get('attachments')
            metadata = run.get('extra', {}).get('metadata', {})
            assert set(metadata) <= {'task_id', 'session_id', 'case_id', 'attempt_id', 'revision',
                                     'status', 'sha256', 'ls_run_depth'}
            if 'ls_run_depth' in metadata:
                assert type(metadata['ls_run_depth']) is int and metadata['ls_run_depth'] >= 0
        for run in target_runs:
            metadata = run['extra']['metadata']
            assert metadata['task_id'].startswith('task-')
            assert metadata['session_id'].startswith('session-')
            assert metadata['case_id'] == next(row['slot_id'] for row in rows if row['task_id'] == metadata['task_id'])
            assert metadata.get('revision') == (None if metadata['task_id'] == 'task-1' else 1)
            assert len(metadata['sha256']) == 64
        assert len(feedbacks) == 96 * 9
        assert any(item['key'] == 'final_complete' and item['value'] == 'unknown' and item.get('score') is None
                   for item in feedbacks)
        assert all(item['value'] == 'unknown' and item.get('score') is None
                   for item in feedbacks if item['key'] == 'human_change')
        assert all(item['score'] == 250.5 for item in feedbacks if item['key'] == 'execution_ms')
        body = json.dumps(sent)
        for forbidden in ('PRIVATE-', 'fake-export-key', 'geometry', 'evidence_dir', 'human_change_rate'):
            assert forbidden not in body
        for method, path, payload in sent:
            if method == 'PATCH' and path.startswith('/sessions/'):
                assert payload['extra']['metadata'] == artifact['binding']
            if method == 'POST' and path == '/datasets':
                assert set(payload['extra']['metadata']) == set(artifact['binding']) | {'runtime'}
                assert payload['extra']['metadata']['runtime']['sdk_version'] == '0.6.4'
        assert 'update_project' not in telemetry.configure_langsmith().__dict__
        with pytest.raises(ValueError, match='receipt already exists'):
            module.execute(artifact, tmp_path / 'safe.json')
    finally:
        asyncio.run(telemetry.close_langsmith())
        langsmith.utils.get_env_var.cache_clear()


@pytest.mark.parametrize('release_env', SDK_RELEASE_ENV_VARS)
def test_sdk_runtime_still_rejects_host_release_metadata(release_env, isolated_sdk_runtime):
    """Isolation of test hosts never widens the production privacy contract."""
    from langsmith.env._runtime_env import get_release_shas, get_runtime_environment

    isolated_sdk_runtime.setenv(release_env, 'fake-unreviewed-release-sha')
    get_release_shas.cache_clear()
    get_runtime_environment.cache_clear()
    # Use the actual locked SDK's environment collection, not a fake dictionary.
    assert get_runtime_environment()[release_env] == 'fake-unreviewed-release-sha'
    with pytest.raises(RuntimeError, match='STOP: unreviewed SDK runtime metadata'):
        exporter().sdk_runtime()
