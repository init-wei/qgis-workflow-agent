import asyncio
import logging
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

import agent.tools.retrieval as retrieval_module
from agent.tools.retrieval import (
    ApiDocRecord,
    DefaultApiDocRetriever,
    RetrievalContext,
    RetrievalQueryBuilder,
)
from agent.tools.tool_contracts import ToolCallPlan, ToolObservation


def test_api_doc_record_and_retrieval_context_are_json_serializable():
    record = ApiDocRecord(
        api_name="osgeo.gdal.Warp",
        library="GDAL",
        content="Warp raster data",
        source="rag_docs",
        metadata={"params": "srcDSOrSrcDSTab, destNameOrDestDS"},
    )
    context = RetrievalContext(
        step_id=1,
        tool_name="execute_processing",
        query="osgeo.gdal.Warp execute_processing",
        query_source="initial",
        candidate_apis=["osgeo.gdal.Warp"],
        records=[record],
        metadata={"argument_names": ["INPUT", "OUTPUT"]},
    )

    dumped = context.model_dump_json()

    assert "osgeo.gdal.Warp" in dumped
    assert context.records[0].metadata["params"] == (
        "srcDSOrSrcDSTab, destNameOrDestDS"
    )


def test_query_builder_uses_candidate_apis_tool_name_and_argument_names():
    call = ToolCallPlan(
        step_id=2,
        tool_name="execute_processing",
        arguments={
            "algorithm": "gdal:warpreproject",
            "parameters": {"INPUT": "in.tif", "OUTPUT": "out.tif"},
        },
        metadata={
            "candidate_gdal_apis": ["osgeo.gdal.Warp"],
            "candidate_pyqgis_apis": ["QgsRasterLayer"],
        },
    )

    context = RetrievalQueryBuilder().build_initial_context(call)

    assert context.step_id == 2
    assert context.tool_name == "execute_processing"
    assert context.query_source == "initial"
    assert "osgeo.gdal.Warp" in context.query
    assert "QgsRasterLayer" in context.query
    assert "execute_processing" in context.query
    assert context.candidate_apis == ["osgeo.gdal.Warp", "QgsRasterLayer"]
    assert context.metadata["argument_names"] == [
        "algorithm",
        "parameters",
        "INPUT",
        "OUTPUT",
    ]


def test_query_builder_includes_error_context_for_failed_observation():
    call = ToolCallPlan(
        step_id=3,
        tool_name="add_layer",
        arguments={"path": "missing.gpkg"},
        metadata={"candidate_pyqgis_apis": ["QgsVectorLayer"]},
    )
    observation = ToolObservation(
        tool_name="add_layer",
        status="error",
        error_type="missing_file",
        message="Layer is not valid: missing.gpkg",
    )

    context = RetrievalQueryBuilder().build_error_context(call, observation)

    assert context.query_source == "error"
    assert "add_layer" in context.query
    assert "missing_file" in context.query
    assert "Layer is not valid" in context.query
    assert "path" in context.query
    assert context.metadata["error_type"] == "missing_file"


def test_default_retriever_offloads_gdal_lookup_to_thread(monkeypatch):
    calls = []

    def fake_search_gdal_docs(api_names):
        return [
            {
                "api_name": api_names[0],
                "description": "Warp raster",
                "params": "src, dst",
                "example_code": "",
                "library": "gdal",
            }
        ]

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setitem(
        sys.modules,
        "agent.tools.rag",
        SimpleNamespace(
            search_gdal_docs=fake_search_gdal_docs,
            search_pyqgis_docs_async=None,
        ),
    )
    monkeypatch.setattr(retrieval_module.asyncio, "to_thread", fake_to_thread)
    context = RetrievalContext(
        step_id=4,
        tool_name="execute_processing",
        query="osgeo.gdal.Warp",
        query_source="initial",
        candidate_apis=["osgeo.gdal.Warp"],
    )

    result = retrieval_module.asyncio.run(DefaultApiDocRetriever().retrieve(context))

    assert calls == [(fake_search_gdal_docs, (["osgeo.gdal.Warp"],), {})]
    assert result.records[0].api_name == "osgeo.gdal.Warp"


class _FakePyqgisClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.get_client_calls = 0

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return self.response


def _install_pyqgis_client(monkeypatch, response=None, error=None):
    import agent.tools.rag as rag

    client = _FakePyqgisClient(response=response, error=error)

    async def get_fake_client():
        client.get_client_calls += 1
        return client

    monkeypatch.setattr(rag, "get_mcp_client", get_fake_client)
    return rag, client


