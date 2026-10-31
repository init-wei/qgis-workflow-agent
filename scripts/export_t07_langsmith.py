"""Export already measured T07 primary results; default is an offline dry run.

First run with --manifest and --output to freeze the safe artifact. Repeat the
same command with --execute only after reviewing that artifact. This imports
facts into two official experiments; it never executes a model or GIS task.
An existing receipt stops automatic replay, including after an ambiguous write.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAMILIES = {'load', 'reproject', 'buffer', 'clip', 'dissolve', 'statistics'}
STATUS = {'complete', 'complete_empty', 'planning_failed', 'invalid_plan', 'execution_failed',
          'oracle_failed', 'environment_interrupted', 'unknown_outcome', 'not_run'}
BOOL_METRICS = ('first_plan_valid', 'first_complete', 'final_complete', 'false_success',
                'unapproved_execution', 'human_change')
TIME_METRICS = ('planning_ms', 'execution_ms', 'wall_ms')
IDENTITY = ('slot_id', 'request_id', 'scenario_id', 'family', 'group', 'arm', 'repeat')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(path, value, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path if exclusive else path.with_name(path.name + '.tmp')
    with target.open('x', encoding='utf-8') as stream:
        stream.write(canonical(value).decode() + '\n')
    if not exclusive:
        target.replace(path)


def identifier(value):
    # Reuse the existing telemetry's secret check and identifier restrictions.
    from agent.observability import _safe_metadata
    if _safe_metadata({'task_id': value}).get('task_id') != value or value is None:
        raise ValueError('Unsafe or missing identifier in local evidence')
    return value


def optional_id(value):
    return identifier(value) if value is not None else None


def metrics(row):
    result = {}
    for key in BOOL_METRICS:
        value = row.get(key)
        if value is not None and not isinstance(value, bool):
            raise ValueError('A boolean metric has an invalid type')
        result[key] = value if key != 'human_change' or row.get('operator_kind') == 'human' else None
    for key in TIME_METRICS:
        value = row.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise ValueError('A measured duration is invalid')
        result[key] = value
    return result


def comparison_evidence(runtime_path, summary):
    """Check the runner's small drift journal without exporting its raw fields."""
    blockers = Counter()
    runtime_bytes, rows = None, []
    try:
        runtime_bytes = runtime_path.read_bytes()
        rows = [json.loads(line) for line in runtime_bytes.splitlines() if line.strip()]
        if not rows:
            blockers['runtime_journal_empty'] = 1
        elif any(not isinstance(row, dict) or not isinstance(row.get('event'), str) for row in rows):
            blockers['runtime_journal_invalid'] = 1
            rows = []
    except FileNotFoundError:
        blockers['runtime_journal_missing'] = 1
    except (OSError, ValueError):
        blockers['runtime_journal_invalid'] = 1
    drift_count = sum(row['event'] == 'frozen_runtime_drift' for row in rows)
    observed_incomparable = any(row.get('comparison_status') == 'incomparable'
                               or row['event'] in {'frozen_runtime_drift', 'runtime_drift'} for row in rows)
    if any(row['event'] not in {'runner_end', 'frozen_runtime_drift', 'runtime_drift'}
           or ('comparison_status' in row and row['comparison_status'] != 'incomparable')
           or (row['event'] == 'frozen_runtime_drift' and row.get('comparison_status') != 'incomparable')
           for row in rows):
        blockers['runtime_journal_invalid'] = 1
    declared = summary.get('comparison_integrity')
    declared = declared if isinstance(declared, dict) else {}
    declared_status = declared.get('status')
    if declared_status not in {'incomparable', 'no_observed_runtime_drift'}:
        declared_status = 'unknown'
    declared_count = declared.get('runtime_drift_count')
    if type(declared_count) is not int or declared_count < 0:
        declared_count = None
    valid_source = not blockers
    observed_status = 'incomparable' if observed_incomparable else 'no_observed_runtime_drift'
    matches = (valid_source and declared_status == observed_status and declared_count == drift_count)
    if not matches:
        blockers['runtime_summary_unverified'] = 1
    if observed_incomparable or declared_status == 'incomparable' or (declared_count or 0) > 0:
        status = 'incomparable'
        blockers['runtime_drift'] = max(drift_count, declared_count or 0, 1)
    else:
        status = observed_status if matches else 'unknown'
    integrity = {'status': status, 'runtime_drift_count': drift_count if valid_source else None,
                 'summary_status': declared_status, 'summary_runtime_drift_count': declared_count,
                 'summary_matches_runtime': matches}
    return integrity, sha(runtime_bytes) if runtime_bytes is not None else None, blockers


