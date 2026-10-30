"""Exercise production claim SQL against isolated PostgreSQL temporary tables."""

from contextlib import contextmanager
import os

import psycopg
import pytest

from agent.tools import database, execution_guard


pytestmark = pytest.mark.database


def _connect_project_database():
    """Keep explicit database checks away from libpq's host/service defaults."""
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("Set DATABASE_URL for the dedicated project database; do not use libpq defaults.")
    options = psycopg.conninfo.conninfo_to_dict(dsn)
    database_name = os.getenv("POSTGRES_DB", "qgis_workflow")
    if (options.get("host") not in {"127.0.0.1", "localhost"}
            or options.get("port") != "55433"
            or options.get("hostaddr") not in {None, "127.0.0.1"}
            or options.get("dbname") != database_name
            or options.get("service") or os.getenv("PGSERVICE") or os.getenv("PGSERVICEFILE")):
        pytest.fail("Persistence tests require an explicit project database on loopback port 55433, without a service override.")
    # Explicit kwargs override PGHOSTADDR/PGHOST/PGPORT/PGDATABASE as well as DSN
    # defaults. A hostname must never redirect these tests away from loopback.
    return psycopg.connect(dsn, host="127.0.0.1", hostaddr="127.0.0.1", port="55433", dbname=database_name)


@pytest.fixture
def task_store(monkeypatch):
    # Temporary tables shadow the application's names and disappear on close.
    # No application task, attempt or checkpoint is modified by these cases.
    conn = _connect_project_database()
    conn.execute("CREATE TEMP TABLE task_executions (LIKE public.task_executions INCLUDING ALL)")
    conn.execute("ALTER TABLE task_executions ADD COLUMN IF NOT EXISTS reconciliation JSONB")
    conn.execute("CREATE TEMP TABLE task_step_attempts (LIKE public.task_step_attempts INCLUDING ALL)")
    conn.commit()

    class TemporaryPool:
        @contextmanager
        def connection(self):
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

    monkeypatch.setattr(database, "_pool", TemporaryPool())
    monkeypatch.setattr(execution_guard, "_unknown_tasks", set())
    yield database
    conn.close()


def record(task_id="pg-claim", **updates):
    value = dict(task_id=task_id, session_id="pg-session", plan_revision=1,
                 status="approved", plan={"task_id": task_id, "revision": 1}, attempts=[])
    value.update(updates)
    return value


def test_matching_approval_claims_once_and_orphan_query_needs_attention(task_store):
    task_store.save_task_execution_record(**record())
    with execution_guard.ExecutionSlot("pg-claim").activate():
        task_store.save_task_execution_record(**record(status="running", claim_execution=True))
        assert task_store.get_task_execution_record("pg-claim")["execution"]["status"] == "running"

    recovered = task_store.get_task_execution_record("pg-claim")
    assert recovered["execution"]["status"] == "needs_attention"
    with pytest.raises(RuntimeError, match="needs attention"):
        task_store.save_task_execution_record(**record(status="running", claim_execution=True))


def test_pause_fact_without_checkpoint_proof_cannot_claim(task_store):
    task_store.save_task_execution_record(**record(status="needs_review", resume_state={
        "cursor": 1, "trace_id": "tr", "review_id": "risk", "attempt_ids": [],
    }))

    with pytest.raises(RuntimeError, match="checkpoint.*disagree"):
        task_store.save_task_execution_record(**record(status="running", claim_execution=True))

    assert task_store.get_task_execution_record("pg-claim")["execution"]["status"] == "needs_attention"


def pause_attempt():
    return {
        "attempt_id": "step-1", "step_id": 1, "status": "success",
        "tool_call": {"tool_name": "add_layer", "arguments": {"path": "original.geojson"}},
        "observation": {"status": "success", "data": {"id": "layer-1"}, "outcome": "completed"},
        "artifacts": [], "started_at": "2026-10-30T00:00:00+00:00",
        "finished_at": "2026-10-30T00:00:01+00:00", "duration_ms": 1000,
    }


