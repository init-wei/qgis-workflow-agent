"""Optional tracing at the HTTP workflow and external SDK/GIS boundaries."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import planner_node as planner_module
import ui.backend.main as backend
from test_backend_workflow import execution_app, selected_file


@pytest.fixture(autouse=True)
def close_trace_client():
    yield
    import asyncio
    from agent.observability import close_langsmith
    asyncio.run(close_langsmith())


def event(response, name):
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if lines and lines[0] == f"event: {name}":
            return json.loads(lines[1].removeprefix("data: "))
    raise AssertionError(f"{name} not found in {response.text}")


class Model:
    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps({
            "task_id": "model-id", "task_summary": "Inspect", "risk_level": "low",
            "review_status": "pending_review", "required_inputs": [], "expected_outputs": [],
            "steps": [{"step_id": 1, "description": "Inspect", "allowed_tools": ["get_layers"],
                       "input_refs": [], "output_refs": [], "risk_level": "low", "requires_review": False}],
            "tool_calls": [{"step_id": 1, "tool_name": "get_layers", "arguments": {}}],
        }))


@pytest.mark.parametrize(("tracing", "api_key"), [("true", None), ("false", "test-key"), (None, "test-key")])
def test_disabled_or_unconfigured_tracing_does_not_create_a_tracer_or_prevent_review(monkeypatch, tracing, api_key):
    import langsmith.client
    from langchain_core.tracers.langchain import LangChainTracer

    if tracing is None:
        monkeypatch.delenv("QGIS_AGENT_LANGSMITH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", tracing)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    if api_key is None:
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LANGSMITH_API_KEY", api_key)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    tracer_attempts = []
    client_attempts = []

    def forbidden_tracer(*args, **kwargs):
        tracer_attempts.append(True)
        raise RuntimeError("an unconfigured tracer must never be created")

    def forbidden_client(*args, **kwargs):
        client_attempts.append(True)
        raise RuntimeError("an unconfigured client must never be created")

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(LangChainTracer, "__init__", forbidden_tracer)
    monkeypatch.setattr(langsmith.client.Client, "__init__", forbidden_client)
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: Model())
    response = TestClient(backend.app).post("/api/chat/stream", json={
        "thread_id": "without-key", "message": "Show current layers",
    })
    assert event(response, "human_review_required")["task_plan"]["revision"] == 1
    assert tracer_attempts == []
    assert client_attempts == []


@pytest.mark.parametrize("sdk_flag", ["LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2",
                                      "LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"])
def test_conflicting_global_sdk_tracing_fails_closed_without_breaking_review(monkeypatch, caplog, sdk_flag):
    import langsmith.client
    import agent.observability as telemetry

    attempts = []
    monkeypatch.setattr(telemetry, "_legacy_conflict", False, raising=False)
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    for name in ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(sdk_flag, "true")

    def forbidden_client(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("conflicting SDK mode must not initialize a client")

    monkeypatch.setattr(langsmith.client.Client, "__init__", forbidden_client)
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: Model())
    client = TestClient(backend.app)
    response = client.post("/api/chat/stream", json={
        "thread_id": "sdk-conflict", "message": "Show current layers",
    })
    assert event(response, "human_review_required")["task_plan"]["revision"] == 1
    second = client.post("/api/chat/stream", json={
        "thread_id": "sdk-conflict-again", "message": "Show current layers",
    })
    assert event(second, "human_review_required")["task_plan"]["revision"] == 1
    assert not attempts
    assert "global" in caplog.text.lower() and sdk_flag in caplog.text


@pytest.mark.parametrize("sdk_mode", ["LANGSMITH_AGENT_ID", "LANGSMITH_AGENT_ENVIRONMENT",
                                      "LANGCHAIN_AGENT_ID", "LANGCHAIN_AGENT_ENVIRONMENT",
                                      "LANGSMITH_RUNS_ENDPOINTS", "LANGSMITH_USE_PYO3_CLIENT",
                                      "LANGCHAIN_RUNS_ENDPOINTS", "LANGCHAIN_USE_PYO3_CLIENT",
                                      "LANGSMITH_OTEL_ENABLED", "LANGCHAIN_OTEL_ENABLED", "OTEL_ENABLED"])
def test_unsupported_sdk_destination_or_export_mode_does_not_start_client(monkeypatch, caplog, sdk_mode):
    import langsmith.client

    attempts = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setenv(sdk_mode, "true")

    def forbidden_client(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("unsupported SDK mode must not initialize a client")

    monkeypatch.setattr(langsmith.client.Client, "__init__", forbidden_client)
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: Model())
    response = TestClient(backend.app).post("/api/chat/stream", json={
        "thread_id": "unsupported-sdk-mode", "message": "Show current layers",
    })
    assert event(response, "human_review_required")["task_plan"]["revision"] == 1
    assert not attempts
    assert "unsupported" in caplog.text.lower() and sdk_mode in caplog.text


@pytest.mark.parametrize("replica_flag", ["LANGSMITH_RUNS_ENDPOINTS", "LANGCHAIN_RUNS_ENDPOINTS"])
def test_replica_endpoint_alias_never_receives_graph_or_gis_spans(monkeypatch, tmp_path, caplog, replica_flag):
    import requests
    import langsmith.utils
    from test_t03_workflow import test_processing_chain_requires_independently_validated_results

    sent = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    monkeypatch.delenv("LANGCHAIN_ENDPOINT", raising=False)
    monkeypatch.delenv("LANGSMITH_RUNS_ENDPOINTS", raising=False)
    monkeypatch.delenv("LANGCHAIN_RUNS_ENDPOINTS", raising=False)
    monkeypatch.setenv(replica_flag, json.dumps({"https://replica.invalid": "fake-replica-key"}))
    # The SDK caches environment lookups, so this fake-process test must read
    # its own isolated environment rather than a previous test's cache.
    langsmith.utils.get_env_var.cache_clear()

    def fake_transport(session, method, url, **kwargs):
        sent.append((method, url))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.url = url
        return response

    async def no_startup_or_database():
        return None

    monkeypatch.setattr(requests.Session, "request", fake_transport)
    monkeypatch.setattr(backend, "preload_sessions_from_db", no_startup_or_database)
    monkeypatch.setattr(backend, "close_checkpointer_pool", no_startup_or_database)
    try:
        with TestClient(backend.app):
            test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, "valid", None, 3)
        assert sent == []
        assert replica_flag in caplog.text
    finally:
        langsmith.utils.get_env_var.cache_clear()


def test_cached_replica_setting_cannot_redirect_a_later_single_target_run(monkeypatch, tmp_path):
    import requests
    import langsmith.utils
    from test_t03_workflow import test_processing_chain_requires_independently_validated_results

    sent = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.delenv("LANGSMITH_RUNS_ENDPOINTS", raising=False)
    monkeypatch.setenv("LANGCHAIN_RUNS_ENDPOINTS", json.dumps({"https://replica.invalid": "fake-replica-key"}))
    langsmith.utils.get_env_var.cache_clear()
    assert langsmith.utils.get_env_var("RUNS_ENDPOINTS")
    monkeypatch.delenv("LANGCHAIN_RUNS_ENDPOINTS")

    def fake_transport(session, method, url, **kwargs):
        sent.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.url = url
        return response

    async def no_startup_or_database():
        return None

    monkeypatch.setattr(requests.Session, "request", fake_transport)
    monkeypatch.setattr(backend, "preload_sessions_from_db", no_startup_or_database)
    monkeypatch.setattr(backend, "close_checkpointer_pool", no_startup_or_database)
    try:
        with TestClient(backend.app):
            test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, "valid", None, 3)
        assert sent and all(url.startswith("https://api.smith.langchain.com/") for url in sent)
    finally:
        langsmith.utils.get_env_var.cache_clear()


def test_enabled_graph_runs_are_linked_across_plan_revisions_without_private_state(monkeypatch):
    import copy
    import langsmith.client

    captured = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    monkeypatch.setattr(langsmith.client.Client, "_update_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: Model())
    client = TestClient(backend.app)
    monkeypatch.setattr(backend, "save_task_execution_record", lambda **kwargs: None)
    response = client.post("/api/chat/stream", json={
        "thread_id": "trace-session", "message": "Show current layers PRIVATE-GEOMETRY-AND-KEY",
        "case_id": "case-42",
    })
    first = event(response, "human_review_required")["task_plan"]
    assert client.post("/api/review", json={
        "thread_id": "trace-session", "task_id": first["task_id"], "revision": 1,
        "approved": False, "advise": "Change the plan",
    }).status_code == 200
    revised = event(client.post("/api/resume/trace-session"), "human_review_required")["task_plan"]
    assert revised["task_id"] == first["task_id"] and revised["revision"] == 2
    metadata = [run.get("extra", {}).get("metadata", {}) for run in captured]
    related = [item for item in metadata if item.get("task_id") == first["task_id"]]
    assert {item.get("revision") for item in related} == {1, 2}
    assert {item.get("session_id") for item in related} == {"trace-session"}
    assert {item.get("case_id") for item in related} == {"case-42"}
    assert "PRIVATE-GEOMETRY-AND-KEY" not in str(captured)
    assert "test-key-never-sent" not in str(captured)


def test_processing_spans_describe_completed_calls_and_validation_without_geometry(monkeypatch, tmp_path):
    import copy
    import langsmith.client
    from test_t03_workflow import test_processing_chain_requires_independently_validated_results

    captured = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    monkeypatch.setattr(langsmith.client.Client, "_update_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, "valid", None, 3)

    starts = [run for run in captured if run.get("name") == "qgis.mcp_call" and not run.get("end_time")]
    validations = [run for run in captured if run.get("name") == "qgis.spatial_validation" and not run.get("end_time")]
    assert len(starts) == 3 and len(validations) == 2
    assert all(run.get("parent_run_id") and run.get("trace_id") for run in starts + validations)
    metadata = [run["extra"]["metadata"] for run in starts]
    reproject = next(item for item in metadata if item.get("algorithm") == "native:reprojectlayer")
    buffer = next(item for item in metadata if item.get("algorithm") == "native:buffer")
    assert reproject["target_crs"] == "EPSG:32650"
    assert buffer["distance"] == 100 and buffer["distance_unit"] == "m"
    finished = [run.get("extra", {}).get("metadata", {}) for run in captured if run.get("end_time")]
    assert any(item.get("status") == "passed" and item.get("check_count") == 1 for item in finished)
    assert any(item.get("check_names") == ["target_crs"] for item in finished)
    assert any(item.get("size_bytes") and len(item.get("sha256", "")) == 64 for item in finished)
    serialized = str(captured)
    assert "450000" not in serialized and "4410000" not in serialized
    assert "test-key-never-sent" not in serialized


def test_risk_review_resumption_keeps_correlation_without_replaying_prior_calls(monkeypatch, tmp_path):
    import copy
    import langsmith.client
    from test_t03_workflow import test_processing_chain_requires_independently_validated_results

    captured = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    monkeypatch.setattr(langsmith.client.Client, "_update_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, "project", None, 4)
    calls = [run for run in captured if run.get("name") == "qgis.mcp_call" and not run.get("end_time")]
    assert [run["extra"]["metadata"]["tool_name"] for run in calls] == [
        "execute_processing", "execute_processing", "add_layer", "save_project",
    ]
    assert len({run["trace_id"] for run in calls[:3]}) == 1
    assert calls[3]["trace_id"] != calls[0]["trace_id"]
    assert len({run["extra"]["metadata"]["task_id"] for run in calls}) == 1
    assert {run["extra"]["metadata"]["revision"] for run in calls} == {1}


@pytest.mark.parametrize("failure_point", ["start", "patch"])
def test_transient_span_failure_does_not_reparent_later_steps(monkeypatch, tmp_path, failure_point):
    import copy
    import langsmith.client
    import langsmith.run_helpers
    from test_t03_workflow import test_processing_chain_requires_independently_validated_results

    captured, failed = [], []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))

    def update(self, run, **kwargs):
        if failure_point == "patch" and run.get("name") == "qgis.mcp_call" and not failed:
            failed.append(True)
            raise RuntimeError("simulated first span patch failure")
        captured.append(copy.deepcopy(run))

    monkeypatch.setattr(langsmith.client.Client, "_update_run", update)
    if failure_point == "start":
        original_enter = langsmith.run_helpers.trace.__enter__

        def broken_enter(context):
            run = original_enter(context)
            if context.name == "qgis.mcp_call" and not failed:
                failed.append(True)
                raise RuntimeError("simulated failure after span setup")
            return run

        monkeypatch.setattr(langsmith.run_helpers.trace, "__enter__", broken_enter)
    test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, "valid", None, 3)
    calls = [run for run in captured if run.get("name") == "qgis.mcp_call" and not run.get("end_time")]
    assert failed and len(calls) == 3
    metadata = [run["extra"]["metadata"] for run in calls]
    assert [item["step_id"] for item in metadata] == [1, 2, 3]
    assert [item["tool_name"] for item in metadata] == ["execute_processing", "execute_processing", "add_layer"]
    assert [item.get("algorithm") for item in metadata] == ["native:reprojectlayer", "native:buffer", None]
    assert len({run["parent_run_id"] for run in calls}) == 1


@pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "nonstreaming"])
@pytest.mark.parametrize(("provider_usage", "expected"), [
    ({"prompt_tokens": 12}, {"input_tokens": 12}),
    ({"prompt_tokens": 12, "completion_tokens": 4}, {"input_tokens": 12, "output_tokens": 4}),
    ({"prompt_tokens": 12, "completion_tokens": 0, "total_tokens": 12},
     {"input_tokens": 12, "output_tokens": 0, "total_tokens": 12}),
    ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
     {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
    (None, None),
], ids=["partial", "missing-total", "explicit-zero", "all-zero", "missing-all"])
def test_only_raw_provider_usage_fields_are_observed(monkeypatch, streaming, provider_usage, expected):
    import asyncio
    import re
    import requests
    from langchain_openai import ChatOpenAI

    sent = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

    def fake_transport(session, method, url, **kwargs):
        sent.append(kwargs.get("data", b""))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.url = url
        return response

    async def no_startup_or_database():
        return None

    monkeypatch.setattr(requests.Session, "request", fake_transport)
    monkeypatch.setattr(backend, "preload_sessions_from_db", no_startup_or_database)
    monkeypatch.setattr(backend, "close_checkpointer_pool", no_startup_or_database)
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    content = asyncio.run(Model().ainvoke([])).content

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def __aiter__(self):
            async def chunks():
                yield {"model": "fake-model", "choices": [{"delta": {"role": "assistant", "content": content},
                                                             "finish_reason": "stop"}]}
                if provider_usage is not None:
                    yield {"choices": [], "usage": provider_usage}
            return chunks()

    class FakeAPI:
        def __init__(self):
            self.with_raw_response = self

        async def create(self, **kwargs):
            if kwargs.get("stream"):
                return FakeStream()
            response = {"model": "fake-model", "choices": [{"message": {"role": "assistant", "content": content},
                                                                "finish_reason": "stop"}], "usage": provider_usage}
            return SimpleNamespace(parse=lambda: response)

    model = ChatOpenAI(model="fake-model", api_key="fake-provider-key", disable_streaming=not streaming)
    model.async_client = FakeAPI()
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: model)
    with TestClient(backend.app) as client:
        event(client.post("/api/chat/stream", json={"thread_id": "usage", "message": "Show layers"}),
              "human_review_required")
    body = b"\n".join(sent)
    reported = [json.loads(item) for item in re.findall(rb'"usage_metadata":({[^{}]*})', body)]
    # The locked ChatOpenAI streaming converter discards the original usage
    # object; its normalized AIMessage can contain invented zeros and totals.
    assert reported == ([] if streaming or expected is None else [expected])


def test_spatial_failure_is_linked_to_local_task_and_is_not_a_successful_span(monkeypatch, tmp_path):
    import copy
    import langsmith.client
    from test_t03_workflow import test_processing_chain_requires_independently_validated_results

    captured = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    monkeypatch.setattr(langsmith.client.Client, "_update_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, "coordinate", "coordinate", 1)
    failed = next(run for run in captured if run.get("name") == "qgis.spatial_validation"
                  and run.get("end_time") and run["extra"]["metadata"].get("status") == "failed")
    assert failed["extra"]["metadata"]["session_id"] == "wrong-coordinate"
    assert failed["extra"]["metadata"]["task_id"]
    assert failed["extra"]["metadata"]["revision"] == 1
    assert "coordinates" in failed["extra"]["metadata"]["error_categories"]
    assert failed["error"]
    assert "Reprojected coordinate differs from expected location" not in str(captured)


@pytest.mark.parametrize("fault", ["setup", "post", "patch", "span"])
def test_tracing_failures_do_not_change_review_recovery_or_repeat_gis_writes(monkeypatch, tmp_path, fault):
    import langsmith.client
    import agent.observability as telemetry
    from test_t03_workflow import test_processing_chain_requires_independently_validated_results

    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)

    def failure(*args, **kwargs):
        raise RuntimeError("simulated tracing failure")

    if fault == "setup":
        monkeypatch.setattr(langsmith.client.Client, "__init__", failure)
    else:
        monkeypatch.setattr(langsmith.client.Client, "_create_run", failure if fault == "post" else lambda *a, **kw: None)
        monkeypatch.setattr(langsmith.client.Client, "_update_run", failure if fault == "patch" else lambda *a, **kw: None)
        if fault == "span":
            monkeypatch.setattr(telemetry, "trace", failure)
    # Existing HTTP test covers the plan review, risk pause, and each write count.
    test_processing_chain_requires_independently_validated_results(monkeypatch, tmp_path, "project", None, 4)


def test_unknown_mcp_outcome_is_correlated_without_exposing_raw_response(monkeypatch, tmp_path):
    import copy
    import langsmith.client
    from agent.tools import execution_guard
    from test_backend_workflow import test_qgis_failure_never_changes_approved_arguments_or_reports_success

    captured = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    monkeypatch.setattr(langsmith.client.Client, "_update_run", lambda self, run, **kw: captured.append(copy.deepcopy(run)))
    execution_guard._unknown_tasks.clear()
    try:
        test_qgis_failure_never_changes_approved_arguments_or_reports_success(
            monkeypatch, tmp_path, {"status": "error", "outcome": "unknown", "message": "private-body-secret"},
            "needs_attention",
        )
        failed = next(run for run in captured if run.get("name") == "qgis.mcp_call"
                      and run.get("end_time") and run["extra"]["metadata"].get("outcome") == "unknown")
        assert failed["extra"]["metadata"]["task_id"] and failed["extra"]["metadata"]["attempt_id"]
        assert failed["extra"]["metadata"]["reason_code"]
        assert failed["error"]
        assert "private-body-secret" not in str(captured)
        assert TestClient(backend.app).post("/api/resume/unknown").status_code == 409
        assert len([run for run in captured if run.get("name") == "qgis.mcp_call"
                    and not run.get("end_time")]) == 1
    finally:
        execution_guard._unknown_tasks.clear()


def test_shutdown_bounds_a_stalled_sdk_flush(monkeypatch):
    import threading
    import time
    import langsmith.client

    release = threading.Event()
    flushed = threading.Event()
    cleaned = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda *a, **kw: None)
    monkeypatch.setattr(langsmith.client.Client, "_update_run", lambda *a, **kw: None)

    def blocked_flush(client):
        flushed.set()
        release.wait(10)

    async def no_startup_or_database():
        return None

    monkeypatch.setattr(langsmith.client.Client, "flush", blocked_flush)
    monkeypatch.setattr(langsmith.client.Client, "cleanup", lambda client: cleaned.append(True))
    monkeypatch.setattr(backend, "preload_sessions_from_db", no_startup_or_database)
    monkeypatch.setattr(backend, "close_checkpointer_pool", no_startup_or_database)
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: Model())
    try:
        with TestClient(backend.app) as client:
            event(client.post("/api/chat/stream", json={"thread_id": "bounded-flush", "message": "Show layers"}),
                  "human_review_required")
            start = time.monotonic()
        elapsed = time.monotonic() - start
        assert flushed.is_set() and cleaned == [True]
        assert 1.8 <= elapsed < 5.0
    finally:
        release.set()


def test_observability_failures_keep_disconnected_producer_ownership(request, monkeypatch):
    import langsmith.client
    from test_backend_workflow import test_disconnected_listener_does_not_cancel_gis_or_admit_second_task

    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)

    def failure(*args, **kwargs):
        raise RuntimeError("simulated tracing failure")

    monkeypatch.setattr(langsmith.client.Client, "_create_run", failure)
    monkeypatch.setattr(langsmith.client.Client, "_update_run", failure)
    test_disconnected_listener_does_not_cancel_gis_or_admit_second_task(request.getfixturevalue("execution_app"))


def test_flush_failure_does_not_hide_business_exception(request, monkeypatch, caplog):
    import langsmith.client

    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setattr(langsmith.client, "_tracing_control_thread_func", lambda *args: None)
    monkeypatch.setattr(langsmith.client.Client, "_create_run", lambda *a, **kw: None)
    monkeypatch.setattr(langsmith.client.Client, "_update_run", lambda *a, **kw: None)

    def fail_flush(client):
        raise RuntimeError("simulated flush failure")

    async def no_startup_or_database():
        return None

    execution = request.getfixturevalue("execution_app")
    monkeypatch.setattr(langsmith.client.Client, "flush", fail_flush)
    monkeypatch.setattr(backend, "preload_sessions_from_db", no_startup_or_database)
    monkeypatch.setattr(backend, "close_checkpointer_pool", no_startup_or_database)
    plan = execution.approve("business-failure")
    execution.faults["after_dispatch"] = True
    with TestClient(backend.app) as client:
        response = client.post("/api/resume/business-failure")
        assert "database unavailable after external write" in event(response, "error")["message"]
    assert len(execution.calls) == 1
    assert execution.client.get(f"/api/executions/{plan['task_id']}").json()["execution"]["status"] == "needs_attention"
    assert "LangSmith flush failed" in caplog.text


@pytest.mark.parametrize(("key_name", "key_value", "case_id"), [
    ("LANGSMITH_API_KEY", "test-key-never-sent", "test-key-never-sent"),
    ("LANGCHAIN_API_KEY", "legacy-secret-never-sent", "legacy-secret-never-sent"),
    ("LANGCHAIN_API_KEY", '"legacy-secret-never-sent"', "legacy-secret-never-sent"),
    ("OPENAI_API_KEY", "provider-secret-never-sent", "provider-secret-never-sent"),
])
def test_real_sdk_batches_and_flushes_only_masked_runs_to_the_mock_transport(monkeypatch, key_name, key_value, case_id):
    import asyncio
    import requests
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    sent = []
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key-never-sent")
    monkeypatch.setenv(key_name, key_value)
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

    def fake_transport(session, method, url, **kwargs):
        sent.append((method, url, kwargs.get("data")))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.url = url
        return response

    async def no_startup_or_database():
        return None

    monkeypatch.setattr(requests.Session, "request", fake_transport)
    monkeypatch.setattr(backend, "preload_sessions_from_db", no_startup_or_database)
    monkeypatch.setattr(backend, "close_checkpointer_pool", no_startup_or_database)
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    content = asyncio.run(Model().ainvoke([])).content
    model = FakeMessagesListChatModel(responses=[AIMessage(content=content, usage_metadata={
        "input_tokens": 12, "output_tokens": 4, "total_tokens": 16,
    })])
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: model)
    with TestClient(backend.app) as client:
        plan = event(client.post("/api/chat/stream", json={
            "thread_id": "sdk-transport", "message": "Show layers PRIVATE-GEOMETRY-AND-KEY",
            "case_id": case_id,
        }), "human_review_required")["task_plan"]
    assert sent and all(method == "POST" and url.endswith("/runs/multipart") for method, url, _ in sent)
    body = b"\n".join(data for _, _, data in sent)
    assert plan["task_id"].encode() in body and b"sdk-transport" in body
    # FakeMessagesListChatModel exposes normalized AIMessage counts only; no
    # original provider response proves which counts were actually returned.
    assert b'"usage_metadata"' not in body
    assert b"PRIVATE-GEOMETRY-AND-KEY" not in body and b"test-key-never-sent" not in body
    assert case_id.encode() not in body
