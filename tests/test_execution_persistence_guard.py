"""Connection-boundary checks only: no PostgreSQL connection is made."""

import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "_persistence_fixture_guard", Path(__file__).with_name("test_execution_persistence.py"),
)
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


@pytest.fixture
def captured_connections(monkeypatch):
    for name in ("DATABASE_URL", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POSTGRES_DB", "qgis_workflow")
    calls = []
    monkeypatch.setattr(fixture_module.psycopg, "connect", lambda *args, **kwargs: calls.append((args, kwargs)))
    return calls


def test_empty_project_dsn_never_contacts_default_postgres(monkeypatch, captured_connections):
    monkeypatch.setenv("DATABASE_URL", " ")
    with pytest.raises(pytest.skip.Exception):
        fixture_module._connect_project_database()
    assert captured_connections == []


@pytest.mark.parametrize("dsn,environment", [
    ("host=localhost hostaddr=192.0.2.1 port=55433 dbname=qgis_workflow", {}),
    ("host=192.0.2.1 port=55433 dbname=qgis_workflow", {}),
    ("host=localhost port=5432 dbname=qgis_workflow", {}),
    ("host=localhost port=55433 dbname=other_project", {}),
    ("host=localhost port=55433 dbname=qgis_workflow service=other", {}),
    ("host=localhost port=55433 dbname=qgis_workflow", {"PGSERVICE": "other"}),
    ("host=localhost port=55433 dbname=qgis_workflow", {"PGSERVICEFILE": "/tmp/unrelated-service"}),
])
def test_unrelated_connection_target_is_rejected_before_connect(monkeypatch, captured_connections, dsn, environment):
    monkeypatch.setenv("DATABASE_URL", dsn)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(pytest.fail.Exception):
        fixture_module._connect_project_database()
    assert captured_connections == []


def test_connection_uses_explicit_loopback_even_with_libpq_environment(monkeypatch, captured_connections):
    monkeypatch.setenv("DATABASE_URL", "host=localhost port=55433 dbname=qgis_workflow")
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("PGHOST", "unrelated.example")
    monkeypatch.setenv("PGPORT", "5432")
    monkeypatch.setenv("PGDATABASE", "other_project")
    fixture_module._connect_project_database()
    assert len(captured_connections) == 1
    assert captured_connections[0][1] == {
        "host": "127.0.0.1", "hostaddr": "127.0.0.1", "port": "55433", "dbname": "qgis_workflow",
    }
