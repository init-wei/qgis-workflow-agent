"""Offline public graph/HTTP evidence for opt-in raw provider usage."""

import asyncio
import json
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

import httpx
import pytest
from langgraph.graph import StateGraph, START, END

from agent.llm import create_llm
from agent.observability import graph_events


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-canary')
    monkeypatch.setenv('QGIS_AGENT_LANGSMITH_ENABLED', 'false')
    for key in ('LANGSMITH_TRACING', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING_V2',
                'LANGCHAIN_TRACING', 'LANGCHAIN_HANDLER'):
        monkeypatch.setenv(key, 'false')
    monkeypatch.delenv('QGIS_AGENT_EVAL_NONSTREAMING', raising=False)
    monkeypatch.delenv('QGIS_AGENT_EVAL_USAGE_PATH', raising=False)


@pytest.fixture
def journal(monkeypatch):
    import tempfile
    import shutil
    root = Path(__file__).resolve().parents[1] / '.target' / 't07'
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='offline-usage-', dir=root))
    path = directory / 'usage.jsonl'
    monkeypatch.setenv('QGIS_AGENT_EVAL_USAGE_PATH', str(path))
    yield path
    shutil.rmtree(directory)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def collect(graph):
    async def run():
        return [event async for event in graph_events(graph, {}, {}, 'session-outer',
                 {'task_id': 'task-outer', 'case_id': 'case-outer', 'planning_revision': 2})]
    return asyncio.run(run())


class Events:
    def __init__(self, events=(), error=None):
        self.events, self.error = events, error

    async def astream_events(self, *args, **kwargs):
        for event in self.events:
            yield event
        if self.error:
            raise self.error


def model_event(kind, identity, **data):
    return {'event': f'on_chat_model_{kind}', 'run_id': identity,
            'metadata': {'langgraph_node': 'planner', 'task_id': 'forged',
                         'ls_model_name': 'requested-model', 'prompt': 'secret-canary'},
            'data': data}


def test_default_sdk_and_double_opt_in(monkeypatch, journal):
    import agent.llm as module
    received = []
    monkeypatch.setattr(module, 'ChatOpenAI', lambda **kwargs: received.append(kwargs))
    create_llm()
    assert 'disable_streaming' not in received[-1]
    collect(Events())
    assert not journal.exists()
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    create_llm()
    assert received[-1]['disable_streaming'] is True
    collect(Events())
    assert [row['event'] for row in rows(journal)] == ['graph_start', 'graph_end']