@pytest.mark.parametrize("entry", ["async", "sync", "retriever"])
def test_pyqgis_transport_failure_is_logged_and_propagated(monkeypatch, caplog, entry):
    rag, client = _install_pyqgis_client(
        monkeypatch, error=ConnectionError("FAKE_MCP_UNAVAILABLE")
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ConnectionError, match="FAKE_MCP_UNAVAILABLE"):
            if entry == "async":
                asyncio.run(rag.search_pyqgis_docs_async(["QgsVectorLayer"]))
            elif entry == "sync":
                rag.search_pyqgis_docs(["QgsVectorLayer"])
            else:
                context = RetrievalContext(
                    step_id=1, tool_name="add_layer", query="QgsVectorLayer",
                    query_source="initial", candidate_apis=["QgsVectorLayer"],
                )
                asyncio.run(DefaultApiDocRetriever().retrieve(context))
    assert "FAKE_MCP_UNAVAILABLE" in caplog.text
    assert client.calls == [("batch_extract_qgis_methods", {"method_names": ["QgsVectorLayer"]})]


@pytest.mark.parametrize("response", [
    None,
    {},
    {"results": None},
    {"results": {}},
    {"results": [{}]},
    {"results": ["not a document"]},
    {"content": [{"type": "text", "text": "not JSON"}]},
    {"isError": True, "content": [{"type": "text", "text": '{"results": []}'}]},
    {"isError": "true", "results": []},
    {"is_error": [], "results": []},
    {"status": "error", "results": []},
    {"structuredContent": {"isError": True, "results": []}},
    {"content": '{"error": "backend failed", "results": []}'},
    ToolMessage(content='{"results": []}', tool_call_id="x", status="error"),
    ToolMessage(content="", tool_call_id="x", artifact={"isError": True, "structured_content": {"results": []}}),
])
def test_pyqgis_invalid_or_error_response_is_not_an_empty_match(monkeypatch, caplog, response):
    rag, _ = _install_pyqgis_client(monkeypatch, response=response)
    with caplog.at_level(logging.ERROR):
        with pytest.raises((RuntimeError, ValueError)):
            asyncio.run(rag.search_pyqgis_docs_async(["QgsVectorLayer"]))
    assert caplog.records


@pytest.mark.parametrize("response", [
    {"results": []},
    {"isError": False, "results": []},
    {"is_error": None, "results": []},
    '{"results": []}',
    {"content": [{"type": "text", "text": '{"results": []}'}]},
    ToolMessage(content="", tool_call_id="x", artifact={"structured_content": {"results": []}}),
])
def test_pyqgis_successful_empty_response_stays_empty(monkeypatch, response):
    rag, client = _install_pyqgis_client(monkeypatch, response=response)
    assert asyncio.run(rag.search_pyqgis_docs_async(["QgsVectorLayer"])) == []
    assert len(client.calls) == 1


def test_default_retriever_preserves_successful_pyqgis_document(monkeypatch):
    _, client = _install_pyqgis_client(
        monkeypatch, response={"results": [{"class_name": "QgsVectorLayer", "content": "Layer docs"}]}
    )
    context = RetrievalContext(
        step_id=1, tool_name="add_layer", query="QgsVectorLayer",
        query_source="initial", candidate_apis=["QgsVectorLayer"],
    )
    result = asyncio.run(DefaultApiDocRetriever().retrieve(context))
    assert len(client.calls) == 1
    assert result.records[0].api_name == "QgsVectorLayer"
    assert result.records[0].content == "Layer docs"


def test_default_retriever_keeps_successful_empty_pyqgis_match(monkeypatch):
    _, client = _install_pyqgis_client(monkeypatch, response={"results": []})
    context = RetrievalContext(
        step_id=1, tool_name="add_layer", query="QgsVectorLayer",
        query_source="initial", candidate_apis=["QgsVectorLayer"],
    )
    result = asyncio.run(DefaultApiDocRetriever().retrieve(context))
    assert result.records == []
    assert len(client.calls) == 1


def test_pyqgis_client_creation_failure_is_logged_and_propagated(monkeypatch, caplog):
    import agent.tools.rag as rag

    async def unavailable_client():
        raise ConnectionError("FAKE_CLIENT_SETUP_FAILURE")

    monkeypatch.setattr(rag, "get_mcp_client", unavailable_client)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ConnectionError, match="FAKE_CLIENT_SETUP_FAILURE"):
            asyncio.run(rag.search_pyqgis_docs_async(["QgsVectorLayer"]))
    assert "FAKE_CLIENT_SETUP_FAILURE" in caplog.text


def test_empty_pyqgis_query_does_not_construct_a_client(monkeypatch):
    rag, client = _install_pyqgis_client(monkeypatch)
    assert rag.search_pyqgis_docs([]) == []
    assert asyncio.run(rag.search_pyqgis_docs_async([])) == []
    assert client.calls == [] and client.get_client_calls == 0
