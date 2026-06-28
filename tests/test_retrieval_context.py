import sys
from types import SimpleNamespace

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