def build_artifact(manifest_path, summary_path):
    """Validate fixed identities and summary counts without importing the runner."""
    manifest_bytes, summary_bytes = manifest_path.read_bytes(), summary_path.read_bytes()
    manifest, summary = json.loads(manifest_bytes), json.loads(summary_bytes)
    results_bytes = (manifest_path.parent / 'results.jsonl').read_bytes()
    experiment_id = identifier(manifest.get('experiment_id'))
    if (manifest.get('schema_version') != 1 or manifest.get('phase') != 'heldout'
            or summary.get('schema_version') != 1 or summary.get('experiment_id') != experiment_id
            or summary.get('planned') != 96):
        raise ValueError('Expected one completed aggregate for a fixed heldout manifest')
    requests = manifest['requests']
    request_ids = [identifier(row['request_id']) for row in requests]
    if (len(request_ids) != 24 or len(set(request_ids)) != 24
            or Counter(row['family'] for row in requests) != Counter({family: 4 for family in FAMILIES})
            or len({row['scenario_id'] for row in requests}) != 12
            or set(Counter(row['scenario_id'] for row in requests).values()) != {2}):
        raise ValueError('Expected 24 unique requests in twelve paired scenarios and six families')
    expected = []
    for request in requests:
        for arm, repeat in [('A0', 1), ('A1', 1), ('A1', 2), ('A0', 2)]:
            expected.append({'slot_id': f"{request['request_id']}:{arm}:r{repeat}",
                             'request_id': request['request_id'], 'scenario_id': identifier(request['scenario_id']),
                             'family': request['family'], 'group': 'heldout', 'arm': arm, 'repeat': repeat})
    if manifest['schedule'] != expected:
        raise ValueError('The frozen 96-slot ABBA schedule differs from its request identities')
    slots = {slot['slot_id']: slot for slot in expected}
    attempts, primary_ids = {}, {}
    for line in results_bytes.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        slot = slots.get(row.get('slot_id'))
        if (slot is None or row.get('experiment_id') != experiment_id
                or any(row.get(key) != slot[key] for key in IDENTITY)):
            raise ValueError('A raw result is not bound to its frozen slot')
        attempt_id = identifier(row.get('attempt_id'))
        previous = attempts.get(attempt_id)
        if previous and (previous['slot_id'] != row['slot_id'] or previous.get('event') == 'finished'):
            raise ValueError('Duplicate finished fact or reused attempt identity')
        if previous and row.get('event') == 'started':
            raise ValueError('Duplicate started fact')
        if previous and any(previous.get(key) is not None and row.get(key) != previous[key]
                            for key in ('task_id', 'thread_id', 'rerun_of', 'attempt_index')):
            raise ValueError('Business identity changed within a raw attempt')
        if row.get('event') not in {'started', 'checkpoint', 'finished'}:
            raise ValueError('Unknown result event')
        if row.get('rerun_of') is None and row.get('attempt_index', 1) == 1:
            if primary_ids.setdefault(row['slot_id'], attempt_id) != attempt_id:
                raise ValueError('Multiple primary attempts for one slot')
        else:
            parent = attempts.get(row.get('rerun_of'))
            if (row.get('attempt_index', 0) <= 1 or parent is None or parent['slot_id'] != row['slot_id']
                    or parent.get('event') != 'finished' or parent.get('status') not in
                    {'planning_failed', 'invalid_plan', 'execution_failed', 'oracle_failed'}):
                raise ValueError('A rerun lacks its earlier terminal failure')
        attempts[attempt_id] = row
    if summary.get('attempts') != len(attempts):
        raise ValueError('The summary attempt count differs from the local result journal')
    primary = []
    for slot in expected:
        raw = attempts[primary_ids[slot['slot_id']]] if slot['slot_id'] in primary_ids else None
        row = dict(raw) if raw else {**slot, 'status': 'not_run'}
        if raw and raw.get('event') != 'finished':
            row.update(status='environment_interrupted', final_complete=None)
        primary.append(row)
    if summary.get('statuses') != dict(Counter(row['status'] for row in primary)):
        raise ValueError('The aggregate statuses differ from the original primary attempts')
    for arm in ('A0', 'A1'):
        selected = [row for row in primary if row['arm'] == arm]
        arm_summary = summary['arms'][arm]
        if (arm_summary.get('planned') != 48
                or arm_summary.get('attempted') != sum(row['status'] != 'not_run' for row in selected)
                or arm_summary.get('statuses') != dict(Counter(row['status'] for row in selected))):
            raise ValueError('Arm counts differ from the local primary results')
        for field in BOOL_METRICS[:3]:
            fraction = arm_summary[field]
            if (fraction.get('N') != 48 or fraction.get('n') != sum(row.get(field) is True for row in selected)
                    or fraction.get('unknown') != sum(not isinstance(row.get(field), bool) for row in selected)):
                raise ValueError('Aggregate completion scores differ from the primary results')
    comparison, runtime_sha, blockers = comparison_evidence(manifest_path.parent / 'runtime.jsonl', summary)
    arms = {'A0': [], 'A1': []}
    for row in primary:
        if row['status'] not in STATUS:
            raise ValueError('Unknown primary result status')
        revision = row.get('revision')
        if revision is not None and (type(revision) is not int or revision < 0):
            raise ValueError('Invalid plan revision')
        safe = {key: row[key] for key in ('slot_id', 'request_id', 'scenario_id', 'family', 'arm', 'repeat', 'status')}
        safe.update(task_id=optional_id(row.get('task_id')), session_id=optional_id(row.get('thread_id')),
                    case_id=identifier(row['slot_id']), attempt_id=optional_id(row.get('attempt_id')),
                    revision=revision,
                    source_sha256=sha(canonical(attempts[row['attempt_id']])) if row.get('attempt_id') else None,
                    metrics=metrics(row))
        if row['status'] == 'not_run' or row.get('event') == 'started':
            blockers['not_finished_or_not_run'] += 1
        if (row.get('event') == 'checkpoint'
                and attempts[row['attempt_id']].get('status') not in {'environment_interrupted', 'unknown_outcome'}):
            blockers['unresolved_in_progress'] += 1
        if not all(safe[key] for key in ('task_id', 'session_id', 'attempt_id')):
            blockers['missing_business_identity'] += 1
        arms[row['arm']].append(safe)
    task_ids = [row['task_id'] for rows in arms.values() for row in rows if row['task_id']]
    if len(set(task_ids)) != len(task_ids):
        raise ValueError('A business task is reused across primary slots')
    examples = []
    by_arm = {arm: {(row['request_id'], row['repeat']): row for row in rows} for arm, rows in arms.items()}
    for request in requests:
        for repeat in (1, 2):
            inputs = {'request_id': request['request_id'], 'scenario': request['scenario_id'],
                      'family': request['family'], 'repeat': repeat}
            outputs = {}
            for arm in ('A0', 'A1'):
                fact = by_arm[arm][(request['request_id'], repeat)]
                outputs[arm] = {key: fact[key] for key in ('task_id', 'session_id', 'case_id', 'revision',
                                                          'source_sha256', 'status')}
            examples.append({'inputs': inputs, 'outputs': outputs})
    return {'schema_version': 1, 'experiment_id': experiment_id, 'ready': not blockers,
            'comparison_integrity': comparison,
            'blockers': dict(blockers), 'binding': {'manifest_sha256': sha(manifest_bytes),
                                                 'summary_sha256': sha(summary_bytes),
                                                 'results_sha256': sha(results_bytes),
                                                 'runtime_sha256': runtime_sha,
                                                 'exporter_sha256': sha(Path(__file__).read_bytes())},
            'counts': {'primary': 96, 'examples': 48, 'attempts': len(attempts),
                       'reruns': len(attempts) - len(primary_ids)}, 'examples': examples, 'arms': arms,
            'semantics': {'run_duration': 'import_duration_only', 'time_feedback': 'original_measured_ms',
                          'source_sha256': 'sha256_of_canonical_final_primary_journal_row',
                          'human_change': 'human_observation_only_otherwise_unknown',
                          'scope': 'primary_heldout_only_special_cases_remain_local'}}


