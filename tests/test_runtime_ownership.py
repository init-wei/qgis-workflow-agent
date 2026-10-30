"""HTTP query projection with the actual business query and process owner rules."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from agent.tools import database, execution_guard
import ui.backend.main as backend


@pytest.fixture
def durable_running_task(monkeypatch):
    execution = {'task_id': 'orphan', 'session_id': 'thread', 'plan_revision': 1,
                 'status': 'running', 'plan': {}, 'confirmed': None, 'summary': None,
                 'inflight_call': {'tool_name': 'add_layer'}, 'created_at': None, 'updated_at': None}
    commands = []

    class Cursor:
        def execute(self, query, params):
            commands.append(query)
            if 'UPDATE task_executions' in query:
                execution['status'] = 'needs_attention'

        def fetchone(self):
            return dict(execution)

        def fetchall(self):
            return []

    class Connection:
        @contextmanager
        def cursor(self, **kwargs):
            yield Cursor()

        def commit(self):
            pass

    class Pool:
        @contextmanager
        def connection(self):
            yield Connection()

    monkeypatch.setattr(database, '_get_pool', Pool)
    execution_guard._unknown_tasks.clear()
    yield execution, commands
    execution_guard._unknown_tasks.clear()


def test_first_query_after_restart_marks_orphaned_inflight_and_blocks_new_gis(durable_running_task):
    execution, commands = durable_running_task
    response = TestClient(backend.app).get('/api/executions/orphan')
    result = response.json()['execution']
    assert result['status'] == 'needs_attention'
    assert result['inflight_call'] == {'tool_name': 'add_layer'}
    assert execution['status'] == 'needs_attention'
    assert 'FOR UPDATE' in commands[0]
    with pytest.raises(execution_guard.ExecutionBusy, match='needs attention'):
        execution_guard.ExecutionSlot('different-task')


def test_query_of_live_owner_keeps_running_until_its_operation_finishes(durable_running_task):
    execution, commands = durable_running_task
    with execution_guard.ExecutionSlot('orphan').activate():
        result = TestClient(backend.app).get('/api/executions/orphan').json()['execution']
        assert result['status'] == 'running'
        assert execution['status'] == 'running'
        assert not any('UPDATE task_executions' in query for query in commands)
    result = TestClient(backend.app).get('/api/executions/orphan').json()['execution']
    assert result['status'] == 'needs_attention'
