"""RAG imports and configuration must never implicitly connect to PostgreSQL."""

import importlib
import logging
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]

# The fake is installed before either module is imported. No real pool or socket
# is created, even when the configuration is deliberately non-empty.
_ISOLATED_PROBE = r'''
import importlib
import os
import sys
import types

calls = []
class Cursor:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, query, args): pass
    def fetchall(self): return [{"api_name": "osgeo.gdal.Warp"}]
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self, **kwargs): return Cursor()
class Pool:
    def __init__(self, **kwargs): calls.append(kwargs)
    def connection(self): return Connection()
module = types.ModuleType("psycopg_pool")
module.ConnectionPool = Pool
sys.modules["psycopg_pool"] = module

rag = importlib.import_module(sys.argv[1])
assert calls == [], "import constructed a pool"
assert not hasattr(rag, "search_cookbook")
if sys.argv[1] == "qgis_mcp_server.rag_mcp":
    import asyncio
    names = {tool.name for tool in asyncio.run(rag.mcp.list_tools())}
    assert {"search_gdal_api", "search_pyqgis_api"} <= names
    assert "search_qgis_cookbook" not in names
else:
    from agent.tools import database
    assert all(not hasattr(database, name) for name in
        ("save_cookbook_entry", "update_cookbook_usage", "get_cookbook_by_id"))

searches = [(rag.search_gdal_docs, ["osgeo.gdal.Warp"])]
if sys.argv[1] == "agent.tools.rag":
    searches.extend([(rag.search_gdal_docs_fuzzy, "Warp"),
                     (rag.search_all_apis_by_keywords, ["Warp"])])
if sys.argv[2] == "missing":
    for search, query in searches:
        try:
            search(query)
        except RuntimeError as exc:
            assert "DATABASE_URL" in str(exc)
        else:
            raise AssertionError("missing configuration must be reported")
    assert calls == [], "missing configuration constructed a pool"
else:
    for search, query in searches:
        assert search(query) == [{"api_name": "osgeo.gdal.Warp"}]
    assert len(calls) == 1
    assert calls[0]["conninfo"] == os.environ["DATABASE_URL"]
    assert searches[0][0](searches[0][1]) == [{"api_name": "osgeo.gdal.Warp"}]
    assert len(calls) == 1, "same process should reuse the lazy pool"
print("probe passed")
'''

_DIRECT_PROBE = r'''
import pathlib
import runpy
import sys
import types
import uvicorn

root = pathlib.Path.cwd().resolve()
script = root / "qgis_mcp_server" / "rag_mcp.py"
sys.path[:] = [str(script.parent)] + [item for item in sys.path
    if item and pathlib.Path(item).resolve() != root]
calls = []
class Pool:
    def __init__(self, **kwargs): calls.append(kwargs)
module = types.ModuleType("psycopg_pool")
module.ConnectionPool = Pool
sys.modules["psycopg_pool"] = module
uvicorn.run = lambda *args, **kwargs: None
runpy.run_path(str(script), run_name="__main__")
assert calls == [], "direct entry constructed a pool"
print("direct probe passed")
'''

_NESTED_PYQGIS_PROBE = r'''
import asyncio
import sys
import types

class Pool:
    def __init__(self, **kwargs):
        raise AssertionError("unexpected database pool")
module = types.ModuleType("psycopg_pool")
module.ConnectionPool = Pool
sys.modules["psycopg_pool"] = module
import agent.tools.rag as rag

class Client:
    error = None
    async def call_tool(self, name, arguments):
        if self.error is not None:
            raise self.error
        return {"results": []}
client = Client()
async def get_fake_client(): return client
rag.get_mcp_client = get_fake_client

async def main():
    assert rag.search_pyqgis_docs(["QgsVectorLayer"]) == []
    client.error = ConnectionError("FAKE_NESTED_FAILURE")
    try:
        rag.search_pyqgis_docs(["QgsVectorLayer"])
    except ConnectionError as exc:
        assert "FAKE_NESTED_FAILURE" in str(exc)
    else:
        raise AssertionError("nested synchronous call swallowed the MCP error")
asyncio.run(main())
print("nested probe passed")
'''


def _run_probe(source, *args, configured=None):
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    if configured is not None:
        env["DATABASE_URL"] = configured
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-c", source, *args], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=20, check=False,
    )


@pytest.mark.parametrize("module", ["agent.tools.rag", "qgis_mcp_server.rag_mcp"])
@pytest.mark.parametrize("database_url", [None, ""], ids=["unset", "empty"])
def test_import_and_missing_configuration_never_construct_pool(module, database_url):
    result = _run_probe(_ISOLATED_PROBE, module, "missing", configured=database_url)
    assert result.returncode == 0, result.stderr
    assert "probe passed" in result.stdout


@pytest.mark.parametrize("module", ["agent.tools.rag", "qgis_mcp_server.rag_mcp"])
def test_configured_search_constructs_and_reuses_pool_only_on_call(module):
    result = _run_probe(_ISOLATED_PROBE, module, "configured",
                        configured="postgresql://isolated.invalid/test")
    assert result.returncode == 0, result.stderr
    assert "probe passed" in result.stdout


def test_direct_rag_mcp_script_import_is_lazy_without_connecting():
    result = _run_probe(_DIRECT_PROBE)
    assert result.returncode == 0, result.stderr
    assert "direct probe passed" in result.stdout


def test_sync_pyqgis_lookup_inside_event_loop_keeps_success_and_failure_semantics():
    result = _run_probe(_NESTED_PYQGIS_PROBE)
    assert result.returncode == 0, result.stderr
    assert "nested probe passed" in result.stdout


@pytest.mark.parametrize("module,search_name,query", [
    ("agent.tools.rag", "search_gdal_docs", ["osgeo.gdal.Warp"]),
    ("agent.tools.rag", "search_gdal_docs_fuzzy", "Warp"),
    ("agent.tools.rag", "search_all_apis_by_keywords", ["Warp"]),
    ("qgis_mcp_server.rag_mcp", "search_gdal_docs", ["osgeo.gdal.Warp"]),
])
def test_query_failure_is_logged_and_does_not_look_like_no_results(
    monkeypatch, caplog, module, search_name, query,
):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    rag = importlib.import_module(module)

    class FailingPool:
        def connection(self):
            raise RuntimeError("simulated document lookup failure")

    monkeypatch.setattr(rag, "_get_pool", lambda: FailingPool())
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="simulated document lookup failure"):
            getattr(rag, search_name)(query)
    assert "simulated document lookup failure" in caplog.text