def target_for(rows):
    """The SDK target looks up local facts; it cannot execute business work."""
    by_key = {(row['request_id'], row['repeat']): row for row in rows}

    def measured_result(inputs):
        from langsmith import get_current_run_tree
        row = by_key[(inputs['request_id'], inputs['repeat'])]
        run = get_current_run_tree()
        if run is None:
            raise RuntimeError('Official evaluation run context is missing')
        run.add_metadata({**{key: row[key] for key in ('task_id', 'session_id', 'case_id', 'attempt_id',
                                                       'revision', 'status') if row[key] is not None},
                          'sha256': row['source_sha256']})
        return {key: row[key] for key in ('task_id', 'case_id', 'attempt_id', 'status', 'source_sha256', 'metrics')}

    return measured_result


def factual_scores(run, example):
    # No model judge, comments, correction, raw closure, prompt, or geometry.
    scores = run.outputs['metrics']
    return {'results': [{'key': key, **({'value': 'unknown'} if value is None else {'score': value})}
                        for key, value in scores.items()]}


def official_url(value):
    from agent.observability import _contains_configured_key
    if not isinstance(value, str):
        raise RuntimeError('The SDK did not supply an official resource URL')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or _contains_configured_key(value)):
        raise RuntimeError('The SDK returned an invalid resource URL')
    return value