def test_locked_sdk_nonstream_raw_usage(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    requests = []

    def response(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body['stream'] is False
        return httpx.Response(200, json={'id': 'chatcmpl-provider-real', 'object': 'chat.completion',
            'created': 1, 'model': 'provider-model',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'response-canary'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 7, 'completion_tokens': 0,
                      'prompt_cache_hit_tokens': 2, 'prompt_cache_miss_tokens': 5}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            llm = create_llm({'model': 'requested-model', 'api_base': 'https://offline.invalid'})
            # The public factory only accepts its existing configuration surface.
            llm.http_async_client = client
            import openai
            llm.root_async_client = openai.AsyncOpenAI(api_key='fake-key-canary',
                base_url='https://offline.invalid', http_client=client)
            llm.async_client = llm.root_async_client.chat.completions
            class State(TypedDict):
                answer: str
            async def node(state):
                answer = await llm.ainvoke('prompt-canary')
                assert answer.usage_metadata['total_tokens'] == 7
                return {'answer': answer.content}
            builder = StateGraph(State)
            builder.add_node('planner', node)
            builder.add_edge(START, 'planner')
            builder.add_edge('planner', END)
            return [event async for event in graph_events(builder.compile(), {}, {}, 'session-outer',
                {'task_id': 'task-outer', 'case_id': 'h-load-cn1:A0:r1', 'planning_revision': 2})]
    events = asyncio.run(run())
    assert requests and any(event['event'] == 'on_chat_model_end' for event in events)
    entries = rows(journal)
    assert [row['event'] for row in entries] == ['graph_start', 'start', 'end', 'graph_end']
    end = entries[2]
    assert end['usage'] == {'prompt_tokens': 7, 'completion_tokens': 0, 'total_tokens': None,
        'prompt_cache_hit_tokens': 2, 'prompt_cache_miss_tokens': 5, 'reasoning_tokens': None}
    assert end['usage_missing_fields'] == ['total_tokens', 'reasoning_tokens']
    assert end['usage_source'] == 'provider_response_metadata.token_usage'
    assert end['provider_response_id'] == 'chatcmpl-provider-real'
    assert end['provider_response_model'] == 'provider-model'
    assert end['requested_model'] == 'requested-model'
    assert all(row['task_id'] == 'task-outer' and row['revision'] == 2 for row in entries)
    assert all(row['case_id'] == 'h-load-cn1:A0:r1' for row in entries)
    assert len({row['invocation_id'] for row in entries}) == 1
    assert entries[-1]['model_run_ids'] == [end['run_id']]
    assert entries[-1]['model_start_count'] == 1
    assert entries[-1]['write_error_count'] == entries[-1]['unidentified_model_events'] == 0
    assert journal.stat().st_mode & 0o777 == 0o600
    assert not any(value in journal.read_text() for value in ('prompt-canary', 'response-canary', 'fake-key-canary'))

    # Consume the exact journal produced by the real SDK graph above.
    from scripts.evaluate_t07 import usage_for
    audited = usage_for('task-outer', entries, expected_graph_invocations=1)
    assert audited['journal_integrity_complete'] is True
    assert audited['integrity_errors'] == []
    assert audited['no_model_calls'] is False
    assert audited['calls'] == 1
    assert audited['observed_graph_invocations'] == 1
    assert audited['run_ids'] == [end['run_id']]
    expected = dict(prompt_tokens=7, completion_tokens=0, total_tokens=None,
                    prompt_cache_hit_tokens=2, prompt_cache_miss_tokens=5, reasoning_tokens=None)
    for name, value in expected.items():
        assert audited['fields'][name]['total'] == value
        assert audited['fields'][name]['known_calls'] == (0 if value is None else 1)
        assert audited['fields'][name]['expected_calls'] == 1
    missing_graph = usage_for('task-outer', entries, expected_graph_invocations=2)
    assert missing_graph['journal_integrity_complete'] is False
    assert 'graph_count_mismatch' in missing_graph['integrity_errors']
    assert missing_graph['fields']['prompt_tokens']['total'] is None
    assert missing_graph['fields']['prompt_tokens']['known_partial_sum'] == 7


def test_absent_path_is_disabled(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    monkeypatch.delenv('QGIS_AGENT_EVAL_USAGE_PATH')
    collect(Events())
    assert not journal.exists()


def test_failure_preserves_original_exception_and_unfinished_model(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    identity = str(uuid4())
    original = RuntimeError('secret-canary/path/geometry')
    with pytest.raises(RuntimeError) as caught:
        collect(Events([model_event('start', identity)], original))
    assert caught.value is original
    entries = rows(journal)
    assert [row['event'] for row in entries] == ['graph_start', 'start', 'error', 'graph_error']
    assert entries[-1]['model_run_ids'] == [identity]
    assert entries[-1]['model_start_count'] == 1
    assert 'secret-canary' not in journal.read_text()


@pytest.mark.parametrize('error', [GeneratorExit, asyncio.CancelledError])
def test_close_or_cancel_never_forges_model_end(monkeypatch, journal, error):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    identity = str(uuid4())
    async def run():
        stream = graph_events(Events([model_event('start', identity)], error()), {}, {},
                              'session-outer', {'task_id': 'task-outer'})
        await anext(stream)
        if error is GeneratorExit:
            await stream.aclose()
        else:
            with pytest.raises(asyncio.CancelledError):
                await anext(stream)
    asyncio.run(run())
    assert [row['event'] for row in rows(journal)] == ['graph_start', 'start', 'error', 'graph_error']


def test_malformed_and_duplicate_ids_and_orphan_ends_remain_auditable(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    identity, orphan = str(uuid4()), str(uuid4())
    collect(Events([model_event('start', identity), model_event('start', identity),
                    model_event('end', orphan), model_event('start', None),
                    model_event('end', 'not-a-uuid'), model_event('end', None)]))
    entries = rows(journal)
    assert entries[-1]['model_run_ids'] == sorted([identity, orphan])
    assert entries[-1]['model_start_count'] == 3
    assert entries[-1]['unidentified_model_events'] == 3
    assert sum(row.get('run_id') is None for row in entries if row['event'] in {'start', 'end'}) == 3
    assert sum(row.get('run_id') == identity and row['event'] == 'start' for row in entries) == 2
    assert any(row['event'] == 'error' and row['run_id'] == identity for row in entries)


def test_only_raw_counts_and_real_response_id_are_recorded(monkeypatch, journal):
    from langchain_core.messages import AIMessage
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    identity = str(uuid4())
    output = AIMessage(content='response-canary', id='internal-message-id',
        usage_metadata={'input_tokens': 99, 'output_tokens': 99, 'total_tokens': 198},
        response_metadata={'model_name': 'provider-model', 'token_usage': {
            'prompt_tokens': True, 'completion_tokens': -1, 'total_tokens': 0,
            'prompt_cache_hit_tokens': '0', 'prompt_cache_miss_tokens': 1.0,
            'reasoning_tokens': 999, 'completion_tokens_details': {'reasoning_tokens': 0}}})
    collect(Events([model_event('start', identity), model_event('end', identity, output=output)]))
    end = rows(journal)[2]
    assert end['provider_response_id'] is None
    assert end['usage'] == dict(prompt_tokens=None, completion_tokens=None, total_tokens=0,
        prompt_cache_hit_tokens=None, prompt_cache_miss_tokens=None, reasoning_tokens=0)
    assert 'internal-message-id' not in journal.read_text()


def test_normalized_usage_without_raw_is_missing(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    identity = str(uuid4())
    collect(Events([model_event('start', identity), model_event('end', identity, output={
        'usage_metadata': {'total_tokens': 7}, 'response_metadata': {'token_usage': None}})]))
    end = rows(journal)[2]
    assert end['usage_source'] is None
    assert len(end['usage_missing_fields']) == 6
    assert set(end['usage'].values()) == {None}


def test_write_error_is_fixed_warning_and_visible_in_later_summary(monkeypatch, journal, caplog):
    import agent.eval_usage as module
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    append = module._append
    def intermittent(path, row):
        if row['event'] == 'start':
            raise OSError('fake-key-canary sensitive path')
        append(path, row)
    monkeypatch.setattr(module, '_append', intermittent)
    collect(Events([model_event('start', str(uuid4()))]))
    assert rows(journal)[-1]['write_error_count'] == 1
    assert caplog.messages == ['Evaluation usage journal unavailable; usage evidence may be incomplete.']
    assert 'sensitive' not in journal.read_text()


@pytest.mark.parametrize('escape', ['outside', 'dotdot', 'leaf_symlink', 'parent_symlink', 'hardlink'])
def test_journal_cannot_escape_or_modify_existing_asset(monkeypatch, journal, tmp_path, escape, caplog):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    original = (journal.parent if escape == 'hardlink' else tmp_path) / 'original'
    original.write_text('immutable-original')
    if escape == 'outside':
        destination = original
    elif escape == 'dotdot':
        destination = journal.parent / '..' / '..' / '..' / 'original'
    elif escape == 'leaf_symlink':
        journal.symlink_to(original)
        destination = journal
    elif escape == 'hardlink':
        import os
        os.link(original, journal)
        destination = journal
    else:
        link = journal.parent / 'link'
        link.symlink_to(tmp_path, target_is_directory=True)
        destination = link / 'original'
    monkeypatch.setenv('QGIS_AGENT_EVAL_USAGE_PATH', str(destination))
    assert collect(Events()) == []
    assert original.read_text() == 'immutable-original'
    assert caplog.messages == ['Evaluation usage journal unavailable; usage evidence may be incomplete.'] * 2


def test_canonical_identity_and_safe_fields_ignore_arbitrary_metadata(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    identity = str(uuid4())
    event = model_event('end', identity, output={'response_metadata': {
        'model_name': 'fake-key-canary', 'id': 'https://private.example',
        'finish_reason': '/private/path', 'token_usage': {'prompt_tokens': 0}}})
    event['metadata'].update(task_id='spoof', case_id='spoof', revision=900,
                             langgraph_node='secret-fake-key-canary', random='geometry-canary')
    collect(Events([event]))
    end = rows(journal)[1]
    assert end['task_id'] == 'task-outer' and end['case_id'] == 'case-outer' and end['revision'] == 2
    assert end['node'] is end['provider_response_model'] is end['provider_response_id'] is end['finish_reason'] is None
    assert 'fake-key-canary' not in journal.read_text()
    assert 'geometry-canary' not in journal.read_text()


@pytest.mark.parametrize('broken_journal', [False, True])
def test_http_review_keeps_gis_blocked_and_does_not_add_sse_types(monkeypatch, journal, tmp_path, broken_journal):
    from test_t06_workflow import setup_workflow, load_plan, event
    import agent.eval_usage as module
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    if broken_journal:
        def unavailable(*args):
            raise OSError('secret-canary')
        monkeypatch.setattr(module, '_append', unavailable)
    client, source, prompts, gis_calls = setup_workflow(monkeypatch, tmp_path, load_plan)
    response = client.post('/api/chat/stream', json={
        'thread_id': 'eval-http-review', 'message': '加载图层', 'files': ['new-file'],
        'case_id': 'h-load-cn1:A0:r1'})
    plan = event(response, 'human_review_required')['task_plan']
    assert plan['review_status'] == 'pending_review'
    assert gis_calls == []
    assert 'event: graph_start' not in response.text and 'event: graph_end' not in response.text
    assert 'event: start' in response.text
    if not broken_journal:
        entries = rows(journal)
        assert entries[0]['event'] == 'graph_start' and entries[-1]['event'] == 'graph_end'
        assert all(row['task_id'] == plan['task_id'] for row in entries)
        assert all(row['case_id'] == 'h-load-cn1:A0:r1' for row in entries)


def test_real_sdk_model_failure_is_paired_with_graph_error(monkeypatch, journal):
    import openai
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    async def run():
        def response(request):
            assert json.loads(request.content)['stream'] is False
            return httpx.Response(400, json={'error': {'message': 'response-canary',
                'type': 'invalid_request_error', 'code': 'invalid_request'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            llm = create_llm({'model': 'requested-model', 'api_base': 'https://offline.invalid'})
            llm.root_async_client = openai.AsyncOpenAI(api_key='fake-key-canary',
                base_url='https://offline.invalid', http_client=client)
            llm.async_client = llm.root_async_client.chat.completions
            class State(TypedDict):
                answer: str
            async def node(state):
                await llm.ainvoke('prompt-canary')
                return {}
            builder = StateGraph(State)
            builder.add_node('planner', node)
            builder.add_edge(START, 'planner')
            builder.add_edge('planner', END)
            with pytest.raises(openai.BadRequestError):
                async for _ in graph_events(builder.compile(), {}, {}, 'session-outer', {'task_id': 'task-outer'}):
                    pass
    asyncio.run(run())
    entries = rows(journal)
    assert [row['event'] for row in entries] == ['graph_start', 'start', 'error', 'graph_error']
    assert entries[1]['run_id'] == entries[2]['run_id']
    assert entries[-1]['model_run_ids'] == [entries[1]['run_id']]
    assert not any(canary in journal.read_text() for canary in ('response-canary', 'prompt-canary', 'fake-key-canary'))


def test_uuid_shaped_secret_is_not_a_model_identity(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    secret = str(uuid4())
    monkeypatch.setenv('OPENAI_API_KEY', secret)
    collect(Events([model_event('start', secret), model_event('error', secret)]))
    assert rows(journal)[-1]['unidentified_model_events'] == 2
    assert secret not in journal.read_text()


def test_unidentified_unfinished_model_keeps_error_on_graph_failure(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    with pytest.raises(RuntimeError):
        collect(Events([model_event('start', None)], RuntimeError('private-canary')))
    entries = rows(journal)
    assert [row['event'] for row in entries] == ['graph_start', 'start', 'error', 'graph_error']
    assert entries[1]['run_id'] is entries[2]['run_id'] is None
    assert entries[-1]['unidentified_model_events'] == 1


def test_each_graph_has_independent_identity_and_timestamps(monkeypatch, journal):
    from datetime import datetime, timezone
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    collect(Events())
    collect(Events())
    entries = rows(journal)
    assert [row['event'] for row in entries] == ['graph_start', 'graph_end'] * 2
    assert entries[0]['invocation_id'] == entries[1]['invocation_id']
    assert entries[2]['invocation_id'] == entries[3]['invocation_id']
    assert entries[0]['invocation_id'] != entries[2]['invocation_id']
    for entry in entries:
        assert entry['schema_version'] == 1
        assert datetime.fromisoformat(entry['timestamp_utc']).tzinfo == timezone.utc
        assert entry['timestamp_utc'].endswith('+00:00')
    assert all(before['monotonic_ns'] <= after['monotonic_ns'] for before, after in zip(entries, entries[1:]))
    assert entries[1]['model_run_ids'] == [] and entries[1]['model_start_count'] == 0


def test_runner_slot_case_identity_is_preserved_by_public_graph_events(monkeypatch, journal):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    async def run():
        return [event async for event in graph_events(Events(), {}, {}, 'session-outer',
                {'task_id': 'task-outer', 'case_id': 'h-load-cn1:A0:r1', 'planning_revision': 2})]
    assert asyncio.run(run()) == []
    assert all(row['case_id'] == 'h-load-cn1:A0:r1' for row in rows(journal))


@pytest.mark.parametrize('case_id', ['https://private.example', '/private/path',
    'C:\\private\\path', 'slot:fake-key-canary:r1', '../outside', 'x' * 129])
def test_case_identity_still_rejects_secret_path_url_and_unbounded_values(monkeypatch, journal, case_id):
    monkeypatch.setenv('QGIS_AGENT_EVAL_NONSTREAMING', 'true')
    async def run():
        return [event async for event in graph_events(Events(), {}, {}, 'session-outer',
                {'task_id': 'task-outer', 'case_id': case_id, 'planning_revision': 2})]
    assert asyncio.run(run()) == []
    assert all(row['case_id'] is None for row in rows(journal))
    assert case_id not in journal.read_text()
