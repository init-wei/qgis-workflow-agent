#!/usr/bin/env python3
"""Run the fixed T07 HTTP experiment and rebuild its local evidence summaries.

No business modules are imported: credentials belong only to the server. A run
uses public HTTP decisions, immutable slots, and read-only independent oracles.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import fcntl
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
import uuid

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEFINITION = ROOT / 'tests/fixtures/gis/t07_benchmark.json'
USAGE_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens',
                'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'reasoning_tokens')
SUCCESS = {'complete', 'complete_empty'}
TERMINAL_EXECUTION = {'success', 'failed', 'rejected', 'reconciled'}
FAMILIES = {'load', 'reproject', 'buffer', 'clip', 'dissolve', 'statistics'}


def utc_now():
    return datetime.now(UTC).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value, *, exclusive=False):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def append(path, row):
    # One owner process, append-only facts, durable even when a later task fails.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def journal(path):
    if not Path(path).exists():
        return []
    rows = []
    for index, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f'Invalid journal line {index}; preserve and repair evidence explicitly') from exc
        if not isinstance(row, dict):
            raise ValueError(f'Journal line {index} is not an object')
        rows.append(row)
    return rows


def requests_from(definition):
    """Definition requests are frozen verbatim; no prompt rewriting in the runner."""
    rows = definition.get('requests')
    if rows is None:
        rows = []
        for scenario in definition.get('scenarios', []):
            for request in scenario['requests']:
                rows.append({'request_id': request['id'], 'scenario_id': scenario['id'],
                             'family': scenario['family'], 'split': 'heldout',
                             'message': request['text'], 'datasets': scenario['inputs'],
                             'expected': scenario['expected'],
                             'expected_candidate_families': scenario['expected_candidate_families']})
        for reference in definition.get('dev_references', []):
            rows.append({'request_id': reference['request']['id'], 'scenario_id': reference['id'],
                         'family': reference['family'], 'split': 'dev',
                         'message': reference['request']['text'], 'datasets': reference['inputs'],
                         'dev_reference': reference})
    if not isinstance(rows, list):
        raise ValueError('Definition requests must be a list')
    ids = [row['request_id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Request IDs must be unique')
    return rows


def source_inventory():
    return {str(path.relative_to(ROOT)): digest(path) for directory in
            ('agent', 'ui/backend', 'qgis_mcp_server', 'scripts') for path in sorted((ROOT / directory).rglob('*.py'))}


def template_inventory():
    return {path.name: digest(path) for path in sorted((ROOT / 'agent/skills').glob('*.json'))}


def validate_pricing(pricing, requested_model=None):
    if (not isinstance(pricing, dict) or not isinstance(pricing.get('source_url'), str) or not pricing['source_url'].strip()
            or not isinstance(pricing.get('fetched_at'), str) or not pricing['fetched_at'].strip()):
        raise ValueError('Pricing requires a source_url and fetched_at timestamp')
    source = urlparse(pricing['source_url'])
    if source.scheme not in {'http', 'https'} or not source.hostname or source.username or source.password:
        raise ValueError('Pricing source_url must be an uncredentialed HTTP source')
    try:
        fetched = datetime.fromisoformat(pricing['fetched_at'].replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Pricing fetched_at must be an ISO timestamp') from exc
    if fetched.tzinfo is None:
        raise ValueError('Pricing fetched_at requires an explicit timezone')
    model = pricing.get('requested_model')
    providers = pricing.get('provider_response_models')
    if (not isinstance(model, str) or not model or (requested_model is not None and model != requested_model)
            or not isinstance(providers, list) or not providers
            or any(not isinstance(value, str) or not value for value in providers)
            or len(providers) != len(set(providers))):
        raise ValueError('Pricing requires the requested model and a nonempty provider response model allowlist')
    periods = pricing.get('usd_per_million')
    if not isinstance(periods, dict) or set(periods) != {'peak', 'off_peak'}:
        raise ValueError('Pricing requires both peak and off_peak rates')
    for rates in periods.values():
        if not isinstance(rates, dict) or set(rates) != {'cache_hit', 'cache_miss', 'output'} or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                for value in rates.values()):
            raise ValueError('Pricing rates must be finite nonnegative numbers')


def schedule(args):
    if not args.model.strip() or args.max_tokens < 1 or not math.isfinite(args.temperature):
        raise ValueError('A model, positive max_tokens and finite temperature are required')
    definition_path = args.definition.resolve()
    definition = read_json(definition_path)
    requests = [row for row in requests_from(definition) if row['split'] == args.split]
    if not requests:
        raise ValueError('No requests for the selected split')
    if args.split == 'heldout':
        scenarios = Counter(row['scenario_id'] for row in requests)
        if len(requests) != 24 or len(scenarios) != 12 or set(scenarios.values()) != {2}:
            raise ValueError('Formal heldout requires exactly 12 scenarios, two requests each')
        if Counter(row['family'] for row in requests) != Counter({family: 4 for family in FAMILIES}):
            raise ValueError('Formal heldout requires four requests per task family')
    order = [('A0', 1), ('A1', 1), ('A1', 2), ('A0', 2)]
    slots = []
    for request in requests:
        for arm, repeat in order:
            slots.append({'slot_id': f"{request['request_id']}:{arm}:r{repeat}",
                          'request_id': request['request_id'], 'scenario_id': request['scenario_id'],
                          'family': request['family'], 'group': args.split, 'arm': arm, 'repeat': repeat})
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True)
    diff = subprocess.run(['git', 'diff', 'HEAD', '--'], cwd=ROOT, capture_output=True, check=True)
    manifest = {'schema_version': 1, 'experiment_id': args.experiment_id, 'phase': args.split,
                'created_at': utc_now(), 'git_sha': git.stdout.strip(),
                'dirty_patch_sha256': hashlib.sha256(diff.stdout).hexdigest(),
                'source_sha256': source_inventory(),
                'snyder_helper_sha256': digest(ROOT / 'scripts/build_spatial_oracle.py'),
                'lock_sha256': digest(ROOT / 'uv.lock'), 'definition_path': str(definition_path),
                'definition_sha256': digest(definition_path), 'requests': requests,
                'schedule': slots, 'review_policy': {'operator': 'automated_developer_operator',
                                                   'max_operator_revisions': 0},
                'llm_config': {'model': args.model, 'temperature': args.temperature,
                               'max_tokens': args.max_tokens},
                'runner_sha256': digest(Path(__file__)),
                'eval_nonstreaming': True, 'pricing': read_json(args.pricing) if args.pricing else None,
                'special_cases': {group: definition.get(group, []) for group in ('exceptions', 'browser')},
                'oracle_sha256': digest(ROOT / 'tests/fixtures/gis/t07_benchmark_oracle.json') if (ROOT / 'tests/fixtures/gis/t07_benchmark_oracle.json').exists() else None,
                'generator_sha256': digest(ROOT / 'scripts/build_t07_fixtures.py') if (ROOT / 'scripts/build_t07_fixtures.py').exists() else None,
                'templates': template_inventory()}
    if args.pricing:
        validate_pricing(manifest['pricing'], args.model)
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_json(args.output_dir / 'manifest.json', manifest, exclusive=True)
    print(json.dumps({'manifest': str(args.output_dir / 'manifest.json'), 'planned': len(slots),
                      'arms': dict(Counter(row['arm'] for row in slots))}))


def load_manifest(path):
    manifest = read_json(path)
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported manifest version')
    slots = manifest['schedule']
    ids = [row['slot_id'] for row in slots]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate slots in manifest')
    if manifest.get('review_policy', {}).get('max_operator_revisions') != 0:
        raise ValueError('This runner supports only the frozen zero operator revision policy')
    return manifest


def collect_attempts(manifest, rows):
    slots = {slot['slot_id']: slot for slot in manifest['schedule']}
    attempts = {}
    for row in rows:
        if row.get('experiment_id') != manifest['experiment_id']:
            raise ValueError('Foreign experiment identity in results journal')
        if row.get('slot_id') not in slots:
            raise ValueError('Unknown slot in results journal')
        slot = slots[row['slot_id']]
        if any(row.get(key) != slot[key] for key in slot):
            raise ValueError('Result identity does not match frozen slot')
        key = row['attempt_id']
        if key in attempts and attempts[key]['slot_id'] != row['slot_id']:
            raise ValueError('Attempt ID reused across slots')
        current = attempts.setdefault(key, {'slot_id': row['slot_id'], 'events': []})
        if row.get('event') not in {'started', 'checkpoint', 'finished'}:
            raise ValueError('Unknown results journal event')
        if current.get('event') == 'finished':
            raise ValueError('Duplicate or subsequent facts after finished attempt')
        if row['event'] == 'started' and current['events']:
            raise ValueError('Duplicate started fact')
        current.update(row)
        current['events'].append(row['event'])
    chains = {}
    for item in attempts.values():
        chain = chains.setdefault(item['slot_id'], [])
        if not chain:
            if item.get('rerun_of') is not None or item.get('attempt_index', 1) != 1:
                raise ValueError('The primary attempt must have no rerun link and index 1')
        else:
            previous = chain[-1]
            if (item.get('rerun_of') != previous['attempt_id']
                    or item.get('attempt_index') != len(chain) + 1
                    or previous.get('event') != 'finished'
                    or previous.get('status') not in {'planning_failed', 'invalid_plan', 'execution_failed', 'oracle_failed'}):
                raise ValueError('A second attempt requires an explicit linear rerun link to a terminal failure')
        chain.append(item)
    result = []
    for item in attempts.values():
        row = {key: value for key, value in item.items() if key != 'events'}
        if row.get('event') != 'finished':
            first_complete = first_completion_verdict(row.get('first_plan_valid'),
                False if row.get('first_complete') is False else None)
            row.update(status='environment_interrupted', final_complete=None, first_complete=first_complete, interrupted=True)
        result.append(row)
    return result


def integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def usage_for(task_id, rows, expected_graph_invocations=None):
    """Prove journal completeness against independently counted HTTP starts."""
    target_invocations = {row['invocation_id'] for row in rows if task_id and row.get('task_id') == task_id
                          and isinstance(row.get('invocation_id'), str)}
    relevant = [row for row in rows if task_id and (row.get('task_id') == task_id or
                (isinstance(row.get('invocation_id'), str) and row['invocation_id'] in target_invocations))]
    graph_events = {'graph_start', 'graph_end', 'graph_error'}
    models = {'start', 'end', 'error'}
    graphs, runs, reasons = {}, {}, []
    if any(not isinstance(row.get('task_id'), str) or not row.get('task_id')
           or not isinstance(row.get('invocation_id'), str) or not row.get('invocation_id') for row in rows):
        reasons.append('unattributed_journal_event')
    unidentified_starts = 0
    for row in relevant:
        if row.get('task_id') != task_id:
            reasons.append('journal_task_identity_mismatch')
        event = row.get('event')
        identity = row.get('invocation_id')
        if event in graph_events:
            if not isinstance(identity, str) or not identity:
                reasons.append('graph_identity_missing')
            else:
                graphs.setdefault(identity, []).append(row)
        elif event in models:
            run_id = row.get('run_id')
            if not isinstance(run_id, str) or not run_id:
                reasons.append('model_run_identity_missing')
                unidentified_starts += event == 'start'
            else:
                runs.setdefault(run_id, []).append(row)
            if not isinstance(identity, str) or not identity:
                reasons.append('model_graph_identity_missing')
        else:
            reasons.append('unknown_journal_event')
    if not task_id:
        reasons.append('business_identity_missing')
    expected = integer(expected_graph_invocations)
    if expected is None:
        reasons.append('independent_graph_count_unknown')
    elif len(graphs) != expected:
        reasons.append('graph_count_mismatch')
    summary_model_count = 0
    for identity, events in graphs.items():
        starts = [row for row in events if row['event'] == 'graph_start']
        ends = [row for row in events if row['event'] in {'graph_end', 'graph_error'}]
        if len(starts) != 1 or len(ends) != 1:
            reasons.append('graph_summary_missing_or_duplicate')
            continue
        summary = ends[0]
        ids = summary.get('model_run_ids')
        count = integer(summary.get('model_start_count'))
        if count is not None:
            summary_model_count += count
        if (not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids)
                or len(ids) != len(set(ids)) or count != len(ids)
                or integer(summary.get('unidentified_model_events')) != 0
                or integer(summary.get('write_error_count')) != 0):
            reasons.append('graph_summary_incomplete')
            continue
        observed = {run_id for run_id, rows_for_run in runs.items()
                    if any(row.get('invocation_id') == identity for row in rows_for_run)}
        if set(ids) != observed:
            reasons.append('model_run_inventory_mismatch')
        if sum(row.get('event') == 'start' and row.get('invocation_id') == identity for row in relevant) != count:
            reasons.append('model_start_count_mismatch')
    paired = {}
    model_runs = []
    for run_id, events in runs.items():
        starts = [row for row in events if row['event'] == 'start']
        ends = [row for row in events if row['event'] == 'end']
        errors = [row for row in events if row['event'] == 'error']
        identities = {row.get('invocation_id') for row in events if isinstance(row.get('invocation_id'), str)}
        valid_pair = (len(starts) == len(ends) == 1 and not errors
                      and ends[0].get('usage_source') == 'provider_response_metadata.token_usage'
                      and isinstance(ends[0].get('usage'), dict))
        if len(identities) != 1 or not identities <= set(graphs):
            reasons.append('model_invocation_mismatch')
        if not valid_pair:
            reasons.append('model_response_incomplete')
        paired[run_id] = ends[0].get('usage', {}) if valid_pair else {}
        requests = {row.get('requested_model') for row in events
                    if isinstance(row.get('requested_model'), str) and row['requested_model']}
        model_runs.append({'run_id': run_id, 'requested_model': next(iter(requests)) if len(requests) == 1 else None,
                           'requested_model_values': sorted(requests),
                           'provider_response_model': ends[0].get('provider_response_model') if valid_pair else None,
                           'usage': {field: integer(paired[run_id].get(field)) for field in USAGE_FIELDS}})
    complete = not reasons
    expected_calls = max(len(runs) + unidentified_starts, summary_model_count)
    counts = {}
    for field in USAGE_FIELDS:
        values = [integer(usage.get(field)) for usage in paired.values()]
        known = [value for value in values if value is not None]
        counts[field] = {'total': sum(known) if complete and expected_calls > 0 and len(known) == expected_calls else None,
                         'known_partial_sum': sum(known), 'known_calls': len(known), 'expected_calls': expected_calls,
                         'field_complete': complete and len(known) == expected_calls if expected_calls > 0 else None}
    return {'calls': len(runs), 'journal_integrity_complete': complete, 'no_model_calls': complete and expected_calls == 0, 'integrity_errors': sorted(set(reasons)),
            'expected_graph_invocations': expected, 'observed_graph_invocations': len(graphs),
            'fields': counts, 'run_ids': sorted(runs), 'per_model_runs': model_runs}


def cost_for(usage, pricing):
    """Only journal-audited, model-bound, consistent raw counts can be priced."""
    def unknown(status):
        return {'cost_usd': None, 'cost_estimate_range_usd': None, 'cost_status': status}
    if usage.get('no_model_calls'):
        return unknown('not_applicable_no_model_calls')
    if not usage.get('journal_integrity_complete'):
        return unknown('usage_audit_incomplete')
    try:
        validate_pricing(pricing)
    except ValueError:
        return unknown('pricing_unknown')
    models = usage.get('per_model_runs', [])
    if not models or len(models) != usage['calls']:
        return unknown('model_identity_unknown')
    total_lower = total_upper = 0.0
    for run in models:
        if (run['requested_model'] != pricing['requested_model']
                or run['provider_response_model'] not in pricing['provider_response_models']):
            return unknown('model_identity_unknown_or_mismatch')
        raw = run['usage']
        prompt, output = raw['prompt_tokens'], raw['completion_tokens']
        hit, miss = raw['prompt_cache_hit_tokens'], raw['prompt_cache_miss_tokens']
        if output is None or (prompt is None and (hit is None or miss is None)):
            return unknown('usage_unknown')
        if prompt is not None and ((hit is not None and hit > prompt) or (miss is not None and miss > prompt)
                                   or (hit is not None and miss is not None and hit + miss != prompt)):
            return unknown('cache_usage_inconsistent')
        per_call = []
        for rate in pricing['usd_per_million'].values():
            if hit is not None and miss is not None:
                lower = upper = hit * rate['cache_hit'] + miss * rate['cache_miss'] + output * rate['output']
            else:
                lower = prompt * min(rate['cache_hit'], rate['cache_miss']) + output * rate['output']
                upper = prompt * max(rate['cache_hit'], rate['cache_miss']) + output * rate['output']
            per_call.append((lower / 1e6, upper / 1e6))
        total_lower += min(value[0] for value in per_call)
        total_upper += max(value[1] for value in per_call)
    return {'cost_usd': None, 'cost_estimate_range_usd': [total_lower, total_upper],
            'cost_status': 'observed_usage_tariff_range'}


def fraction(rows, field, planned):
    known = [row[field] for row in rows if isinstance(row.get(field), bool)]
    return {'n': sum(known), 'N': planned, 'percent': 100 * sum(known) / planned if planned and known else None,
            'unknown': planned - len(known)}


def percentile(values, quantile):
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def aggregate(args):
    manifest = load_manifest(args.manifest)
    folder = args.manifest.resolve().parent
    rows = collect_attempts(manifest, journal(args.results or folder / 'results.jsonl'))
    usages = journal(args.usage or folder / 'usage.jsonl')
    first = {}
    for row in rows:
        first.setdefault(row['slot_id'], row)
    primary = []
    for slot in manifest['schedule']:
        primary.append(first.get(slot['slot_id'], {**slot, 'status': 'not_run'}))
    enriched = []
    for row in rows:
        row = dict(row)
        usage = usage_for(row.get('task_id'), usages, row.get('expected_graph_invocations')
                          if row.get('graph_invocation_count_known') is True else None)
        row['usage'] = usage
        row.update(cost_for(usage, manifest.get('pricing')))
        enriched.append(row)
    arms = {}
    for arm in ('A0', 'A1'):
        slots = [row for row in primary if row['arm'] == arm]
        actual = [row for row in slots if row['status'] != 'not_run']
        arms[arm] = {'planned': len(slots), 'attempted': len(actual),
                     'statuses': dict(Counter(row['status'] for row in slots)),
                     **{field: fraction(actual, field, len(slots))
                        for field in ('first_plan_valid', 'first_complete', 'final_complete')}}
        for field in ('planning_ms', 'execution_ms', 'wall_ms'):
            values = [row[field] for row in actual if isinstance(row.get(field), (int, float))]
            arms[arm][field] = {'n': len(values), 'p50': percentile(values, .5), 'p95': percentile(values, .95)}
        candidates = [row for row in actual if isinstance(row.get('candidate_families'), list)]
        related = sum(len(set(row['candidate_families']) & set(row.get('expected_candidate_families', [])))
                      for row in candidates)
        provided = sum(len(row['candidate_families']) for row in candidates)
        arms[arm]['matching'] = ({'precision': {'n': related, 'N': provided,
                                              'rate': related / provided if provided else None,
                                              'percent': 100 * related / provided if provided else None},
                                  'coverage': {'n': sum(row['family'] in row['candidate_families'] for row in candidates),
                                               'N': len(slots), 'known': len(candidates),
                                               'percent': 100 * sum(row['family'] in row['candidate_families'] for row in candidates) / len(slots) if slots else None,
                                               'unknown': len(slots) - len(candidates)}}
                                 if arm == 'A1' else None)
    usage_summary = {}
    for field in USAGE_FIELDS:
        facts = [row['usage']['fields'][field] for row in enriched if not row['usage']['no_model_calls']]
        complete = facts and all(fact['total'] is not None for fact in facts)
        usage_summary[field] = {'total': sum(fact['total'] for fact in facts) if complete else None,
                                'known_partial_sum': sum(fact['known_partial_sum'] for fact in facts),
                                'known_calls': sum(fact['known_calls'] for fact in facts),
                                'expected_calls': sum(fact['expected_calls'] for fact in facts),
                                'field_complete': bool(complete) if facts else None}
    for arm in arms:
        observations = [row for row in enriched if row['arm'] == arm]
        arms[arm]['usage'] = {}
        for field in USAGE_FIELDS:
            facts = [row['usage']['fields'][field] for row in observations if not row['usage']['no_model_calls']]
            arms[arm]['usage'][field] = {
                'total': sum(row['total'] for row in facts) if facts and all(row['total'] is not None for row in facts) else None,
                'known_partial_sum': sum(row['known_partial_sum'] for row in facts),
                'known_calls': sum(row['known_calls'] for row in facts),
                'expected_calls': sum(row['expected_calls'] for row in facts),
                'field_complete': all(row['total'] is not None for row in facts) if facts else None}
        priced = [row for row in observations if not row['usage']['no_model_calls']]
        intervals = [row['cost_estimate_range_usd'] for row in priced if row['cost_estimate_range_usd'] is not None]
        arms[arm]['cost'] = {'cost_usd': None, 'known_attempts': len(intervals), 'attempts': len(observations), 'not_applicable_attempts': len(observations) - len(priced),
                             'observed_usage_estimate_range_usd': [sum(row[0] for row in intervals), sum(row[1] for row in intervals)]
                             if intervals and len(intervals) == len(priced) else None,
                             'known_partial_estimate_range_usd': [sum(row[0] for row in intervals), sum(row[1] for row in intervals)] if intervals else None}
    human = [row for row in enriched if row.get('operator_kind') == 'human' and isinstance(row.get('human_change'), bool)]
    pairs = Counter()
    keyed = {(row['request_id'], row['repeat'], row['arm']): row for row in primary}
    for request in manifest['requests']:
        for repeat in (1, 2):
            left, right = (keyed[(request['request_id'], repeat, arm)] for arm in ('A0', 'A1'))
            a, b = left.get('final_complete'), right.get('final_complete')
            pairs['unknown' if not isinstance(a, bool) or not isinstance(b, bool)
                  else f'A0_{int(a)}_A1_{int(b)}'] += 1
    special_rows = journal(getattr(args, 'special_results', None) or folder / 'special-results.jsonl')
    declared_cases = {(group, case['id']): case for group, cases in manifest.get('special_cases', {}).items()
                      for case in cases}
    validated_specials = {}
    for observation in special_rows:
        key = (observation.get('group'), observation.get('case_id'))
        if (key[0] not in {'exceptions', 'browser'} or key not in declared_cases
                or observation.get('experiment_id') != manifest['experiment_id']):
            raise ValueError('Foreign group, case or experiment identity in special-case results')
        if key in validated_specials:
            raise ValueError('Duplicate special-case result; use a separate experiment for reruns')
        if any(field in observation for field in ('id', 'metrics', 'input', 'expected', 'category', 'execution', 'actions')):
            raise ValueError('A special observation cannot replace the frozen case definition')
        if observation.get('status') not in {'passed', 'failed', 'not_run'}:
            raise ValueError('Special-case status must be passed, failed or not_run')
        if observation['status'] != 'not_run':
            references = observation.get('evidence_refs')
            if not isinstance(references, list) or not references:
                raise ValueError('Special-case acceptance requires hashed local evidence references')
            for reference in references:
                if not isinstance(reference, dict) or not isinstance(reference.get('path'), str):
                    raise ValueError('Each evidence reference requires path and sha256')
                checksum = reference.get('sha256')
                if (not isinstance(checksum, str) or len(checksum) != 64
                        or any(character not in '0123456789abcdef' for character in checksum)):
                    raise ValueError('Evidence SHA256 must be a lowercase 64-character hash')
                path = Path(reference['path'])
                path = path if path.is_absolute() else folder / path
                if not path.is_file() or digest(path) != checksum:
                    raise ValueError('Special-case evidence is missing or its checksum changed')
        validated_specials[key] = observation
    special = {}
    for group, definitions in manifest.get('special_cases', {}).items():
        observations = [row for (owner, _), row in validated_specials.items() if owner == group]
        actual = [row for row in observations if row['status'] != 'not_run']
        special[group] = {'planned': len(definitions), 'passed': sum(row['status'] == 'passed' for row in actual),
                          'failed': sum(row['status'] == 'failed' for row in actual),
                          'not_run': len(definitions) - len(actual),
                          'cases': [{**case, **validated_specials.get((group, case['id']), {'status': 'not_run'})}
                                    for case in definitions]}
    fixed_metrics = {}
    for field in ('false_success', 'unapproved_execution'):
        declarations = {key for key, case in declared_cases.items() if field in case.get('metrics', [])}
        facts = [row for key, row in validated_specials.items() if key in declarations and row['status'] != 'not_run']
        fixed_metrics[field] = fraction(facts, field, len(declarations))
    claimed = [row for row in enriched if row.get('business_claimed_success') is True
               or row['status'] in SUCCESS | {'oracle_failed'}]
    normal_success = fraction(claimed, 'false_success', len(claimed))
    normal_approval = fraction(enriched, 'unapproved_execution', len(enriched))
    runtime = journal(folder / 'runtime.jsonl')
    drift_count = sum(row.get('event') == 'frozen_runtime_drift' for row in runtime)
    summary = {'schema_version': 1, 'experiment_id': manifest['experiment_id'],
               'planned': len(primary), 'attempts': len(rows), 'reruns': len(rows) - len(first),
               'statuses': dict(Counter(row['status'] for row in primary)), 'arms': arms,
               'comparison_integrity': {'status': 'incomparable' if drift_count else 'no_observed_runtime_drift',
                                        'runtime_drift_count': drift_count},
               'paired_completion': dict(pairs), 'usage': usage_summary,
               'usage_journal_integrity': {'unattributed_event_count': sum(
                   not isinstance(row.get('task_id'), str) or not row.get('task_id')
                   or not isinstance(row.get('invocation_id'), str) or not row.get('invocation_id') for row in usages)},
               'journal_integrity_complete': bool(enriched) and all(row['usage']['journal_integrity_complete'] for row in enriched),
               'usage_integrity': [{'attempt_id': row['attempt_id'], 'journal_integrity_complete': row['usage']['journal_integrity_complete'],
                                    'no_model_calls': row['usage']['no_model_calls'],
                                    'per_model_runs': row['usage']['per_model_runs'],
                                    'errors': row['usage']['integrity_errors']} for row in enriched],
               'special_cases': special,
               'human_change_rate': {'n': sum(row['human_change'] for row in human), 'N': len(human),
                                     'rate': sum(row['human_change'] for row in human) / len(human) if human else None,
                                     'percent': 100 * sum(row['human_change'] for row in human) / len(human) if human else None, 'unknown': 0},
               'operator_feedback_count': sum(row.get('operator_feedback_count', 0) for row in enriched),
               'complexity': {'model_calls_observed': sum(row['usage']['calls'] for row in enriched),
                              'tool_attempts': sum(row.get('tool_call_count', 0) for row in enriched),
                              'backend_starts': sum(row.get('backend_starts', 0) for row in runtime)},
               'normal_claimed_success': {'false_success': normal_success},
               'normal_approval_checks': {'unapproved_execution': normal_approval},
               'fixed_counterexamples': fixed_metrics,
               'unapproved_execution': fixed_metrics['unapproved_execution'],
               'review_checks': {key: fraction([row.get('review_checks', {}) for row in enriched], key, len(enriched))
                                 for key in sorted({key for row in enriched for key in row.get('review_checks', {})})},
               'false_success': fixed_metrics['false_success'],
               'notes': ['Primary denominators include every frozen slot; reruns are listed separately.',
                         'Unknown usage is never zero; amounts are observed-usage estimates, not billing reconciliation.']}
    target = args.output_dir or folder
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / 'summary.json', summary)
    output_rows = enriched + [row for row in primary if row['status'] == 'not_run']
    columns = ['slot_id', 'request_id', 'scenario_id', 'family', 'group', 'arm', 'repeat', 'attempt_id',
               'rerun_of', 'task_id', 'status', 'first_plan_valid', 'first_complete', 'final_complete',
               'planning_ms', 'execution_ms', 'wall_ms', 'operator_kind', 'human_change',
               'operator_feedback_count', 'false_success', 'cost_usd', 'cost_estimate_range_usd', 'cost_status']
    columns += list(USAGE_FIELDS)
    with (target / 'results.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in output_rows:
            data = dict(row)
            data.update({field: row.get('usage', {}).get('fields', {}).get(field, {}).get('total') for field in USAGE_FIELDS})
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in data.items()})
    print(json.dumps({'summary': str(target / 'summary.json'), 'planned': len(primary), 'attempts': len(rows)}))
    return summary


class Backend:
    """Only this child PID is owned; no port-based process killing."""

    def __init__(self, base_url, folder, *, timeout=60):
        self.base_url = base_url
        self.folder = folder
        self.timeout = timeout
        self.process = None
        self.log = None
        self.arm = None
        self.restarts = 0

    def switch(self, arm):
        if self.arm == arm and self.process is not None and self.process.poll() is None:
            return
        self.stop()
        parsed = urlparse(self.base_url)
        host, port = parsed.hostname, parsed.port or 80
        if host != '127.0.0.1' or port != 9000:
            raise ValueError('Managed backend requires http://127.0.0.1:9000')
        with socket.socket() as probe:
            probe.settimeout(2)
            if probe.connect_ex((host, port)) == 0:
                raise ValueError('Port 9000 is already occupied; release the current backend explicitly')
        env = {**os.environ, 'QGIS_AGENT_SKILLS_ENABLED': 'true' if arm == 'A1' else 'false',
               'QGIS_AGENT_EVAL_NONSTREAMING': 'true',
               'QGIS_AGENT_EVAL_USAGE_PATH': str(self.folder / 'usage.jsonl')}
        self.log = (self.folder / 'backend.log').open('ab')
        self.process = subprocess.Popen(['uv', 'run', '--locked', '--env-file', '.env', 'uvicorn',
                                         'ui.backend.main:app', '--host', '127.0.0.1', '--port', '9000'],
                                        cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
        self.restarts += 1
        until = time.monotonic() + self.timeout
        with httpx.Client(base_url=self.base_url, timeout=2, trust_env=False) as client:
            while time.monotonic() < until:
                if self.process.poll() is not None:
                    raise RuntimeError('Owned backend exited during startup; see local backend.log')
                try:
                    if client.get('/api/health').is_success:
                        if self.process.poll() is not None:
                            raise RuntimeError('Owned backend exited; an unrelated health response is not ownership')
                        self.arm = arm
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(.2)
        raise TimeoutError('Owned backend did not become healthy')

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=20)
                except subprocess.TimeoutExpired as exc:
                    # Do not forcibly kill a potentially writing GIS owner.
                    raise RuntimeError('Owned backend did not stop normally; inspect it before continuing') from exc
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None
        self.arm = None


def response_json(response):
    response.raise_for_status()
    return response.json()


def frozen_runtime(manifest):
    if manifest.get('runner_sha256') and digest(Path(__file__)) != manifest['runner_sha256']:
        raise ValueError('Runner changed after experiment freeze')
    if not manifest.get('snyder_helper_sha256'):
        raise ValueError('Missing frozen Snyder helper identity; create a new manifest')
    if digest(manifest['definition_path']) != manifest['definition_sha256']:
        raise ValueError('Fixture definition changed after experiment freeze')
    if digest(ROOT / 'uv.lock') != manifest['lock_sha256']:
        raise ValueError('Lock file changed after experiment freeze')
    if source_inventory() != manifest.get('source_sha256'):
        raise ValueError('Business source inventory changed after experiment freeze')
    if template_inventory() != manifest.get('templates'):
        raise ValueError('Template inventory changed after experiment freeze')
    for name, relative in (('oracle_sha256', 'tests/fixtures/gis/t07_benchmark_oracle.json'),
                           ('generator_sha256', 'scripts/build_t07_fixtures.py'),
                           ('snyder_helper_sha256', 'scripts/build_spatial_oracle.py')):
        if manifest.get(name) and digest(ROOT / relative) != manifest[name]:
            raise ValueError('Oracle, generator or Snyder helper changed after experiment freeze')


class ExperimentDrift(ValueError):
    """A frozen input/source changed; preserve facts and stop comparison."""


def check_frozen(manifest):
    try:
        frozen_runtime(manifest)
    except (OSError, ValueError) as exc:
        raise ExperimentDrift('Frozen experiment inputs changed; stop before further work') from exc


def save_evidence(folder, name, value):
    path = folder / name
    write_json(path, value)
    return {'path': str(path), 'sha256': digest(path)}


def stream_request(client, path, body, folder, name, *, audit=None):
    # Transport cancellation does not cancel a server-owned run. The caller
    # always follows with GET state, never automatically re-sends this request.
    audit = audit if audit is not None else {}
    audit.setdefault('start_events', 0)
    with client.stream('POST', path, json=body) as response:
        response.raise_for_status()
        with (folder / name).open('x', encoding='utf-8') as stream:
            for line in response.iter_lines():
                if line.strip() == 'event: start':
                    audit['start_events'] += 1
                elif line.strip() == 'event: error':
                    audit['error_event'] = True
                stream.write(line + '\n')
                stream.flush()


def wait_state(client, thread_id, timeout):
    until = time.monotonic() + timeout
    while True:
        state = response_json(client.get(f'/api/state/{thread_id}'))
        if not state.get('running'):
            return state
        if time.monotonic() >= until:
            raise TimeoutError('Task remains running; inspect current task instead of replaying')
        time.sleep(.1)


def checked_url(base_url):
    parsed = urlparse(base_url)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password:
        raise ValueError('Runner HTTP must use a local loopback URL without credentials')


def plan_errors(plan, request, uploads, pause):
    """Review the concrete task against frozen requested operations before approval."""
    expected = request.get('expected')
    if not expected:
        return ['No frozen pre-approval contract; developer pilots use the existing independent runbooks']
    errors = []
    if not isinstance(plan, dict) or plan.get('review_status') != 'pending_review':
        return ['No pending reviewable plan']
    if any(plan.get(key) != pause.get(key) for key in ('task_id', 'revision')):
        errors.append('Plan and current pause identity disagree')
    selected = {item['file_id'] for item in uploads.values()}
    inputs = plan.get('required_inputs', [])
    if {item.get('file_id') for item in inputs} != selected:
        errors.append('Plan does not use exactly the selected uploads')
    by_file = {item['file_id']: item for item in inputs}
    paths = {}
    for dataset, uploaded in uploads.items():
        item = by_file.get(uploaded['file_id'], {})
        if not item.get('path') or item.get('sha256') != uploaded.get('sha256'):
            errors.append('Plan input path or checksum does not match upload')
        paths[dataset] = item.get('path')
    calls = plan.get('tool_calls', [])
    if any(call.get('tool_name') not in {'execute_processing', 'add_layer'} for call in calls):
        errors.append('Plan contains an unrequested auxiliary tool')
    if any('repair_arguments' in call.get('metadata', {}) for call in calls):
        errors.append('Preconfigured argument repair is outside the frozen prompt-only experiment')
    if any(call.get('requires_review') is not False for call in calls):
        errors.append('Normal benchmark tasks must not require an unrequested risk operation')
    processing = [call for call in calls if call.get('tool_name') == 'execute_processing']
    algorithms = [call.get('arguments', {}).get('algorithm') for call in processing]
    if algorithms != expected['algorithms']:
        errors.append('Processing algorithm sequence does not match the request')
    outputs = plan.get('expected_outputs', [])
    declared = {item.get('output_id'): item.get('path') for item in outputs}
    steps = {item['step_id']: item for item in plan.get('steps', [])}
    if len(steps) != len(calls):
        errors.append('Every call must have a distinct reviewable step')
    file_names = expected.get('output_filenames', [])
    if len(outputs) != len(processing):
        errors.append('Processing output declarations do not match requested operations')
    previous = paths.get(request['datasets'][0]) if request['datasets'] else None
    previous_ref = next((item.get('input_id') for item in inputs if item.get('path') == previous), None)
    for index, call in enumerate(processing):
        arguments = call.get('arguments', {})
        parameters = arguments.get('parameters', {})
        algorithm = arguments.get('algorithm')
        step = steps.get(call.get('step_id'), {})
        source_key = 'INPUT_LAYER' if algorithm == 'native:basicstatisticsforfields' else 'INPUT'
        if parameters.get(source_key) != previous:
            errors.append('Processing does not consume the selected or preceding actual output')
        required_refs = [previous_ref]
        if algorithm == 'native:clip':
            overlay = paths.get(request['datasets'][1]) if len(request['datasets']) > 1 else None
            if parameters.get('OVERLAY') != overlay:
                errors.append('Clip overlay does not match selected mask')
            required_refs.append(next((item.get('input_id') for item in inputs if item.get('path') == overlay), None))
        if step.get('input_refs') != required_refs:
            errors.append('Processing input references do not match source dependencies')
        output = parameters.get('OUTPUT')
        refs = step.get('output_refs', [])
        if len(refs) != 1 or declared.get(refs[0]) != output:
            errors.append('Processing output is not the declared step artifact')
        if not isinstance(output, str) or not output.lower().endswith('.gpkg'):
            errors.append('Requested outputs must be explicit new GeoPackages')
        elif index < len(file_names) and Path(output).name != file_names[index]:
            errors.append('Output filename differs from the explicit user request')
        if algorithm == 'native:reprojectlayer' and parameters.get('TARGET_CRS') != expected.get('target_crs'):
            errors.append('Target CRS differs from request')
        if algorithm == 'native:buffer':
            if (parameters.get('DISTANCE') != expected.get('distance_m')
                    or isinstance(parameters.get('DISTANCE'), bool)
                    or parameters.get('SEGMENTS') != expected.get('segments')
                    or isinstance(parameters.get('SEGMENTS'), bool)
                    or parameters.get('DISSOLVE') is not expected.get('dissolve')
                    or parameters.get('SEPARATE_DISJOINT', False) is not False
                    or call.get('distance_unit') != 'm'):
                errors.append('Buffer distance, metre unit, segmentation or dissolve differs from request')
        if algorithm == 'native:dissolve':
            if parameters.get('FIELD') != expected.get('group_fields') or parameters.get('SEPARATE_DISJOINT', False) is not False:
                errors.append('Dissolve grouping differs from request')
        if algorithm == 'qgis:exportaddgeometrycolumns' and parameters.get('CALC_METHOD') != expected.get('calculation_method'):
            errors.append('Area calculation method differs from request')
        if algorithm == 'native:basicstatisticsforfields' and parameters.get('FIELD_NAME') != expected.get('field'):
            errors.append('Statistics field differs from request')
        previous = output
        previous_ref = refs[0] if len(refs) == 1 else None
    loads = [call for call in calls if call.get('tool_name') == 'add_layer']
    if len(loads) != len(expected.get('loads', [])):
        errors.append('Loaded layers differ from request')
    for call, contract in zip(loads, expected.get('loads', [])):
        arguments = call.get('arguments', {})
        wanted = paths.get(request['datasets'][0]) if contract['source'] == 'input' else previous
        if arguments.get('path') != wanted or arguments.get('name') != contract['name'] or arguments.get('layer_type') != 'vector':
            errors.append('Layer source, display name or type differs from request')
        if contract['source'] == 'final' and calls.index(call) < calls.index(processing[-1]):
            errors.append('Final result is loaded before it is produced')
    return list(dict.fromkeys(errors))


def verify_result(inputs_dir, slot, execution, uploads, evidence):
    identity = uuid.uuid4().hex
    request_path = evidence / f'oracle-{identity}-input.json'
    report_path = evidence / f'oracle-{identity}-report.json'
    write_json(request_path, {'execution': execution, 'uploads': uploads}, exclusive=True)
    completed = subprocess.run(['/usr/bin/python3', str(ROOT / 'scripts/build_t07_fixtures.py'),
                                'verify-result', '--manifest', str(inputs_dir / 'manifest.json'),
                                '--scenario', slot['scenario_id'], '--execution-json', str(request_path),
                                '--output-json', str(report_path)], cwd=ROOT,
                               capture_output=True, text=True, timeout=120, check=False)
    if not report_path.exists():
        raise RuntimeError('Independent oracle unavailable; no report returned')
    report = read_json(report_path)
    if (report.get('status') not in {'passed', 'failed'} or not isinstance(report.get('empty'), bool)
            or not isinstance(report.get('checks'), list) or not isinstance(report.get('errors'), list)
            or any(not isinstance(error, str) or not error for error in report['errors'])
            or (report.get('status') == 'failed') != bool(report['errors'])
            or completed.returncode != (0 if report.get('status') == 'passed' else 1)):
        raise ValueError('Independent oracle report is malformed')
    return report


def uploaded_inputs(client, request, inputs_dir, input_manifest):
    uploads = {}
    for name in request['datasets']:
        item = input_manifest['datasets'][name]
        filename = item['filename']
        if Path(filename).name != filename:
            raise ValueError('Fixture filename must be a single leaf')
        path = inputs_dir / filename
        if digest(path) != item['sha256']:
            raise ValueError('Fixture checksum changed')
        if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Fixture has a transaction sidecar')
        with path.open('rb') as stream:
            record = response_json(client.post('/api/upload', files={'file': (filename, stream, 'application/octet-stream')}))
        if record.get('sha256') != item['sha256']:
            raise ValueError('Uploaded checksum does not match frozen fixture')
        uploads[name] = record
    return uploads


def get_execution(client, task_id, *, response_evidence=None):
    if not task_id:
        return None
    response = client.get(f'/api/executions/{task_id}')
    if response_evidence is not None:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        write_json(response_evidence, {'http_status': response.status_code, 'body': body})
    if response.status_code == 404:
        return None
    execution = response_json(response)['execution']
    if not isinstance(execution, dict):
        raise ValueError('Execution query returned an invalid business record')
    return execution


def unsafe_execution(execution):
    if not execution:
        return False
    if execution.get('status') == 'reconciled' and execution.get('reconciliation'):
        return False  # Preserve the old unknown call; the explicit human fact releases the batch.
    return (execution.get('status') not in TERMINAL_EXECUTION
            or bool(execution.get('inflight_call'))
            or any((attempt.get('observation') or {}).get('outcome') == 'unknown'
                   for attempt in execution.get('attempts', [])))


def assert_snapshot_idle(snapshot, execution):
    if (snapshot.get('running') is not False or snapshot.get('pause') is not None
            or snapshot.get('continuation') is not None or snapshot.get('can_start_task') is not True
            or unsafe_execution(execution)):
        raise ValueError('The workflow still has running, paused, resumable or unverified work')
    if execution and execution.get('status') == 'success' and not isinstance(execution.get('confirmed'), bool):
        raise ValueError('Successful artifacts still await their result decision')


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_only_plan_facts(snapshot, execution, *, rejected_only=False):
    """Finite exception: an unexecuted plan may stay paused without being resumed."""
    if not isinstance(snapshot, dict) or snapshot.get('running') is not False:
        return None
    values = snapshot.get('state') or {}
    plan = values.get('task_plan')
    if not isinstance(plan, dict) or values.get('pending_risk_review') is not None or values.get('execution_trace') is not None:
        return None
    task_id, revision = plan.get('task_id'), integer(plan.get('revision'))
    if not isinstance(task_id, str) or not task_id or not revision or values.get('task_id') != task_id:
        return None
    identity = {'task_id': task_id, 'revision': revision}
    continuation = snapshot.get('continuation')
    pause = snapshot.get('pause')
    if plan.get('review_status') == 'rejected':
        if (snapshot.get('next') != ['planner_node'] or pause is not None
                or continuation != {'kind': 'resume', **identity}):
            return None
    elif not rejected_only and plan.get('review_status') == 'pending_review':
        if (snapshot.get('next') != ['human_review_node'] or continuation is not None
                or not isinstance(pause, dict) or pause.get('kind') != 'plan_review'
                or any(pause.get(key) != value for key, value in identity.items())):
            return None
    else:
        return None
    if (plan.get('review_status') == 'rejected' and execution is None
            or plan.get('review_status') == 'pending_review' and execution is not None):
        return None
    if execution is not None:
        if (execution.get('status') != 'rejected' or execution.get('task_id') != task_id
                or integer(execution.get('plan_revision')) != revision or execution.get('plan') != plan
                or execution.get('attempts') != [] or execution.get('artifacts') != []
                or 'inflight_call' not in execution or execution['inflight_call'] is not None
                or 'confirmed' not in execution or execution['confirmed'] is not None):
            return None
    return {**identity, 'review_status': plan['review_status'], 'next': snapshot['next'],
            'continuation': continuation, 'plan_sha256': json_hash(plan),
            'execution_sha256': json_hash(execution), 'zero_gis_execution': True}


def recorded_read_only_rejection(row, snapshot, execution):
    disposition = row.get('read_only_rejection_disposition')
    facts = read_only_plan_facts(snapshot, execution, rejected_only=True)
    if (row.get('status') != 'invalid_plan' or row.get('workflow_state') != 'paused_replanning_not_resumed'
            or not isinstance(disposition, dict) or not facts or disposition.get('facts') != facts):
        return False
    references = disposition.get('evidence_refs')
    return (isinstance(references, list) and len(references) == 2
            and all(isinstance(ref, dict) and isinstance(ref.get('path'), str)
                    and Path(ref['path']).is_file() and digest(ref['path']) == ref.get('sha256')
                    for ref in references))


def assert_idle(client, *, by_slot=None):
    owned = {rows[-1].get('thread_id'): rows[-1] for rows in (by_slot or {}).values()}
    for session in response_json(client.get('/api/sessions')):
        snapshot = response_json(client.get(f"/api/state/{session['thread_id']}"))
        execution = get_execution(client, snapshot.get('state', {}).get('task_id'))
        row = owned.get(session['thread_id'])
        if row is not None and row.get('read_only_rejection_disposition'):
            if not recorded_read_only_rejection(row, snapshot, execution):
                raise ValueError('Recorded read-only rejection identity or evidence changed')
            continue
        if row is None and read_only_plan_facts(snapshot, execution):
            continue
        assert_snapshot_idle(snapshot, execution)


def assert_experiment_idle(client, by_slot, *, allowed_attempt=None):
    for attempts in by_slot.values():
        row = attempts[-1]
        if row['attempt_id'] == allowed_attempt:
            continue  # Resume this original task only; never advance another one.
        if row.get('event') != 'finished' or not row.get('thread_id'):
            raise ValueError('Another experiment attempt has an unresolved checkpoint or session identity')
        snapshot = response_json(client.get(f"/api/state/{row['thread_id']}"))
        execution = get_execution(client, row.get('task_id') or snapshot.get('state', {}).get('task_id'))
        if row.get('read_only_rejection_disposition'):
            if not recorded_read_only_rejection(row, snapshot, execution):
                raise ValueError('Recorded read-only rejection identity or evidence changed')
            continue
        assert_snapshot_idle(snapshot, execution)


def assert_finished(snapshot, execution, confirmation):
    assert_snapshot_idle(snapshot, execution)
    if (snapshot.get('next') not in ([], ()) or not snapshot.get('state', {}).get('final_summary')
            or not execution or execution.get('status') != 'success' or execution.get('confirmed') is not confirmation):
        raise ValueError('No public terminal workflow and matching result confirmation')


def planning_diagnostics(values):
    records = values.get('planning_attempt_diagnostics')
    kinds = {None, 'request_rejected', 'invalid_plan', 'environment_error', 'unknown'}
    if not isinstance(records, list) or not 1 <= len(records) <= 2:
        return []
    for expected, record in enumerate(records, 1):
        if (not isinstance(record, dict) or integer(record.get('attempt')) != expected
                or 'failure_kind' not in record or record.get('failure_kind') not in tuple(kinds)
                or not isinstance(record.get('model_response_received'), bool)):
            return []
    return [{key: record[key] for key in ('attempt', 'failure_kind', 'model_response_received')} for record in records]


def first_plan_verdict(values, *, contract_errors=None):
    records = planning_diagnostics(values)
    if not records:
        return None
    first = records[0]
    if first['failure_kind'] == 'invalid_plan' and first['model_response_received']:
        return False
    if (len(records) == 1 and first['failure_kind'] is None and first['model_response_received']
            and contract_errors is not None):
        return not contract_errors
    return None


def first_completion_verdict(first_plan_valid, execution_complete):
    """Later outcomes cannot resolve an unknown first planning attempt."""
    if first_plan_valid is False:
        return False
    if first_plan_valid is True:
        return execution_complete
    return None


def determinate_planning_failure(snapshot, execution):
    values = snapshot.get('state', {})
    records = planning_diagnostics(values)
    kind = values.get('planning_failure_kind')
    if (snapshot.get('running') is not False or snapshot.get('next') not in ([], ())
            or snapshot.get('can_start_task') is not True or snapshot.get('pause') is not None
            or snapshot.get('continuation') is not None or values.get('task_plan') is not None
            or execution is not None or len(records) != 2 or integer(values.get('retry_count')) != 2
            or not values.get('planning_error') or records[-1]['failure_kind'] != kind):
        return None
    last = records[-1]
    if kind == 'invalid_plan' and last['model_response_received']:
        return kind
    if kind == 'request_rejected' and not last['model_response_received']:
        return kind
    return None


def one_task(client, manifest, slot, request, inputs_dir, input_manifest, folder, previous=None, rerun_of=None, attempt_index=1):
    results_path = folder / 'results.jsonl'
    attempt_id = previous['attempt_id'] if previous else str(uuid.uuid4())
    evidence = folder / 'evidence' / attempt_id
    evidence.mkdir(parents=True, exist_ok=bool(previous))
    row = {**slot, 'experiment_id': manifest['experiment_id'], 'attempt_id': attempt_id,
           'started_at': previous.get('started_at') if previous else utc_now(),
           'operator_kind': 'automated_developer_operator', 'operator_feedback_count': 0,
           'human_change': None, 'expected_graph_invocations': 0, 'graph_invocation_count_known': True, 'rerun_of': rerun_of, 'attempt_index': attempt_index, 'first_plan_valid': None, 'first_complete': None, 'final_complete': None}
    if previous:
        row.update({key: value for key, value in previous.items() if key not in {'event', 'events', 'status'}})
    else:
        append(results_path, {**row, 'event': 'started'})
    clock = time.monotonic()
    submission_clock = None
    stop_batch = False

    def outcome(*, first_execution_complete=None, **updates):
        row.update(updates)
        row['first_complete'] = first_completion_verdict(row.get('first_plan_valid'), first_execution_complete)

    def checkpoint(**updates):
        row.update(updates)
        row['first_complete'] = first_completion_verdict(row.get('first_plan_valid'), row.get('first_complete'))
        append(results_path, {**row, 'event': 'checkpoint'})

    def consume(path, body):
        number = len(list(evidence.glob('stream-*.sse')))
        prior_count, prior_known = row.get('expected_graph_invocations'), row.get('graph_invocation_count_known')
        checkpoint(expected_graph_invocations=None, graph_invocation_count_known=False, graph_invocation_inflight=True)
        audit = {}
        try:
            stream_request(client, path, body, evidence, f'stream-{number}.sse', audit=audit)
        except httpx.TransportError:
            checkpoint(transport_interrupted=True)
        finally:
            if audit.get('start_events') == 1 and prior_known is True and integer(prior_count) is not None:
                checkpoint(expected_graph_invocations=prior_count + 1, graph_invocation_count_known=True, graph_invocation_inflight=False)
            else:
                checkpoint(expected_graph_invocations=None, graph_invocation_count_known=False, graph_invocation_inflight=False)
        return wait_state(client, row['thread_id'], manifest.get('task_timeout', 600))

    try:
        check_frozen(manifest)
        if previous:
            if not row.get('thread_id'):
                raise RuntimeError('Interrupted before durable session identity; explicit inspected rerun required')
            uploads = read_json(evidence / 'uploads.json')
            snapshot = wait_state(client, row['thread_id'], manifest.get('task_timeout', 600))
        else:
            uploads = uploaded_inputs(client, request, inputs_dir, input_manifest)
            save_evidence(evidence, 'uploads.json', uploads)
            session = response_json(client.post('/api/sessions', json={'session_name': f"T07 {slot['slot_id']}"}))
            checkpoint(thread_id=session['thread_id'])
            message = request['message'].format(**{name: value['file_id'] for name, value in uploads.items()})
            checkpoint(submitted=True, submitted_at=utc_now())
            submission_clock = time.monotonic()
            snapshot = consume('/api/chat/stream', {'thread_id': row['thread_id'], 'case_id': slot['slot_id'],
                               'message': message, 'files': [
                                   {'file_id': value['file_id'], 'layer_name': input_manifest['datasets'][name].get('layer')}
                                   for name, value in uploads.items()], 'llm_config': manifest['llm_config']})
        if submission_clock is not None:
            row['planning_ms'] = (time.monotonic() - submission_clock) * 1000
        values = snapshot.get('state', {})
        plan = values.get('task_plan')
        task_id = values.get('task_id') or (plan or {}).get('task_id')
        checkpoint(task_id=task_id)
        save_evidence(evidence, f'state-{uuid.uuid4().hex}.json', snapshot)
        existing = get_execution(client, task_id)
        diagnostics = planning_diagnostics(values)
        first_plan_valid = first_plan_verdict(values)
        if (first_plan_valid is None and previous and previous.get('task_id') == task_id
                and previous.get('planning_attempt_diagnostics') == diagnostics
                and previous.get('revision') == (plan or (existing or {}).get('plan') or {}).get('revision')):
            first_plan_valid = previous.get('first_plan_valid')
        checkpoint(planning_failure_kind=values.get('planning_failure_kind'),
                   planning_attempt_diagnostics=diagnostics,
                   first_plan_valid=first_plan_valid)
        check_frozen(manifest)
        if (snapshot.get('pause') or {}).get('kind') == 'plan_review' and existing and existing.get('attempts'):
            outcome(status='execution_failed', failure_class='unapproved_execution',
                    unapproved_execution=True, final_complete=False, first_execution_complete=False)
            stop_batch = True
        elif unsafe_execution(existing):
            outcome(status='unknown_outcome', failure_class='unverified_external_write')
            stop_batch = True
        else:
            pause = snapshot.get('pause') or {}
            if pause.get('kind') == 'plan_review':
                row['unapproved_execution'] = False
                errors = plan_errors(plan, request, uploads, pause)
                iteration = (plan.get('metadata') or {}).get('iteration')
                row['planning_attempts'] = iteration
                row['first_plan_valid'] = first_plan_verdict(values, contract_errors=errors)
                candidates = (plan.get('metadata') or {}).get('skill_candidates', {})
                if candidates.get('enabled') is not (slot['arm'] == 'A1'):
                    raise ValueError('Backend template arm differs from frozen slot')
                row['candidate_families'] = [item['family'] for item in candidates.get('provided', [])]
                row['expected_candidate_families'] = request.get('expected_candidate_families', [request['family']])
                row['revision'] = pause['revision']
                checkpoint(plan_reviewed=True, plan_review_errors=errors,
                           review_checks={'current_plan_identity': True, 'unapproved_calls_zero': True})
                check_frozen(manifest)
                response_json(client.post('/api/review', json={'thread_id': row['thread_id'],
                              'task_id': pause['task_id'], 'revision': pause['revision'],
                              'approved': not errors, **({'advise': '固定评测请求不匹配；本次不执行。'} if errors else {})}))
                checkpoint(plan_decision_saved=True, approved=not errors)
                check_frozen(manifest)
                if errors:
                    rejected_snapshot = response_json(client.get(f"/api/state/{row['thread_id']}"))
                    rejected_state_ref = save_evidence(evidence, 'rejected-state.json', rejected_snapshot)
                    rejected_execution = get_execution(client, task_id,
                        response_evidence=evidence / 'rejected-execution-http.json')
                    check_frozen(manifest)
                    facts = read_only_plan_facts(rejected_snapshot, rejected_execution, rejected_only=True)
                    if not facts or any(facts[key] != pause[key] for key in ('task_id', 'revision')):
                        raise RuntimeError('Rejected plan is not proven to be unexecuted read-only replanning')
                    references = [rejected_state_ref,
                                  save_evidence(evidence, 'rejected-execution.json', rejected_execution)]
                    outcome(status='invalid_plan', first_execution_complete=False, final_complete=False,
                               operator_feedback_count=1, failure_class='request_contract_mismatch',
                               workflow_state='paused_replanning_not_resumed',
                               read_only_rejection_disposition={'facts': facts, 'evidence_refs': references,
                                                               'checked_at': utc_now()})
                else:
                    execution_clock = time.monotonic()
                    checkpoint(approved=True)
                    snapshot = consume(f"/api/resume/{row['thread_id']}", None)
                    row['execution_ms'] = (time.monotonic() - execution_clock) * 1000
            elif previous and (pause.get('kind') == 'result_review' or
                               (existing and isinstance(existing.get('confirmed'), bool))):
                pass
            elif previous and snapshot.get('continuation'):
                raise RuntimeError('An interrupted continuation is unresolved; inspect the current task without replaying')
            elif previous and existing and existing.get('status') in {'failed', 'rejected'}:
                outcome(status='execution_failed', final_complete=False, first_execution_complete=False,
                           failure_class='existing_terminal_execution_failure')
            elif determinate_planning_failure(snapshot, existing):
                outcome(status='planning_failed', first_plan_valid=first_plan_verdict(values),
                           first_execution_complete=False, final_complete=False, planning_attempts=values.get('retry_count'),
                           failure_class=values['planning_failure_kind'])
            else:
                raise RuntimeError('Unexpected pause; inspect current task before continuing')
            if 'status' not in row:
                execution = get_execution(client, task_id)
                if execution:
                    row['tool_call_count'] = len(execution.get('attempts', []))
                    row['artifact_count'] = len(execution.get('artifacts', []))
                    save_evidence(evidence, f'execution-{uuid.uuid4().hex}.json', execution)
                if unsafe_execution(execution):
                    outcome(status='unknown_outcome', failure_class='unverified_external_write')
                    stop_batch = True
                elif snapshot.get('running') or snapshot.get('continuation'):
                    raise RuntimeError('Approved execution remains active or resumable; its outcome is unresolved')
                elif execution and execution.get('status') in {'failed', 'rejected'}:
                    outcome(status='execution_failed', final_complete=False, first_execution_complete=False,
                               failure_class='business_execution_failed')
                elif not execution or execution.get('status') != 'success':
                    raise RuntimeError('No explicit terminal execution fact; inspect without replaying')
                else:
                    checkpoint(business_claimed_success=True)
                    check_frozen(manifest)
                    report = verify_result(inputs_dir, slot, execution, uploads, evidence)
                    if report.get('status') not in {'passed', 'failed'}:
                        raise RuntimeError('Independent oracle unavailable; no determinate verdict')
                    check_frozen(manifest)
                    passed = report['status'] == 'passed'
                    attempts = execution.get('attempts', [])
                    first_execution = (len(attempts) == len(execution.get('plan', {}).get('tool_calls', []))
                                       and all(attempt.get('status') == 'success' for attempt in attempts)
                                       and len({attempt.get('step_id') for attempt in attempts}) == len(attempts))
                    row['execution_retries'] = len(attempts) - len({attempt.get('step_id') for attempt in attempts})
                    outcome(status=('complete_empty' if report['empty'] else 'complete') if passed else 'oracle_failed',
                               final_complete=passed, first_execution_complete=passed and first_execution,
                               false_success=not passed, oracle_status=report['status'],
                               artifact_count=len(execution.get('artifacts', [])),
                               tool_call_count=len(execution.get('attempts', [])))
                    result_pause = snapshot.get('pause') or {}
                    if result_pause.get('kind') == 'result_review':
                        check_frozen(manifest)
                        response_json(client.post('/api/confirm-result', json={
                            'thread_id': row['thread_id'], 'task_id': result_pause['task_id'],
                            'revision': result_pause['revision'], 'review_id': result_pause['review_id'],
                            'is_completed': passed,
                            **({'user_feedback': '独立空间预期未通过，本次评测失败。'} if not passed else {})}))
                        checkpoint(result_confirmed=passed, review_checks={**row.get('review_checks', {}),
                                                                           'current_result_identity': True})
                        check_frozen(manifest)
                        snapshot = consume(f"/api/resume/{row['thread_id']}", None)
                    elif execution.get('confirmed') is not passed:
                        raise RuntimeError('Successful execution lacks a matching result decision identity')
                    final_execution = get_execution(client, task_id)
                    save_evidence(evidence, f'final-state-{uuid.uuid4().hex}.json', snapshot)
                    assert_finished(snapshot, final_execution, passed)
                    checkpoint(workflow_finished=True)
        check_frozen(manifest)
    except ExperimentDrift:
        for field in ('status', 'first_plan_valid', 'first_complete', 'final_complete'):
            if field in row:
                row['observed_' + field] = row[field]
        row.update(status='environment_interrupted', comparison_status='incomparable', comparable=False,
                   first_plan_valid=None, first_complete=None, final_complete=None, failure_class='frozen_runtime_drift')
        stop_batch = True
    except (OSError, ValueError, KeyError, httpx.HTTPError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        outcome(status='environment_interrupted', failure_class=type(exc).__name__, final_complete=None,
                first_execution_complete=False if row.get('first_complete') is False else None)
        stop_batch = True
        # Never stringify a provider/HTTP response into the result journal.
    row['wall_ms'] = (time.monotonic() - clock) * 1000
    row['finished_at'] = utc_now()
    row['evidence_dir'] = str(evidence)
    append(results_path, {**row, 'event': 'checkpoint' if row['status'] == 'environment_interrupted' else 'finished'})
    return row, stop_batch


def _run(args, *, client=None, backend_factory=Backend):
    manifest = load_manifest(args.manifest)
    frozen_runtime(manifest)
    if args.limit is not None and args.limit < 1:
        raise ValueError('Limit must be a positive number of actual task attempts')
    if args.timeout <= 0:
        raise ValueError('Timeout must be positive')
    if manifest.get('phase') == 'dev':
        raise ValueError('Dev references require the existing independent T03/T05/T06 pilot runbooks; heldout verifier cannot certify them')
    checked_url(args.base_url)
    if not args.manage_backend and not args.arm:
        raise ValueError('An externally managed backend requires its explicit fixed --arm')
    if args.manage_backend and args.arm:
        raise ValueError('Managed backend uses the complete frozen interleaved schedule; omit --arm')
    folder = args.manifest.resolve().parent
    inputs_dir = args.inputs_dir.resolve()
    inputs_path = inputs_dir / 'manifest.json'
    input_manifest = read_json(inputs_path)
    if input_manifest['definition_sha256'] != manifest['definition_sha256']:
        raise ValueError('Materialized inputs do not match frozen definition')
    for key in ('oracle_sha256', 'generator_sha256', 'snyder_helper_sha256'):
        if manifest.get(key) and input_manifest.get(key) != manifest[key]:
            raise ValueError('Materialized oracle or generator differs from experiment')
    # Bind the actual materialization on first use without changing the manifest.
    binding = folder / 'inputs-binding.json'
    if binding.exists():
        if read_json(binding)['sha256'] != digest(inputs_path):
            raise ValueError('Actual input manifest changed during the experiment')
    else:
        write_json(binding, {'path': str(inputs_path), 'sha256': digest(inputs_path)}, exclusive=True)
    previous = collect_attempts(manifest, journal(folder / 'results.jsonl'))
    if previous and not args.resume and not args.rerun:
        raise ValueError('Results already exist; explicitly resume or inspect and rerun a terminal failure')
    by_slot = {}
    for attempt in previous:
        by_slot.setdefault(attempt['slot_id'], []).append(attempt)
    pending_attempts = [attempts[-1] for attempts in by_slot.values() if attempts[-1].get('event') != 'finished']
    if pending_attempts and (args.rerun or not args.resume or len(pending_attempts) != 1):
        raise ValueError('Unresolved experiment checkpoints must be resolved before a new task or rerun')
    allowed_pending = pending_attempts[0]['attempt_id'] if pending_attempts else None
    if args.rerun:
        attempts = by_slot.get(args.rerun, [])
        if not attempts or attempts[-1].get('status') not in {'planning_failed', 'invalid_plan', 'execution_failed', 'oracle_failed'}:
            raise ValueError('Only an inspected known terminal failure can receive an explicit rerun; unknown writes cannot')
    manager = backend_factory(args.base_url, folder) if args.manage_backend else None
    owned_client = client is None
    client = client or httpx.Client(base_url=args.base_url, timeout=args.timeout, trust_env=False)
    keep_backend = False
    count = 0
    active_slot = None
    try:
        check_frozen(manifest)
        if manager and by_slot:
            startup = pending_attempts[0] if pending_attempts else next(reversed(by_slot.values()))[-1]
            check_frozen(manifest)
            manager.switch(startup['arm'])
            check_frozen(manifest)
        assert_experiment_idle(client, by_slot, allowed_attempt=allowed_pending)
        for slot in manifest['schedule']:
            active_slot = slot['slot_id']
            check_frozen(manifest)
            if args.rerun and slot['slot_id'] != args.rerun:
                continue
            attempts = by_slot.get(slot['slot_id'], [])
            last = attempts[-1] if attempts else None
            pending = last if last and last.get('event') != 'finished' else None
            if args.arm and slot['arm'] != args.arm:
                if not last or pending:
                    print(json.dumps({'next_slot': slot['slot_id'], 'requires_arm': slot['arm'],
                                      'message': 'Frozen interleaving stops at the next arm boundary'}), flush=True)
                    break
                continue
            if last and last.get('status') == 'unknown_outcome':
                continue  # Preserved unknown primary; public terminal facts were checked above, never replay it.
            if last and not pending and not args.rerun:
                if last['status'] == 'environment_interrupted':
                    raise ValueError('Interrupted terminal evidence requires explicit inspection before another attempt')
                continue
            if args.limit is not None and count >= args.limit:
                break
            assert_experiment_idle(client, by_slot, allowed_attempt=pending['attempt_id'] if pending else None)
            if manager:
                # Before an arm change, inspect all existing sessions while the
                # owned server is alive. Startup itself refuses occupied ports.
                if manager.arm is not None and manager.arm != slot['arm']:
                    assert_idle(client, by_slot=by_slot)
                check_frozen(manifest)
                manager.switch(slot['arm'])
                check_frozen(manifest)
            if not pending:
                assert_idle(client, by_slot=by_slot)
            request = next(row for row in manifest['requests'] if row['request_id'] == slot['request_id'])
            result, stop = one_task(client, {**manifest, 'task_timeout': args.timeout}, slot, request,
                                    inputs_dir, input_manifest, folder, previous=pending,
                                    rerun_of=last['attempt_id'] if args.rerun else None,
                                    attempt_index=len(attempts) + 1)
            fact = {**result, 'event': 'checkpoint' if result['status'] == 'environment_interrupted' else 'finished'}
            if pending:
                by_slot[slot['slot_id']][-1] = fact
            else:
                by_slot.setdefault(slot['slot_id'], []).append(fact)
            print(json.dumps({'slot_id': slot['slot_id'], 'task_id': result.get('task_id'), 'status': result['status']}), flush=True)
            count += 1
            if stop:
                if result.get('comparison_status') == 'incomparable':
                    append(folder / 'runtime.jsonl', {'event': 'frozen_runtime_drift', 'slot_id': active_slot,
                           'attempt_id': result['attempt_id'], 'comparison_status': 'incomparable', 'time': utc_now()})
                keep_backend = True
                break
            check_frozen(manifest)
    except ExperimentDrift:
        append(folder / 'runtime.jsonl', {'event': 'frozen_runtime_drift', 'slot_id': active_slot,
               'comparison_status': 'incomparable', 'time': utc_now()})
        keep_backend = True
        raise
    except BaseException:
        keep_backend = True
        raise
    finally:
        if manager and not keep_backend:
            manager.stop()
        if owned_client:
            client.close()
        if manager:
            append(folder / 'runtime.jsonl', {'event': 'runner_end', 'time': utc_now(),
                                             'backend_starts': manager.restarts,
                                             'left_owned_backend_running': keep_backend,
                                             'owned_pid': manager.process.pid if keep_backend and manager.process else None})
    aggregate(argparse.Namespace(manifest=args.manifest, results=None, usage=None, output_dir=None))
    return 1 if keep_backend else 0


def run(args, *, client=None, backend_factory=Backend):
    # Kernel releases this lock after a crash; no stale-PID recovery machinery.
    lock_path = args.manifest.resolve().parent / '.runner.lock'
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another runner owns this experiment') from exc
        return _run(args, client=client, backend_factory=backend_factory)


def main(argv=None, *, client=None, backend_factory=Backend):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    freeze = commands.add_parser('schedule', help='Freeze an ABBA manifest; never starts services')
    freeze.add_argument('--definition', type=Path, default=DEFAULT_DEFINITION)
    freeze.add_argument('--output-dir', type=Path, required=True)
    freeze.add_argument('--experiment-id', required=True)
    freeze.add_argument('--split', choices=['heldout', 'dev'], default='heldout')
    freeze.add_argument('--model', required=True)
    freeze.add_argument('--temperature', type=float, default=.1)
    freeze.add_argument('--max-tokens', type=int, default=8192)
    freeze.add_argument('--pricing', type=Path)
    dry = commands.add_parser('dry-run', help='Validate and print a frozen schedule without network')
    dry.add_argument('--manifest', type=Path, required=True)
    total = commands.add_parser('aggregate', help='Rebuild JSON and CSV without services or credentials')
    total.add_argument('--manifest', type=Path, required=True)
    total.add_argument('--results', type=Path)
    total.add_argument('--usage', type=Path)
    total.add_argument('--output-dir', type=Path)
    total.add_argument('--special-results', type=Path, help='Separately recorded exception/browser evidence journal')
    live = commands.add_parser('run', help='Run HTTP tasks; real execution is explicit')
    live.add_argument('--manifest', type=Path, required=True)
    live.add_argument('--inputs-dir', type=Path, required=True)
    live.add_argument('--base-url', default='http://127.0.0.1:9000')
    live.add_argument('--manage-backend', action='store_true')
    live.add_argument('--arm', choices=['A0', 'A1'], help='Required for an externally managed, fixed-arm backend')
    live.add_argument('--resume', action='store_true')
    live.add_argument('--limit', type=int)
    live.add_argument('--timeout', type=float, default=600)
    live.add_argument('--rerun', metavar='SLOT_ID', help='Explicit new attempt of a known terminal failure only')
    args = parser.parse_args(argv)
    try:
        if args.command == 'schedule':
            return schedule(args)
        if args.command == 'aggregate':
            return aggregate(args)
        if args.command == 'dry-run':
            manifest = load_manifest(args.manifest)
            frozen_runtime(manifest)
            print(json.dumps({'planned': len(manifest['schedule']), 'schedule': manifest['schedule']}, ensure_ascii=False))
            return manifest
        return run(args, client=client, backend_factory=backend_factory)
    except (OSError, ValueError, KeyError, httpx.HTTPError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        # Do not print transport bodies, credentials or potentially sensitive URLs.
        print(f'T07 {type(exc).__name__}: {exc if not isinstance(exc, httpx.HTTPError) else "HTTP operation failed; see local evidence"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    result = main()
    raise SystemExit(result if isinstance(result, int) else 0)