def sdk_runtime():
    """Accept the locked SDK's bounded version information, never arbitrary text."""
    from langsmith.env import get_runtime_environment
    runtime = get_runtime_environment()
    allowed = {'sdk', 'sdk_version', 'library', 'platform', 'runtime', 'py_implementation',
               'runtime_version', 'langchain_version', 'langchain_core_version'}
    if (set(runtime) - allowed or runtime.get('sdk') != 'langsmith-py'
            or runtime.get('sdk_version') != '0.6.4' or runtime.get('runtime') != 'python'):
        raise RuntimeError('STOP: unreviewed SDK runtime metadata')
    for value in runtime.values():
        identifier(value)
    return runtime


@contextmanager
def safe_project_updates(client, binding):
    """Keep SDK evaluation mechanics; discard its automatic Git identity metadata.

    The existing SafeClient owns trace masking. Only this export's single client
    instance is wrapped, and its original attribute is restored on every exit.
    """
    original = client.update_project
    had_override = 'update_project' in client.__dict__
    previous = client.__dict__.get('update_project')

    def update_project(project_id, **kwargs):
        kwargs['metadata'] = dict(binding)
        kwargs.pop('project_extra', None)
        return original(project_id, **kwargs)

    client.update_project = update_project
    try:
        yield
    finally:
        if had_override:
            client.update_project = previous
        else:
            del client.update_project


def verify_platform(client, project, facts, uploaded):
    """Read back all target identities and feedback; bounded ingestion wait only."""
    expected = {row['task_id']: row for row in facts}
    example_ids = {str(item.id): (item.inputs['request_id'], item.inputs['repeat']) for item in uploaded}
    for attempt in range(3):
        stored = list(client.list_runs(project_id=project.id, is_root=True, limit=49))
        if len(stored) == 48:
            feedback = list(client.list_feedback(run_ids=[run.id for run in stored], limit=433))
            if len(feedback) == 432:
                break
        if attempt < 2:
            time.sleep(1)
    else:
        raise RuntimeError('Platform run or feedback count is incomplete; inspect retained receipt')
    seen, by_run = set(), {}
    for run in stored:
        metadata = run.extra.get('metadata', {})
        fact = expected.get(metadata.get('task_id'))
        if (fact is None or run.error or run.inputs or run.outputs
                or any(metadata.get(key) != fact[key] for key in
                       ('task_id', 'session_id', 'case_id', 'attempt_id', 'revision') if fact[key] is not None)
                or metadata.get('sha256') != fact['source_sha256']
                or example_ids.get(str(run.reference_example_id)) != (fact['request_id'], fact['repeat'])):
            raise RuntimeError('Platform run lost its masked business/source identity')
        seen.add(fact['task_id'])
        by_run[str(run.id)] = fact
    if len(seen) != 48:
        raise RuntimeError('Platform runs contain duplicate business identities')
    observed = set()
    for item in feedback:
        fact = by_run.get(str(item.run_id))
        key = (str(item.run_id), item.key)
        if fact is None or item.key not in fact['metrics'] or key in observed:
            raise RuntimeError('Unexpected or duplicate platform feedback')
        observed.add(key)
        value = fact['metrics'][item.key]
        if (value is None and (item.score is not None or item.value != 'unknown')) or (
                value is not None and (item.score is None or not math.isclose(item.score, value, abs_tol=.0001))):
            raise RuntimeError('Platform scalar feedback differs from the measured local fact')