def test_matching_checkpoint_pause_and_actual_attempt_facts_may_continue(task_store):
    proof = {"cursor": 1, "trace_id": "tr", "review_id": "risk", "attempt_ids": ["step-1"]}
    task_store.save_task_execution_record(**record(status="needs_review", attempts=[pause_attempt()], resume_state=proof))

    with execution_guard.ExecutionSlot("pg-claim").activate():
        task_store.save_task_execution_record(**record(
            status="running", attempts=[pause_attempt()], checkpoint_resume=proof, claim_execution=True,
        ))
        result = task_store.get_task_execution_record("pg-claim")
        assert result["execution"]["status"] == "running"
        assert [item["attempt_id"] for item in result["attempts"]] == ["step-1"]


@pytest.mark.parametrize("mutation", ["arguments", "observation"])
def test_equal_pause_proof_cannot_hide_changed_attempt_facts(task_store, mutation):
    proof = {"cursor": 1, "trace_id": "tr", "review_id": "risk", "attempt_ids": ["step-1"]}
    task_store.save_task_execution_record(**record(status="needs_review", attempts=[pause_attempt()], resume_state=proof))
    changed = pause_attempt()
    if mutation == "arguments":
        changed["tool_call"]["arguments"]["path"] = "different.geojson"
    else:
        changed["observation"]["data"]["id"] = "different-layer"

    with pytest.raises(RuntimeError, match="attempts disagree"):
        task_store.save_task_execution_record(**record(
            status="running", attempts=[changed], checkpoint_resume=proof, claim_execution=True,
        ))

    result = task_store.get_task_execution_record("pg-claim")
    assert result["execution"]["status"] == "needs_attention"
    assert result["attempts"][0]["tool_arguments"] == {"path": "original.geojson"}
    assert result["attempts"][0]["observation"]["data"] == {"id": "layer-1"}


def test_reconciliation_keeps_unknown_call_and_attempts_without_authorizing_replay(task_store):
    attempt = pause_attempt()
    attempt['status'] = 'failed'
    attempt['observation']['outcome'] = 'unknown'
    intent = {'tool_name': 'add_layer', 'arguments': {'path': 'original.geojson'}}
    task_store.save_task_execution_record(**record(status='needs_attention', attempts=[attempt], inflight_call=intent))
    evidence = {'note': 'Inspected QGIS and the layer; the operation is no longer active.',
                'qgis': {'process_id': 42, 'instance_id': 'instance-1', 'profile_folder': '/test/profile'}}
    assert not task_store.reconcile_task_execution('pg-claim', 'other-session', evidence)
    assert task_store.reconcile_task_execution('pg-claim', 'pg-session', evidence)
    assert not task_store.reconcile_task_execution('pg-claim', 'pg-session', evidence)
    result = task_store.get_task_execution_record('pg-claim')
    assert result['execution']['status'] == 'reconciled'
    assert result['execution']['inflight_call'] == intent
    assert result['execution']['reconciliation'] == evidence
    assert result['attempts'][0]['observation']['outcome'] == 'unknown'
    with pytest.raises(RuntimeError, match='needs attention'):
        task_store.save_task_execution_record(**record(status='running', claim_execution=True))


def test_result_confirmation_rejects_conflicting_decision_after_checkpoint_failure(task_store):
    task_store.save_task_execution_record(**record(status='success'))
    assert task_store.confirm_task_execution('pg-claim', False, 'Layer is missing')
    assert task_store.confirm_task_execution('pg-claim', False, 'Layer is missing')
    assert not task_store.confirm_task_execution('pg-claim', True)
    assert not task_store.confirm_task_execution('pg-claim', False, 'Different observation')
    result = task_store.get_task_execution_record('pg-claim')['execution']
    assert result['confirmed'] is False
    assert result['summary'] == 'Layer is missing'