def execute(artifact, output):
    comparison = artifact.get('comparison_integrity', {})
    if (not artifact['ready'] or comparison.get('status') != 'no_observed_runtime_drift'
            or comparison.get('summary_matches_runtime') is not True
            or not artifact['binding'].get('runtime_sha256')):
        raise ValueError('STOP: incomplete or unverified comparison; see safe artifact blockers')
    from agent.observability import configure_langsmith
    from langsmith import tracing_context
    client = configure_langsmith()
    if client is None:
        raise RuntimeError('STOP: explicit LangSmith configuration is unavailable or unsafe')
    artifact_sha = sha(canonical(artifact))
    receipt_path = output.with_suffix('.receipt.json')
    name = f"t07-{artifact['experiment_id'][:40]}-{artifact_sha[:16]}"
    if receipt_path.exists():
        raise ValueError('STOP: receipt already exists; inspect platform state before any retry')
    if list(client.list_datasets(dataset_name=name, limit=1)) or any(
            list(client.list_projects(name=f'{name}-{arm}', limit=1)) for arm in ('A0', 'A1')):
        raise ValueError('STOP: a named dataset or experiment already exists; inspect before importing')
    receipt = {'schema_version': 1, 'artifact_sha256': artifact_sha, 'binding': artifact['binding'],
               'state': 'creating_dataset', 'dataset_name': name, 'counts': artifact['counts'], 'experiments': {}}
    write(receipt_path, receipt, exclusive=True)
    try:
        runtime = sdk_runtime()
        dataset = client.create_dataset(name, description='Imported fixed T07 primary facts; GIS is not rerun.',
                                        metadata={**artifact['binding'], 'runtime': runtime})
        receipt['sdk_runtime_keys'] = sorted(runtime)
        receipt.update(dataset_id=str(dataset.id), dataset_url=official_url(dataset.url), state='creating_examples')
        write(receipt_path, receipt)
        examples = [{**example, 'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'{artifact_sha}:{index}'))}
                    for index, example in enumerate(artifact['examples'])]
        client.create_examples(dataset_id=dataset.id, examples=examples)
        uploaded = list(client.list_examples(dataset_id=dataset.id))
        if ({str(example.id): {'inputs': example.inputs, 'outputs': example.outputs} for example in uploaded}
                != {example['id']: {'inputs': example['inputs'], 'outputs': example['outputs']} for example in examples}):
            raise RuntimeError('Uploaded examples differ from the reviewed safe payload')
        for arm in ('A0', 'A1'):
            receipt.update(state=f'creating_{arm}')
            receipt['experiments'][arm] = {'name': f'{name}-{arm}', 'state': 'creating'}
            write(receipt_path, receipt)
            project = client.create_project(f'{name}-{arm}', reference_dataset_id=dataset.id,
                                            metadata=artifact['binding'])
            receipt['experiments'][arm].update(id=str(project.id), url=official_url(project.url), state='evaluating')
            write(receipt_path, receipt)
            with safe_project_updates(client, artifact['binding']), tracing_context(
                    enabled=False, client=client, parent=False, replicas=[]):
                result = client.evaluate(target_for(artifact['arms'][arm]), data=uploaded,
                                         experiment=project, evaluators=[factual_scores],
                                         max_concurrency=0, num_repetitions=1, blocking=True,
                                         upload_results=True, error_handling='log')
            result.wait()
            completed = list(result)
            if len(completed) != 48 or any(row['run'].error for row in completed):
                raise RuntimeError('Official evaluation did not complete all 48 imported facts')
            for row in completed:
                feedback = row['evaluation_results']['results']
                if len(feedback) != len(BOOL_METRICS) + len(TIME_METRICS) or any(
                        item.extra and item.extra.get('error') for item in feedback):
                    raise RuntimeError('Official evaluation feedback is incomplete')
            client.flush()
            verify_platform(client, project, artifact['arms'][arm], uploaded)
            receipt['experiments'][arm].update(state='complete', imported_runs=48,
                                                name=result.experiment_name)
            write(receipt_path, receipt)
        receipt['state'] = 'complete'
        write(receipt_path, receipt)
        return receipt
    except BaseException as exc:
        receipt.update(state='needs_inspection', error_type=type(exc).__name__)
        write(receipt_path, receipt)
        raise RuntimeError('STOP: import did not finish; inspect the retained receipt before retry') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--summary', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        artifact = build_artifact(args.manifest, args.summary or args.manifest.parent / 'summary.json')
        if args.output.exists():
            if read(args.output) != artifact:
                raise ValueError('STOP: reviewed artifact differs from current local evidence; use a new output')
        elif args.execute:
            raise ValueError('STOP: create and review the offline artifact before --execute')
        else:
            write(args.output, artifact, exclusive=True)
        if args.execute:
            execute(artifact, args.output)
        print(json.dumps({'mode': 'execute' if args.execute else 'dry_run', 'ready': artifact['ready'],
                          'artifact_sha256': sha(canonical(artifact)), 'counts': artifact['counts'],
                          'blockers': artifact['blockers']}))
        return 0
    except Exception as exc:
        # Provider and JSON exceptions may include input text, URLs or credentials.
        print(f'T07 export STOP ({type(exc).__name__}); inspect local evidence and receipt.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
