import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import planner_node as planner_module
from conftest import fake_spatial_inspector
import ui.backend.main as backend


def _sse_event(response, event_name):
    for event in response.text.strip().split("\n\n"):
        lines = event.splitlines()
        if lines and lines[0] == f"event: {event_name}":
            return json.loads(lines[1].removeprefix("data: "))
    raise AssertionError(f"{event_name} not found in {response.text}")


class FakeModel:
    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps({
            "task_id": "model-id",
            "task_summary": "Load vector",
            "risk_level": "low",
            "review_status": "pending_review",
            "required_inputs": [{"input_id": "input", "description": "Input", "path": "model-path"}],
            "expected_outputs": [],
            "steps": [{"step_id": 1, "description": "Load vector", "allowed_tools": ["add_layer"],
                       "input_refs": ["input"], "output_refs": [], "risk_level": "low", "requires_review": False}],
            "tool_calls": [{"step_id": 1, "tool_name": "add_layer", "arguments": {"path": "model-path", "layer_type": "vector"}}],
        }))


@pytest.fixture(autouse=True)
def isolate_process_ownership(monkeypatch):
    # Each test represents a separate application lifetime. Unknown operations
    # intentionally quarantine the process until explicit reconciliation.
    from agent.tools import execution_guard
    # These tests exercise HTTP/review/ownership with synthetic file contents.
    # Replace only the GIS readback seam; independent GIS checks have their own tests.
    monkeypatch.setattr('agent.spatial_validation._system_inspector', fake_spatial_inspector)
    execution_guard._unknown_tasks.clear()
    yield
    execution_guard._unknown_tasks.clear()


@pytest.fixture
def selected_file(monkeypatch, tmp_path):
    """Register a real file and metadata at the persistence seam for HTTP requests."""
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "file-1.geojson"
    source.write_text("{}")
    record = {"file_id": "file-1", "filename": source.name, "storage_path": source.name,
              "layers": [{"layer_name": "points", "is_2d": True}]}
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: record if file_id == "file-1" else None)
    return "file-1"


@pytest.mark.parametrize("files", [None, []], ids=["omitted", "empty"])
def test_model_cannot_invent_an_input_when_no_file_is_selected(monkeypatch, tmp_path, files):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model-path").write_text("existing input")
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: FakeModel())
    request = {"thread_id": "unselected-input", "message": "Load a vector layer"}
    if files is not None:
        request["files"] = files
    response = TestClient(backend.app).post("/api/chat/stream", json=request)

    message = _sse_event(response, "error")["message"]
    assert "上传" in message and "选择" in message
    assert "human_review_required" not in response.text


@pytest.mark.parametrize("tool", ["add_layer", "execute_processing"])
def test_file_operation_without_a_declared_input_requests_a_selection(monkeypatch, tmp_path, tool):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "model-path").write_text("existing input")

    class UndeclaredInputModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan["required_inputs"] = []
            plan["steps"][0]["input_refs"] = []
            if tool == "execute_processing":
                plan["expected_outputs"] = [{"output_id": "out", "description": "buffer", "path": "buffer.gpkg"}]
                plan["steps"][0].update(allowed_tools=[tool], output_refs=["out"])
                plan["tool_calls"][0].update(tool_name=tool, arguments={
                    "algorithm": "native:buffer", "parameters": {"INPUT": "model-path", "OUTPUT": "buffer.gpkg"}})
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: UndeclaredInputModel())
    response = TestClient(backend.app).post(
        "/api/chat/stream", json={"thread_id": "undeclared-input", "message": "Process the file"}
    )
    message = _sse_event(response, "error")["message"]
    assert "上传" in message and "选择" in message
    assert "human_review_required" not in response.text


def test_current_layer_metadata_can_be_reviewed_without_a_file_selection(monkeypatch):
    class MetadataModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan["required_inputs"] = []
            plan["steps"][0].update(description="Inspect current layer metadata", allowed_tools=["get_layers"], input_refs=[])
            plan["tool_calls"][0].update(tool_name="get_layers", arguments={})
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: MetadataModel())
    response = TestClient(backend.app).post(
        "/api/chat/stream", json={"thread_id": "metadata", "message": "Show current layers"}
    )
    plan = _sse_event(response, "human_review_required")["task_plan"]
    assert plan["required_inputs"] == []
    assert plan["tool_calls"][0]["tool_name"] == "get_layers"


def test_rejected_plan_resumes_to_new_revision_with_same_sse_contract(monkeypatch, tmp_path):
    from agent.nodes import executor_node as executor_module

    monkeypatch.chdir(tmp_path)
    uploaded_path = tmp_path / "file-1.geojson"
    uploaded_path.write_text("{}")
    record = {
        "file_id": "file-1", "filename": "points.geojson", "storage_path": uploaded_path.name,
        "layers": [{"layer_name": "points", "crs": "EPSG:4326", "geometry": "Point", "is_2d": True}],
    }
    class NamedModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan["tool_calls"][0]["arguments"]["name"] = (
                "Revised display" if "Change the display name" in messages[-1].content else "Original display"
            )
            return SimpleNamespace(content=json.dumps(plan))

    calls = []

    class FakeGIS:
        async def list_tools(self):
            return [{"name": "add_layer"}]

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {"status": "success", "result": {"id": "points"}}

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    async def get_gis():
        return FakeGIS()

    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: record if file_id == "file-1" else None)
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "save_task_execution_record", lambda **kwargs: None)
    monkeypatch.setattr(backend, "get_task_execution_record", lambda task_id: None)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: NamedModel())
    monkeypatch.setattr(executor_module, "get_mcp_client", get_gis)
    monkeypatch.setattr(executor_module, "save_task_execution_record", lambda **kwargs: None)
    client = TestClient(backend.app)

    first = client.post("/api/chat/stream", json={"thread_id": "thread-1", "message": "Load points", "files": ["file-1"]})
    assert first.status_code == 200
    first_plan = _sse_event(first, "human_review_required")["task_plan"]
    assert first_plan["required_inputs"][0]["file_id"] == "file-1"
    assert first_plan["tool_calls"][0]["arguments"]["path"] == str(uploaded_path)
    assert first_plan["tool_calls"][0]["arguments"]["name"] == "Original display"

    review = client.post("/api/review", json={"thread_id": "thread-1", "task_id": first_plan["task_id"],
                                              "revision": first_plan["revision"], "approved": False,
                                              "advise": "Change the display name"})
    assert review.status_code == 200
    resumed = client.post("/api/resume/thread-1")
    revised = _sse_event(resumed, "human_review_required")["task_plan"]
    assert revised["task_id"] == first_plan["task_id"]
    assert revised["revision"] == first_plan["revision"] + 1
    assert revised["review_status"] == "pending_review"

    assert revised["tool_calls"][0]["arguments"]["name"] == "Revised display"
    assert client.post("/api/review", json={"thread_id": "thread-1", "task_id": revised["task_id"],
                                            "revision": revised["revision"], "approved": True}).status_code == 200
    _sse_event(client.post("/api/resume/thread-1"), "result_review_required")
    assert calls == [("add_layer", revised["tool_calls"][0]["arguments"])]


def test_approved_load_and_result_confirmation_are_queryable(monkeypatch, tmp_path):
    from agent.nodes import executor_node as executor_module

    monkeypatch.chdir(tmp_path)
    uploaded_path = tmp_path / "file-1.geojson"
    uploaded_path.write_text("{}")
    file_record = {"file_id": "file-1", "filename": "points.geojson", "storage_path": uploaded_path.name,
                   "layers": [{"layer_name": "points", "is_2d": True}]}
    records = {}

    def save_record(**record):
        records[record["task_id"]] = {**record, "confirmed": None, "summary": None}

    def get_record(task_id):
        record = records.get(task_id)
        if not record:
            return None
        return {"execution": record, "attempts": record["attempts"], "artifacts": []}

    def confirm_record(task_id, confirmed, summary=None):
        if task_id not in records:
            return False
        records[task_id]["confirmed"] = confirmed
        records[task_id]["summary"] = summary
        return True

    class FakeGIS:
        def __init__(self):
            self.calls = []

        async def list_tools(self):
            return [{"name": "add_layer"}]

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"status": "success", "result": {"id": "points", "feature_count": 3}}

    gis = FakeGIS()
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    async def get_gis():
        return gis

    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: file_record)
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "save_task_execution_record", save_record)
    monkeypatch.setattr(executor_module, "save_task_execution_record", save_record)
    monkeypatch.setattr(backend, "get_task_execution_record", get_record)
    monkeypatch.setattr(backend, "confirm_task_execution", confirm_record)
    monkeypatch.setattr(executor_module, "get_mcp_client", get_gis)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: FakeModel())
    client = TestClient(backend.app)

    first = client.post("/api/chat/stream", json={"thread_id": "thread-2", "message": "Load points", "files": ["file-1"]})
    plan = _sse_event(first, "human_review_required")["task_plan"]
    assert gis.calls == []
    stale = client.post("/api/review", json={"thread_id": "thread-2", "task_id": plan["task_id"],
                                             "revision": plan["revision"] + 1, "approved": True})
    assert stale.status_code == 409
    assert gis.calls == []
    approved = client.post("/api/review", json={"thread_id": "thread-2", "task_id": plan["task_id"],
                                                "revision": plan["revision"], "approved": True})
    assert approved.status_code == 200
    resumed = client.post("/api/resume/thread-2")
    _sse_event(resumed, "result_review_required")
    assert gis.calls == [("add_layer", plan["tool_calls"][0]["arguments"])]
    result = client.get(f"/api/executions/{plan['task_id']}").json()["execution"]
    assert result["status"] == "success"
    assert result["attempts"][0]["observation"]["data"]["feature_count"] == 3
    assert result["confirmed"] is None

    unsolicited = client.post("/api/risk-review", json={
        "thread_id": "thread-2", "task_id": plan["task_id"], "revision": plan["revision"], "review_id": "no-pending-review", "status": "approved",
        "tool_call": plan["tool_calls"][0],
    })
    assert unsolicited.status_code == 409
    assert len(gis.calls) == 1

    response = client.post("/api/confirm-result", json={"thread_id": "thread-2", "task_id": plan["task_id"], "revision": plan["revision"], "review_id": f"result:{plan['task_id']}:{plan['revision']}", "is_completed": True})
    assert response.status_code == 200
    result = client.get(f"/api/executions/{plan['task_id']}").json()["execution"]
    assert result["confirmed"] is True


def test_history_shows_current_task_plan_and_business_records(monkeypatch):
    plan = {
        "task_id": "task-1", "revision": 2, "task_summary": "Load points",
        "review_status": "pending_review", "risk_level": "low", "steps": [], "tool_calls": [],
    }
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    import asyncio
    asyncio.run(graph.aupdate_state({"configurable": {"thread_id": "history-1"}},
                                    {"session_id": "history-1", "input_query": "Load points", "task_plan": plan}))
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "list_task_executions_for_session",
                        lambda session: [{"task_id": "task-1", "status": "rejected"}])
    client = TestClient(backend.app)

    response = client.get("/api/sessions/history-1/history")
    assert response.status_code == 200
    history = response.json()
    assert history["task_plan"]["task_id"] == "task-1"
    assert history["executions"] == [{"task_id": "task-1", "status": "rejected"}]
    assert any("Load points" in message["content"] for message in history["chat_messages"])


def test_uploaded_multilayer_file_requires_an_explicit_2d_layer(monkeypatch, tmp_path):
    records = {}
    layers = [
        {"layer_name": "points", "crs": "EPSG:4326", "geometry": "Point", "feature_count": 3,
         "fields": ["value"], "is_2d": True},
        {"layer_name": "points_z", "crs": "EPSG:4326", "geometry": "Point Z", "feature_count": 1,
         "fields": [], "is_2d": False},
    ]

    def save_file(file_id, filename, storage_path, size_bytes, sha256, layers):
        records[file_id] = {"file_id": file_id, "filename": filename, "storage_path": storage_path,
                            "size_bytes": size_bytes, "sha256": sha256, "layers": layers,
                            "uploaded_at": "2026-10-30T00:00:00"}

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "inspect_uploaded_vector", lambda path: layers)
    monkeypatch.setattr(backend, "save_uploaded_file_record", save_file)
    monkeypatch.setattr(backend, "list_uploaded_file_records", lambda: list(records.values()))
    monkeypatch.setattr(backend, "get_uploaded_file_record", records.get)
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: FakeModel())
    client = TestClient(backend.app)

    upload = client.post("/api/upload", files={"file": ("points.gpkg", b"test gpkg", "application/geopackage+sqlite3")})
    assert upload.status_code == 200
    file = upload.json()
    assert file["filename"] == "points.gpkg"
    assert file["size"] == 9
    assert "storage_path" not in file
    assert file["layer_info"] is None
    assert client.get("/api/files").json()["files"][0]["file_id"] == file["file_id"]
    request = {"thread_id": "layers", "message": "Load points", "files": [file["file_id"]]}
    assert client.post("/api/chat/stream", json=request).status_code == 422
    request["files"] = [{"file_id": file["file_id"], "layer_name": "points_z"}]
    assert client.post("/api/chat/stream", json=request).status_code == 422
    request["files"] = [{"file_id": file["file_id"], "layer_name": "points"}]
    plan = _sse_event(client.post("/api/chat/stream", json=request), "human_review_required")["task_plan"]
    assert plan["required_inputs"][0]["layer_name"] == "points"
    assert plan["tool_calls"][0]["arguments"]["path"].endswith(".gpkg|layername=points")


@pytest.mark.parametrize(
    ("reply", "expected_status"),
    [("null", "needs_attention"),
     ({"status": "success", "result": {}}, "needs_attention"),
     ({"status": "error", "message": "invalid parameter", "outcome": "not_started"}, "failed"),
     ({"status": "error", "message": "after write", "outcome": "unknown"}, "needs_attention"),
     ({"status": "error", "message": "legacy unspecified write error"}, "needs_attention")],
)
def test_qgis_failure_never_changes_approved_arguments_or_reports_success(
    monkeypatch, tmp_path, reply, expected_status
):
    from agent.nodes import executor_node as executor_module

    monkeypatch.chdir(tmp_path)
    uploaded_path = tmp_path / "points.geojson"
    uploaded_path.write_text("{}")
    file_record = {"file_id": "file-1", "filename": "points.geojson", "storage_path": uploaded_path.name,
                   "layers": [{"layer_name": "points", "is_2d": True}]}
    records = {}
    calls = []

    class FakeGIS:
        async def list_tools(self):
            return [{"name": "add_layer"}]

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return reply

    class RepairSuggestingModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan["tool_calls"][0]["metadata"] = {"repair_arguments": {"name": "unreviewed"}}
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    async def get_gis():
        return FakeGIS()

    def save_record(**record):
        records[record["task_id"]] = record

    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: file_record)
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "save_task_execution_record", save_record)
    monkeypatch.setattr(backend, "get_task_execution_record", lambda task_id: None)
    monkeypatch.setattr(executor_module, "save_task_execution_record", save_record)
    monkeypatch.setattr(executor_module, "get_mcp_client", get_gis)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: RepairSuggestingModel())
    client = TestClient(backend.app)
    plan = _sse_event(client.post("/api/chat/stream", json={"thread_id": "unknown", "message": "Load points", "files": ["file-1"]}),
                      "human_review_required")["task_plan"]
    assert client.post("/api/review", json={"thread_id": "unknown", "task_id": plan["task_id"],
                                            "revision": plan["revision"], "approved": True}).status_code == 200
    resumed = client.post("/api/resume/unknown")
    assert _sse_event(resumed, "error")["message"]
    assert len(calls) == 1
    assert records[plan["task_id"]]["status"] == expected_status
    assert records[plan["task_id"]]["attempts"][0]["observation"]["status"] != "success"


def test_selected_file_cannot_be_bypassed_by_omitting_step_input_reference(monkeypatch, tmp_path):
    uploaded_path = tmp_path / "points.geojson"
    uploaded_path.write_text("{}")
    file_record = {"file_id": "file-1", "filename": "points.geojson", "storage_path": uploaded_path.name,
                   "layers": [{"layer_name": "points", "is_2d": True}]}

    class UnrelatedInputModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan["steps"][0]["input_refs"] = []
            plan["tool_calls"][0]["arguments"]["path"] = str(tmp_path / "unselected.geojson")
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: file_record)
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: UnrelatedInputModel())
    client = TestClient(backend.app)
    response = client.post("/api/chat/stream", json={"thread_id": "wrong-input", "message": "Load points", "files": ["file-1"]})
    assert "unused_input" in _sse_event(response, "error")["message"]
    assert "event: human_review_required" not in response.text


@pytest.mark.parametrize("tool", ["execute_processing", "capture_map_canvas"])
@pytest.mark.parametrize("target_kind", ["selected", "other_uploaded"])
def test_plan_cannot_write_over_an_uploaded_source(monkeypatch, tmp_path, tool, target_kind):
    uploaded_path = tmp_path / "points.geojson"
    uploaded_path.write_text("original source")
    other_upload = tmp_path / "other.geojson"
    other_upload.write_text("other source")
    target_path = uploaded_path if target_kind == "selected" else other_upload
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    file_record = {"file_id": "file-1", "filename": "points.geojson", "storage_path": uploaded_path.name,
                   "layers": [{"layer_name": "points", "is_2d": True}]}

    class OverwritingModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan["expected_outputs"] = [{"output_id": "output", "description": "result", "path": str(tmp_path.parent / "declared-output.gpkg")}]
            plan["steps"][0].update({"description": "Write output", "allowed_tools": [tool], "output_refs": ["output"]})
            args = ({"algorithm": "native:buffer", "parameters": {"INPUT": str(uploaded_path),
                     "OUTPUT": str(target_path), "DISTANCE": 5}} if tool == "execute_processing"
                    else {"path": str(target_path)})
            plan["tool_calls"][0].update({"tool_name": tool, "arguments": args})
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda file_id: file_record)
    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: OverwritingModel())
    client = TestClient(backend.app)
    response = client.post("/api/chat/stream", json={"thread_id": "overwrite-input", "message": "Process points", "files": ["file-1"]})
    assert "unsafe_output_path" in _sse_event(response, "error")["message"]
    assert "event: human_review_required" not in response.text
    assert uploaded_path.read_text() == "original source"
    assert other_upload.read_text() == "other source"


@pytest.mark.parametrize('risk,flag', [('high', True), ('medium', False), ('low', True)])
def test_inconsistent_step_and_call_risk_never_reaches_review_or_gis(monkeypatch, selected_file, risk, flag):
    calls = []

    from agent.nodes import executor_node as executor_module

    class FakeGIS:
        async def list_tools(self):
            return [{"name": "add_layer"}]

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {"status": "success", "result": {"id": "unexpected"}}

    async def get_gis():
        return FakeGIS()

    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)

    class InconsistentModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['steps'][0].update(risk_level=risk, requires_review=flag)
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: InconsistentModel())
    response = TestClient(backend.app).post('/api/chat/stream', json={'thread_id': 'risk-mismatch', 'message': 'Load', 'files': [selected_file]})
    assert 'inconsistent_call_risk' in _sse_event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text
    assert calls == []


@pytest.mark.parametrize('tool', ['capture_map_canvas', 'execute_processing'])
def test_nonselected_original_file_is_rejected_before_review(monkeypatch, tmp_path, selected_file, tool):
    monkeypatch.chdir(tmp_path)
    original = tmp_path / 'data' / 'original.png'
    original.parent.mkdir()
    original.write_bytes(b'original asset')

    class OverwritingModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['expected_outputs'] = [{'output_id': 'image', 'description': 'image', 'path': str(original)}]
            plan['steps'][0].update(allowed_tools=[tool], output_refs=['image'])
            arguments = {'path': str(original)} if tool == 'capture_map_canvas' else {
                'algorithm': 'native:buffer', 'parameters': {'INPUT': 'model-path', 'nested': [{'OUTPUT': str(original)}]}}
            plan['tool_calls'][0].update(tool_name=tool, arguments=arguments)
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: OverwritingModel())
    response = TestClient(backend.app).post('/api/chat/stream', json={'thread_id': 'original-write', 'message': 'image', 'files': [selected_file]})
    assert 'unsafe_output_path' in _sse_event(response, 'error')['message']
    assert 'event: human_review_required' not in response.text
    assert original.read_bytes() == b'original asset'


def test_output_filename_is_bound_before_review_and_existing_target_is_refused_at_resume(monkeypatch, tmp_path):
    from pathlib import Path
    from agent.nodes import executor_node as executor_module
    monkeypatch.chdir(tmp_path)
    calls = []

    class ScreenshotModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['expected_outputs'] = [{'output_id': 'image', 'description': 'map image', 'path': 'view.png'}]
            plan['required_inputs'] = []
            plan['steps'][0].update(allowed_tools=['capture_map_canvas'], input_refs=[], output_refs=['image'])
            plan['tool_calls'][0].update(tool_name='capture_map_canvas', arguments={'path': 'view.png'})
            return SimpleNamespace(content=json.dumps(plan))

    class FakeGIS:
        async def list_tools(self):
            return [{'name': 'capture_map_canvas'}]

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {'status': 'success', 'result': arguments}

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    async def get_gis():
        return FakeGIS()

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(backend, 'save_task_execution_record', lambda **kwargs: None)
    monkeypatch.setattr(backend, 'get_task_execution_record', lambda task_id: None)
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    monkeypatch.setattr(executor_module, 'save_task_execution_record', lambda **kwargs: None)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: ScreenshotModel())
    client = TestClient(backend.app)
    plan = _sse_event(client.post('/api/chat/stream', json={'thread_id': 'new-image', 'message': 'map'}), 'human_review_required')['task_plan']
    target = tmp_path / 'shared' / 'outputs' / plan['task_id'] / 'r1' / 'image' / 'view.png'
    assert plan['expected_outputs'][0]['path'] == str(target)
    assert plan['tool_calls'][0]['arguments']['path'] == str(target)
    assert client.post('/api/review', json={'thread_id': 'new-image', 'task_id': plan['task_id'], 'revision': 1, 'approved': True}).status_code == 200
    target.parent.mkdir(parents=True)
    target.write_bytes(b'existing artifact')
    response = client.post('/api/resume/new-image')
    assert '校验失败' in _sse_event(response, 'error')['message']
    assert calls == []
    assert target.read_bytes() == b'existing artifact'


@pytest.fixture
def execution_app(monkeypatch, tmp_path, selected_file):
    """The agreed model/GIS/persistence seams; production HTTP and graph remain real."""
    import threading
    from agent.nodes import executor_node as executor_module
    monkeypatch.chdir(tmp_path)
    records = {}
    calls = []
    record_lock = threading.Lock()
    faults = {'after_dispatch': False}
    entered = threading.Event()
    release = threading.Event()
    block = {'enabled': False}
    available_tools = [{'name': 'add_layer'}]

    def save_record(**record):
        with record_lock:
            task_id = record['task_id']
            prior = records.get(task_id)
            if record.get('claim_execution'):
                if not prior or prior['status'] not in {'approved', 'needs_review'}:
                    if prior and prior['status'] == 'running':
                        prior['status'] = 'needs_attention'
                    raise RuntimeError('Execution requires attention or has already completed')
                if prior['plan'] != record['plan']:
                    raise RuntimeError('Execution plan differs from reviewed plan')
                if prior['status'] == 'needs_review' and (
                    record.get('checkpoint_resume') is None
                    or prior.get('resume_state') != record.get('checkpoint_resume')
                ):
                    prior['status'] = 'needs_attention'
                    raise RuntimeError('Checkpoint does not match the durable risk pause; needs attention')
            if faults['after_dispatch'] and calls and not record.get('inflight_call'):
                raise RuntimeError('database unavailable after external write')
            records[task_id] = {**record, 'confirmed': None, 'summary': None}

    class FakeGIS:
        async def list_tools(self):
            return list(available_tools)

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            entered.set()
            if block['enabled'] and len(calls) == 1:
                import asyncio
                await asyncio.to_thread(release.wait, 5)
            return {'status': 'success', 'result': {'id': f'layer-{len(calls)}'}}

    graph = build_graph(MemorySaver())
    graphs = {'current': graph}

    async def get_graph():
        return graphs['current']

    async def get_gis():
        return FakeGIS()

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(backend, 'save_task_execution_record', save_record)
    def query_record(task_id):
        if task_id not in records:
            return None
        from agent.tools.execution_guard import is_task_active, block_unknown_task
        if records[task_id]['status'] == 'running' and not is_task_active(task_id):
            records[task_id]['status'] = 'needs_attention'
            block_unknown_task(task_id)
        return {'execution': records[task_id], 'attempts': records[task_id]['attempts'], 'artifacts': []}

    monkeypatch.setattr(backend, 'get_task_execution_record', query_record)
    monkeypatch.setattr(executor_module, 'save_task_execution_record', save_record)
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: FakeModel())

    client = TestClient(backend.app)

    def approve(thread):
        plan = _sse_event(client.post('/api/chat/stream', json={'thread_id': thread, 'message': 'Load', 'files': [selected_file]}), 'human_review_required')['task_plan']
        assert plan['required_inputs'][0]['file_id'] == selected_file
        assert plan['tool_calls'][0]['arguments']['path'] == str(tmp_path / 'file-1.geojson')
        assert client.post('/api/review', json={'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'], 'approved': True}).status_code == 200
        return plan

    from agent.tools import execution_guard
    execution_guard._unknown_tasks.clear()
    try:
        yield SimpleNamespace(client=client, approve=approve, records=records, calls=calls,
                              entered=entered, release=release, block=block, faults=faults,
                              graphs=graphs, available_tools=available_tools)
    finally:
        execution_guard._unknown_tasks.clear()


@pytest.mark.parametrize('second_thread', ['first', 'second'])
def test_concurrent_resume_rejects_same_or_different_task_without_queueing(execution_app, second_thread):
    from concurrent.futures import ThreadPoolExecutor
    env = execution_app
    env.approve('first')
    if second_thread == 'second':
        env.approve('second')
    env.block['enabled'] = True
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(env.client.post, '/api/resume/first')
        assert env.entered.wait(5), 'first operation did not reach GIS'
        try:
            second = env.client.post(f'/api/resume/{second_thread}')
            assert second.status_code == 409
            assert 'busy' in second.json()['detail'].lower()
            assert len(env.calls) == 1
        finally:
            env.release.set()
        assert first.result(timeout=10).status_code == 200
    assert len(env.calls) == 1


def test_database_failure_after_write_cannot_replay_on_resume(execution_app):
    env = execution_app
    plan = env.approve('failure')
    env.faults['after_dispatch'] = True
    assert 'database unavailable after external write' in _sse_event(env.client.post('/api/resume/failure'), 'error')['message']
    assert len(env.calls) == 1
    env.faults['after_dispatch'] = False
    response = env.client.post('/api/resume/failure')
    assert 'result_review_required' not in response.text
    assert len(env.calls) == 1
    assert env.client.get(f"/api/executions/{plan['task_id']}").json()['execution']['status'] == 'needs_attention'
    assert env.records[plan['task_id']]['status'] == 'needs_attention'


@pytest.mark.parametrize('location', ['step', 'input', 'output'])
def test_nested_plan_fields_are_strict_before_review(monkeypatch, selected_file, location):
    class NestedCallModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            if location == 'step':
                plan['steps'][0]['tool_calls'] = [{'tool_name': 'ping', 'arguments': {}}]
            elif location == 'input':
                plan['required_inputs'][0]['unchecked'] = True
            else:
                plan['expected_outputs'] = [{'output_id': 'image', 'description': 'image', 'path': 'image.png', 'unchecked': True}]
                plan['steps'][0].update(allowed_tools=['capture_map_canvas'], output_refs=['image'])
                plan['tool_calls'][0].update(tool_name='capture_map_canvas', arguments={'path': 'image.png'})
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: NestedCallModel())
    response = TestClient(backend.app).post('/api/chat/stream', json={'thread_id': 'strict', 'message': 'Load', 'files': [selected_file]})
    message = _sse_event(response, 'error')['message']
    assert 'Extra inputs are not permitted' in message
    assert ('tool_calls' if location == 'step' else 'unchecked') in message
    assert 'human_review_required' not in response.text


@pytest.mark.parametrize('tool', ['get_layers', 'execute_processing'])
def test_selected_input_must_be_consumed_by_the_actual_call(monkeypatch, tmp_path, tool):
    source = tmp_path / 'selected.geojson'
    source.write_text('{}')
    monkeypatch.chdir(tmp_path)

    class UnusedFileModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['steps'][0].update(allowed_tools=[tool])
            args = {}
            if tool == 'execute_processing':
                plan['expected_outputs'] = [{'output_id': 'out', 'description': 'new buffer', 'path': 'buffer.gpkg'}]
                plan['steps'][0]['output_refs'] = ['out']
                args = {'algorithm': 'native:buffer', 'parameters': {'INPUT': 'shared/unselected.geojson', 'OUTPUT': 'buffer.gpkg'}}
            plan['tool_calls'][0].update(tool_name=tool, arguments=args)
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(backend, 'get_uploaded_file_record', lambda file_id: {'file_id': 'file-1', 'filename': source.name,
                         'storage_path': source.name, 'layers': [{'layer_name': 'source', 'is_2d': True}]})
    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: UnusedFileModel())
    response = TestClient(backend.app).post('/api/chat/stream', json={'thread_id': 'consume', 'message': 'Process', 'files': ['file-1']})
    assert 'call_input_reference_mismatch' in _sse_event(response, 'error')['message']
    assert 'human_review_required' not in response.text


@pytest.mark.parametrize('arguments', [{'layer_id': 'points', 'limit': 'not-a-number'},
                                      {'layer_id': 'points', 'unexpected': 'ignored'},
                                      {'layer_id': 7}])
def test_invalid_later_call_arguments_reject_the_whole_plan_before_any_write(monkeypatch, selected_file, arguments):
    class InvalidLaterModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['steps'].append({'step_id': 2, 'description': 'load feature metadata',
                                  'allowed_tools': ['get_layer_features'], 'input_refs': [], 'output_refs': [],
                                  'risk_level': 'low', 'requires_review': False})
            plan['tool_calls'].append({'step_id': 2, 'tool_name': 'get_layer_features', 'arguments': arguments})
            return SimpleNamespace(content=json.dumps(plan))

    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: InvalidLaterModel())
    response = TestClient(backend.app).post('/api/chat/stream', json={'thread_id': 'late-invalid', 'message': 'Load and read', 'files': [selected_file]})
    message = _sse_event(response, 'error')['message']
    assert 'invalid_tool_arguments' in message
    assert 'Step 2' in message
    assert 'human_review_required' not in response.text


def test_disconnect_before_response_body_releases_the_execution_slot(execution_app):
    import asyncio
    env = execution_app
    env.approve('disconnect')

    async def disconnect_request():
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'scheme': 'http', 'method': 'POST',
                 'path': '/api/resume/disconnect', 'raw_path': b'/api/resume/disconnect',
                 'query_string': b'', 'headers': [], 'server': ('testserver', 80), 'client': ('test', 1)}

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        async def send(message):
            if message['type'] == 'http.response.start':
                raise BrokenPipeError('browser closed before SSE started')

        try:
            await backend.app(scope, receive, send)
        except Exception:
            pass

    asyncio.run(disconnect_request())
    response = env.client.post('/api/resume/disconnect')
    assert response.status_code == 200
    _sse_event(response, 'result_review_required')
    assert len(env.calls) == 1


def test_invalid_processing_after_load_is_rejected_before_review_and_before_any_write(monkeypatch, tmp_path, selected_file):
    from agent.nodes import executor_node as executor_module
    monkeypatch.chdir(tmp_path)
    calls = []
    inspections = []

    class ProcessingModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['expected_outputs'] = [{'output_id': 'buffer', 'description': 'buffer', 'path': 'buffer.gpkg'}]
            plan['steps'].append({'step_id': 2, 'description': 'Buffer', 'allowed_tools': ['execute_processing'],
                                  'input_refs': ['input'], 'output_refs': ['buffer'],
                                  'risk_level': 'low', 'requires_review': False})
            plan['tool_calls'].append({'step_id': 2, 'tool_name': 'execute_processing', 'distance_unit': 'm', 'arguments': {
                'algorithm': 'native:buffer', 'parameters': {'INPUT': 'model-path', 'OUTPUT': 'buffer.gpkg',
                'DISTANCE': 'not-numeric', 'SEGMENTS': 5, 'END_CAP_STYLE': 0, 'JOIN_STYLE': 0,
                'MITER_LIMIT': 2, 'DISSOLVE': False}}})
            return SimpleNamespace(content=json.dumps(plan))

    class FakeGIS:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            assert name == 'get_qgis_info', 'a write was sent before whole-plan validation'
            return {'status': 'error', 'message': 'Invalid JSON type for QGIS parameter DISTANCE', 'outcome': 'not_started'}

    async def get_gis():
        return FakeGIS()

    def inspect_parameters(requests):
        if requests == {'capabilities': True}:
            return {'status': 'success', 'result': {'qgis_version': 'test', 'processing_capabilities': []}}
        inspections.append(requests)
        assert requests[0]['algorithm'] == 'native:buffer'
        return {'status': 'error', 'message': 'Invalid JSON type for QGIS parameter DISTANCE', 'outcome': 'not_started'}

    graph = build_graph(MemorySaver(), processing_inspector=inspect_parameters)

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: ProcessingModel())
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    response = TestClient(backend.app).post('/api/chat/stream', json={'thread_id': 'late-processing', 'message': 'Load and buffer 100 meters', 'files': [selected_file]})
    assert 'Invalid JSON type for QGIS parameter DISTANCE' in _sse_event(response, 'error')['message']
    assert 'human_review_required' not in response.text
    assert inspections
    assert inspections[0][0]['parameters']['INPUT'] == str(tmp_path / 'file-1.geojson')
    assert calls == []


def test_fresh_application_graph_does_not_replay_persisted_running_task(execution_app):
    env = execution_app
    plan = env.approve('restart')
    env.faults['after_dispatch'] = True
    _sse_event(env.client.post('/api/resume/restart'), 'error')
    assert env.records[plan['task_id']]['status'] == 'running'
    assert env.records[plan['task_id']]['inflight_call']['tool_name'] == 'add_layer'
    checkpoint = env.graphs['current'].checkpointer
    env.graphs['current'] = build_graph(checkpoint)
    env.faults['after_dispatch'] = False
    queried = env.client.get(f"/api/executions/{plan['task_id']}")
    assert queried.json()['execution']['status'] == 'needs_attention'
    response = TestClient(backend.app).post('/api/resume/restart')
    assert response.status_code == 409
    assert len(env.calls) == 1
    record = env.client.get(f"/api/executions/{plan['task_id']}").json()['execution']
    assert record['status'] == 'needs_attention'
    assert record['inflight_call']['tool_name'] == 'add_layer'


def test_missing_execution_record_fails_closed_without_a_gis_call(execution_app):
    env = execution_app
    env.approve('missing')
    env.records.clear()
    response = env.client.post('/api/resume/missing')
    assert 'Execution requires attention' in _sse_event(response, 'error')['message']
    assert env.calls == []


@pytest.mark.parametrize('kind', ['unavailable', 'changed_schema'])
def test_later_remote_tool_contract_is_checked_before_any_write(execution_app, monkeypatch, kind):
    env = execution_app

    class LoadAndReadModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['steps'].append({'step_id': 2, 'description': 'load feature metadata',
                                  'allowed_tools': ['get_layer_features'], 'input_refs': [], 'output_refs': [],
                                  'risk_level': 'low', 'requires_review': False})
            plan['tool_calls'].append({'step_id': 2, 'tool_name': 'get_layer_features',
                                      'arguments': {'layer_id': 'points', 'limit': 10}})
            return SimpleNamespace(content=json.dumps(plan))

    monkeypatch.setattr(planner_module, '_get_llm', lambda state: LoadAndReadModel())
    if kind == 'changed_schema':
        env.available_tools.append({'name': 'get_layer_features', 'input_schema': {'type': 'object', 'required': ['new_parameter']}})
    env.approve('capabilities')
    response = env.client.post('/api/resume/capabilities')
    expected = 'unavailable' if kind == 'unavailable' else 'schema differs'
    assert expected in _sse_event(response, 'error')['message']
    assert env.calls == []


def test_planner_resume_does_not_reserve_gis_slot(execution_app, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    env = execution_app
    env.approve('gis-ready')
    waiting = threading.Event()
    release = threading.Event()
    original = backend.stream_agent_execution

    async def slow_planning(app, initial, thread, config):
        if thread == 'planning':
            waiting.set()
            import asyncio
            await asyncio.to_thread(release.wait, 5)
            yield backend.format_sse_event('done', {})
        else:
            async for event in original(app, initial, thread, config):
                yield event

    monkeypatch.setattr(backend, 'stream_agent_execution', slow_planning)
    with ThreadPoolExecutor(max_workers=2) as workers:
        planning = workers.submit(env.client.post, '/api/resume/planning')
        assert waiting.wait(5)
        try:
            response = env.client.post('/api/resume/gis-ready')
            assert response.status_code == 200
            _sse_event(response, 'result_review_required')
        finally:
            release.set()
        assert planning.result(timeout=10).status_code == 200
    assert len(env.calls) == 1


def test_disconnected_listener_does_not_cancel_gis_or_admit_second_task(execution_app):
    import asyncio
    env = execution_app
    env.approve('first')
    env.approve('second')
    env.block['enabled'] = True

    async def exercise():
        response = await backend.resume_execution('first')
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'method': 'POST', 'path': '/api/resume/first',
                 'headers': []}

        async def receive():
            return {'type': 'http.disconnect'}

        async def send(message):
            if message['type'] == 'http.response.start':
                raise BrokenPipeError('listener gone before body iteration')

        try:
            await response(scope, receive, send)
        except (BrokenPipeError, OSError, ClientDisconnect):
            pass
        assert await asyncio.to_thread(env.entered.wait, 2)
        try:
            with pytest.raises(backend.HTTPException) as caught:
                await backend.resume_execution('second')
            assert caught.value.status_code == 409
            assert len(env.calls) == 1
        finally:
            env.release.set()
        for _ in range(200):
            if env.records[next(iter(env.records))]['status'] == 'success':
                break
            await asyncio.sleep(.01)
        assert len(env.calls) == 1
        first_record = next(record for record in env.records.values() if record['session_id'] == 'first')
        assert first_record['status'] == 'success'

    asyncio.run(exercise())


@pytest.fixture
def risk_execution_app(execution_app, monkeypatch):
    from pathlib import Path
    from agent.nodes import executor_node as executor_module
    env = execution_app

    class RiskModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['risk_level'] = 'high'
            plan['expected_outputs'] = [
                {'output_id': 'map', 'description': 'map', 'path': 'map.png'},
                {'output_id': 'project', 'description': 'project', 'path': 'reviewed.png'}]
            plan['steps'] += [
                {'step_id': 2, 'description': 'Capture', 'allowed_tools': ['capture_map_canvas'],
                 'input_refs': [], 'output_refs': ['map'], 'risk_level': 'low', 'requires_review': False},
                {'step_id': 3, 'description': 'Capture reviewed map', 'allowed_tools': ['capture_map_canvas'],
                 'input_refs': [], 'output_refs': ['project'], 'risk_level': 'high', 'requires_review': True}]
            plan['tool_calls'] += [
                {'step_id': 2, 'tool_name': 'capture_map_canvas', 'arguments': {'path': 'map.png'}},
                {'step_id': 3, 'tool_name': 'capture_map_canvas', 'arguments': {'path': 'reviewed.png'},
                 'risk_level': 'high', 'requires_review': True}]
            return SimpleNamespace(content=json.dumps(plan))

    class RiskGIS:
        async def list_tools(self):
            return [{'name': name} for name in ['add_layer', 'capture_map_canvas', 'capture_map_canvas']]

        async def call_tool(self, name, args):
            env.calls.append((name, args))
            if name == 'add_layer':
                return {'status': 'success', 'result': {'id': 'points'}}
            path = Path(args['path'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fake artifact')
            return {'status': 'success', 'result': {'success': True, 'path': str(path), 'width': 640, 'height': 480}}

    async def get_gis():
        return RiskGIS()

    monkeypatch.setattr(planner_module, '_get_llm', lambda state: RiskModel())
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    return env


def test_risk_resume_consumes_checkpoint_prefix_with_existing_outputs(risk_execution_app):
    env = risk_execution_app
    plan = env.approve('risk-positive')
    first = env.client.post('/api/resume/risk-positive')
    risk = _sse_event(first, 'risk_review_required')
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas']
    assert env.client.post('/api/risk-review', json={
        'thread_id': 'risk-positive', 'task_id': plan['task_id'], 'revision': plan['revision'], 'review_id': risk['review_id'], 'status': 'approved'}).status_code == 200
    continued = env.client.post('/api/resume/risk-positive')
    _sse_event(continued, 'result_review_required')
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas', 'capture_map_canvas']
    assert env.records[plan['task_id']]['status'] == 'success'


def test_durable_risk_pause_without_checkpoint_never_replays_prefix(risk_execution_app, monkeypatch):
    from agent.nodes import executor_node as executor_module
    env = risk_execution_app
    plan = env.approve('risk-crash')
    persist = executor_module._persist_execution_record
    crash = {'enabled': True}

    async def persist_then_crash(*args, **kwargs):
        await persist(*args, **kwargs)
        if args[3] == 'needs_review' and crash['enabled']:
            raise RuntimeError('crash between durable risk pause and checkpoint')

    monkeypatch.setattr(executor_module, '_persist_execution_record', persist_then_crash)
    assert 'crash between' in _sse_event(env.client.post('/api/resume/risk-crash'), 'error')['message']
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas']
    # Remove the completed screenshot to expose replay independently of output guards.
    from pathlib import Path
    Path(env.calls[-1][1]['path']).unlink()
    crash['enabled'] = False
    response = env.client.post('/api/resume/risk-crash')
    assert 'result_review_required' not in response.text
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas']
    assert env.records[plan['task_id']]['status'] == 'needs_attention'


def test_live_execution_query_does_not_misclassify_running_owner(execution_app):
    from concurrent.futures import ThreadPoolExecutor
    env = execution_app
    plan = env.approve('live-query')
    env.block['enabled'] = True
    with ThreadPoolExecutor(max_workers=1) as workers:
        running = workers.submit(env.client.post, '/api/resume/live-query')
        assert env.entered.wait(5)
        try:
            response = env.client.get(f"/api/executions/{plan['task_id']}")
            assert response.json()['execution']['status'] == 'running'
        finally:
            env.release.set()
        _sse_event(running.result(timeout=10), 'result_review_required')


def test_unknown_task_blocks_different_ready_task(execution_app):
    env = execution_app
    first = env.approve('unknown-first')
    env.approve('different-ready')
    env.faults['after_dispatch'] = True
    _sse_event(env.client.post('/api/resume/unknown-first'), 'error')
    env.faults['after_dispatch'] = False
    queried = env.client.get(f"/api/executions/{first['task_id']}")
    assert queried.json()['execution']['status'] == 'needs_attention'
    second = env.client.post('/api/resume/different-ready')
    assert second.status_code == 409
    assert 'needs attention' in second.json()['detail']
    assert len(env.calls) == 1


def test_changed_completed_artifact_cannot_authorize_risk_continuation(risk_execution_app):
    from pathlib import Path
    env = risk_execution_app
    plan = env.approve('changed-artifact')
    risk = _sse_event(env.client.post('/api/resume/changed-artifact'), 'risk_review_required')
    completed = Path(env.calls[-1][1]['path'])
    completed.write_bytes(b'changed externally')
    assert env.client.post('/api/risk-review', json={
        'thread_id': 'changed-artifact', 'task_id': plan['task_id'], 'revision': plan['revision'], 'review_id': risk['review_id'], 'status': 'approved'}).status_code == 200
    response = env.client.post('/api/resume/changed-artifact')
    assert 'artifact changed' in _sse_event(response, 'error')['message']
    assert len(env.calls) == 2
    assert env.client.get(f"/api/executions/{plan['task_id']}").json()['execution']['status'] == 'needs_attention'


@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_later_original_input_failure_prevents_even_first_mcp_connection(execution_app, monkeypatch, tmp_path, mutation):
    import hashlib
    from agent.nodes import executor_node as executor_module
    env = execution_app
    sources = [tmp_path / 'file-1.geojson', tmp_path / 'file-2.geojson']
    sources[1].write_text('{"second": true}')
    records = {
        f'file-{index}': {'file_id': f'file-{index}', 'filename': path.name, 'storage_path': path.name,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'layers': [{'layer_name': 'points', 'is_2d': True}]}
        for index, path in enumerate(sources, start=1)
    }
    monkeypatch.setattr(backend, 'get_uploaded_file_record', records.get)

    class TwoInputs(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['required_inputs'][0]['file_id'] = 'file-1'
            plan['required_inputs'].append({'input_id': 'second', 'description': 'second',
                                            'file_id': 'file-2', 'path': 'second-model-path'})
            plan['steps'].append({'step_id': 2, 'description': 'Load second', 'allowed_tools': ['add_layer'],
                                  'input_refs': ['second'], 'output_refs': [], 'risk_level': 'low', 'requires_review': False})
            plan['tool_calls'].append({'step_id': 2, 'tool_name': 'add_layer',
                                      'arguments': {'path': 'second-model-path', 'layer_type': 'vector'}})
            return SimpleNamespace(content=json.dumps(plan))

    monkeypatch.setattr(planner_module, '_get_llm', lambda state: TwoInputs())
    requested = env.client.post('/api/chat/stream', json={
        'thread_id': 'whole-inputs', 'message': 'Load both', 'files': ['file-1', 'file-2']})
    plan = _sse_event(requested, 'human_review_required')['task_plan']
    assert plan['required_inputs'][1]['sha256'] == records['file-2']['sha256']
    assert env.client.post('/api/review', json={'thread_id': 'whole-inputs', 'task_id': plan['task_id'],
                                              'revision': plan['revision'], 'approved': True}).status_code == 200
    if mutation == 'missing':
        sources[1].unlink()
    else:
        sources[1].write_text('changed after approval')
    connections = []

    async def forbid_connection():
        connections.append(True)
        raise AssertionError('MCP was connected before all originals were checked')

    monkeypatch.setattr(executor_module, 'get_mcp_client', forbid_connection)
    response = env.client.post('/api/resume/whole-inputs')
    assert _sse_event(response, 'error')['message']
    assert connections == []
    assert env.calls == []
    assert env.records[plan['task_id']]['status'] == 'failed'
    env.records[plan['task_id']]['plan_revision'] += 1
    assert env.client.get('/api/state/whole-inputs').json()['can_start_task'] is False
    assert env.client.post('/api/chat/stream', json={
        'thread_id': 'whole-inputs', 'message': 'Do not replace', 'files': ['file-1'],
    }).status_code == 409
    env.records[plan['task_id']]['plan_revision'] = plan['revision']
    assert env.client.get('/api/state/whole-inputs').json()['can_start_task'] is True
    assert env.client.post('/api/resume/whole-inputs').status_code == 409
    monkeypatch.setattr(planner_module, '_get_llm', lambda state: FakeModel())
    fresh = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': 'whole-inputs', 'message': 'Load only the first input', 'files': ['file-1'],
    }), 'human_review_required')['task_plan']
    assert fresh['task_id'] != plan['task_id']
    assert connections == [] and env.calls == []
    assert env.records[plan['task_id']]['status'] == 'failed'


def test_connected_sse_preserves_all_300_output_chunks_in_order(execution_app, monkeypatch):
    async def many_events(app, initial, thread, config):
        yield backend.format_sse_event('start', {'thread_id': thread})
        for index in range(300):
            yield backend.format_sse_event('output', {'content': str(index)})
        yield backend.format_sse_event('end', {})

    monkeypatch.setattr(backend, 'stream_agent_execution', many_events)
    response = execution_app.client.post('/api/resume/many-chunks')
    events = []
    for event in response.text.strip().split('\n\n'):
        lines = event.splitlines()
        events.append((lines[0].removeprefix('event: '), json.loads(lines[1].removeprefix('data: '))))
    assert events[0][0] == 'start'
    assert [payload['content'] for name, payload in events if name == 'output'] == [str(index) for index in range(300)]
    assert events[-1][0] == 'end'


@pytest.mark.parametrize('first_approved', [True, False], ids=['approval-first', 'rejection-first'])
def test_competing_plan_decisions_accept_only_first_current_revision(execution_app, monkeypatch, first_approved):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    env = execution_app
    requested = env.client.post('/api/chat/stream', json={
        'thread_id': 'decision-race', 'message': 'Load', 'files': ['file-1']})
    plan = _sse_event(requested, 'human_review_required')['task_plan']
    first_in_save = threading.Event()
    release_first = threading.Event()
    save_record = backend.save_task_execution_record
    first_status = 'approved' if first_approved else 'rejected'

    def blocked_first_save(**record):
        if record['status'] == first_status:
            first_in_save.set()
            assert release_first.wait(5)
        save_record(**record)

    monkeypatch.setattr(backend, 'save_task_execution_record', blocked_first_save)
    identity = {'thread_id': 'decision-race', 'task_id': plan['task_id'], 'revision': plan['revision']}
    first_payload = {**identity, 'approved': first_approved, 'advise': None if first_approved else 'Revise name'}
    second_payload = {**identity, 'approved': not first_approved, 'advise': 'Revise name' if first_approved else None}
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(env.client.post, '/api/review', json=first_payload)
        assert first_in_save.wait(5)
        try:
            competing = env.client.post('/api/review', json=second_payload)
            assert competing.status_code == 409
        finally:
            release_first.set()
        assert first.result(timeout=10).status_code == 200
    assert env.records[plan['task_id']]['status'] == first_status
    # A delayed request must re-read the committed checkpoint, too.
    assert env.client.post('/api/review', json=second_payload).status_code == 409
    assert env.records[plan['task_id']]['status'] == first_status
    resumed = env.client.post('/api/resume/decision-race')
    _sse_event(resumed, 'result_review_required' if first_approved else 'human_review_required')
    assert len(env.calls) == (1 if first_approved else 0)


def test_disconnect_unblocks_full_sse_backlog_without_cancelling_producer(execution_app, monkeypatch):
    import asyncio
    from agent.tools.execution_guard import ThreadRun
    produced = []

    async def many_events(app, initial, thread, config):
        for index in range(300):
            produced.append(index)
            yield backend.format_sse_event('output', {'content': str(index)})

    monkeypatch.setattr(backend, 'stream_agent_execution', many_events)

    async def exercise():
        prior_producers = set(backend._graph_producers)
        response = await backend.resume_execution('backlogged-disconnect')
        producers = backend._graph_producers - prior_producers
        await asyncio.sleep(.01)
        assert 0 < len(produced) < 300, 'the connected transport must apply bounded backpressure'
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'method': 'POST', 'path': '/api/resume/backlogged-disconnect',
                 'headers': []}

        async def receive():
            return {'type': 'http.disconnect'}

        async def send(message):
            if message['type'] == 'http.response.start':
                raise BrokenPipeError('listener disconnected with full backlog')

        with pytest.raises(ClientDisconnect):
            await response(scope, receive, send)
        await asyncio.wait_for(asyncio.gather(*producers), timeout=2)
        assert produced == list(range(300))
        run = ThreadRun('backlogged-disconnect')
        run.release()

    asyncio.run(exercise())


@pytest.mark.parametrize('first_approved', [True, False], ids=['approval-first', 'rejection-first'])
def test_cancelled_review_request_keeps_owner_until_durable_and_checkpoint_decision_finish(
    execution_app, monkeypatch, first_approved,
):
    import asyncio
    import threading
    env = execution_app
    plan = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': 'cancelled-decision', 'message': 'Load', 'files': ['file-1']}), 'human_review_required')['task_plan']
    first_status = 'approved' if first_approved else 'rejected'
    identity = {'thread_id': 'cancelled-decision', 'task_id': plan['task_id'], 'revision': plan['revision']}
    first_payload = {**identity, 'approved': first_approved, 'advise': None if first_approved else 'Revise name'}
    opposite_payload = {**identity, 'approved': not first_approved, 'advise': 'Revise name' if first_approved else None}
    entered_save = threading.Event()
    release_save = threading.Event()
    finished_save = threading.Event()
    entered_checkpoint = threading.Event()
    release_checkpoint = threading.Event()
    save_record = backend.save_task_execution_record
    graph = env.graphs['current']
    update_checkpoint = graph.aupdate_state

    def slow_save(**record):
        if record['status'] == first_status:
            entered_save.set()
            assert release_save.wait(5)
        save_record(**record)
        if record['status'] == first_status:
            finished_save.set()

    async def slow_checkpoint(config, values, **kwargs):
        if values.get('task_plan') and values['task_plan'].review_status == first_status:
            entered_checkpoint.set()
            assert await asyncio.to_thread(release_checkpoint.wait, 5)
        return await update_checkpoint(config, values, **kwargs)

    monkeypatch.setattr(backend, 'save_task_execution_record', slow_save)
    monkeypatch.setattr(graph, 'aupdate_state', slow_checkpoint)

    async def exercise():
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                 'http_version': '1.1', 'scheme': 'http', 'method': 'POST',
                 'path': '/api/review', 'raw_path': b'/api/review', 'query_string': b'',
                 'headers': [(b'content-type', b'application/json')],
                 'server': ('testserver', 80), 'client': ('test', 1)}

        async def receive():
            return {'type': 'http.request', 'body': json.dumps(first_payload).encode(), 'more_body': False}

        async def send(message):
            pass

        request = asyncio.create_task(backend.app(scope, receive, send))
        try:
            assert await asyncio.to_thread(entered_save.wait, 2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            opposing = await asyncio.to_thread(env.client.post, '/api/review', json=opposite_payload)
            assert opposing.status_code == 409
            release_save.set()
            assert await asyncio.to_thread(entered_checkpoint.wait, 2)
            # Saving the business row is insufficient: the same owner also
            # protects the in-progress checkpoint update after HTTP cancellation.
            opposing = await asyncio.to_thread(env.client.post, '/api/review', json=opposite_payload)
            assert opposing.status_code == 409
            release_checkpoint.set()
            for _ in range(100):
                snapshot = await graph.aget_state({'configurable': {'thread_id': 'cancelled-decision'}})
                if snapshot.values['task_plan'].review_status == first_status:
                    break
                await asyncio.sleep(.01)
            assert snapshot.values['task_plan'].review_status == first_status
            assert env.records[plan['task_id']]['status'] == first_status
            opposing = await asyncio.to_thread(env.client.post, '/api/review', json=opposite_payload)
            assert opposing.status_code == 409
            assert env.records[plan['task_id']]['status'] == first_status
            assert env.calls == []
        finally:
            release_save.set()
            release_checkpoint.set()
            await asyncio.to_thread(finished_save.wait, 2)
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            await asyncio.sleep(.02)

    asyncio.run(exercise())


def test_each_manual_revision_has_two_generation_attempts_and_keeps_task_identity(monkeypatch):
    class RevisionsModel:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 2:
                return SimpleNamespace(content='not json')
            return SimpleNamespace(content=json.dumps({
                "task_id": "untrusted-model-id", "task_summary": "Inspect layers",
                "risk_level": "low", "review_status": "pending_review", "required_inputs": [],
                "expected_outputs": [],
                "steps": [{"step_id": 1, "description": "Inspect", "allowed_tools": ["get_layers"],
                           "input_refs": [], "output_refs": [], "risk_level": "low", "requires_review": False}],
                "tool_calls": [{"step_id": 1, "tool_name": "get_layers", "arguments": {},
                                "risk_level": "low", "requires_review": False}],
            }))

    model = RevisionsModel()
    graph = build_graph(MemorySaver())

    async def get_graph():
        return graph

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "save_task_execution_record", lambda **kwargs: None)
    monkeypatch.setattr(planner_module, "_get_llm", lambda state: model)
    client = TestClient(backend.app)
    first = _sse_event(client.post('/api/chat/stream', json={
        'thread_id': 'repeated-revisions', 'message': 'Inspect layers',
    }), 'human_review_required')['task_plan']
    current = first
    for revision in (2, 3):
        response = client.post('/api/review', json={
            'thread_id': 'repeated-revisions', 'task_id': current['task_id'],
            'revision': current['revision'], 'approved': False, 'advise': f'Change {revision}',
        })
        assert response.status_code == 200
        current = _sse_event(client.post('/api/resume/repeated-revisions'), 'human_review_required')['task_plan']
        assert current['task_id'] == first['task_id']
        assert current['revision'] == revision
        assert current['review_status'] == 'pending_review'
    assert model.calls == 4


def test_resume_requires_plan_and_result_decisions(execution_app):
    env = execution_app
    first = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': 'must-decide', 'message': 'Load', 'files': ['file-1'],
    }), 'human_review_required')['task_plan']
    assert env.client.post('/api/resume/must-decide').status_code == 409
    assert env.calls == []
    assert env.client.post('/api/review', json={
        'thread_id': 'must-decide', 'task_id': first['task_id'],
        'revision': first['revision'], 'approved': True,
    }).status_code == 200
    _sse_event(env.client.post('/api/resume/must-decide'), 'result_review_required')
    assert len(env.calls) == 1
    assert env.client.post('/api/resume/must-decide').status_code == 409
    assert len(env.calls) == 1


def test_duplicate_plan_decision_is_idempotent_but_opposite_is_stale(execution_app):
    env = execution_app
    response = env.client.post('/api/chat/stream', json={
        'thread_id': 'duplicate-plan', 'message': 'Load', 'files': ['file-1'],
    })
    plan = _sse_event(response, 'human_review_required')['task_plan']
    decision = {'thread_id': 'duplicate-plan', 'task_id': plan['task_id'],
                'revision': plan['revision'], 'approved': True}
    assert env.client.post('/api/review', json=decision).status_code == 200
    assert env.client.post('/api/review', json=decision).status_code == 200
    assert env.client.post('/api/review', json={**decision, 'approved': False, 'advise': 'Change'}).status_code == 409
    _sse_event(env.client.post('/api/resume/duplicate-plan'), 'result_review_required')
    assert len(env.calls) == 1
    assert env.client.post('/api/review', json=decision).status_code == 200
    assert len(env.calls) == 1


def test_result_review_is_checkpoint_bound_and_duplicate_is_idempotent(execution_app, monkeypatch):
    env = execution_app
    plan = env.approve('result-identity')
    _sse_event(env.client.post('/api/resume/result-identity'), 'result_review_required')
    pause = env.client.get('/api/state/result-identity').json()['pause']
    assert pause['kind'] == 'result_review'
    assert (pause['task_id'], pause['revision']) == (plan['task_id'], plan['revision'])
    confirmations = []
    monkeypatch.setattr(backend, 'confirm_task_execution', lambda *args: confirmations.append(args) or True)
    decision = {'thread_id': 'result-identity', 'task_id': pause['task_id'],
                'revision': pause['revision'], 'review_id': pause['review_id'], 'is_completed': True}
    assert env.client.post('/api/confirm-result', json={**decision, 'revision': pause['revision'] + 1}).status_code == 409
    assert env.client.post('/api/confirm-result', json={**decision, 'review_id': 'stale'}).status_code == 409
    assert env.client.post('/api/confirm-result', json=decision).status_code == 200
    assert env.client.post('/api/confirm-result', json=decision).status_code == 200
    assert env.client.post('/api/confirm-result', json={**decision, 'is_completed': False}).status_code == 409
    assert len(confirmations) == 1
    assert len(env.calls) == 1


def test_risk_review_is_bound_to_checkpoint_and_duplicate_never_reexecutes(risk_execution_app):
    env = risk_execution_app
    plan = env.approve('risk-identity')
    _sse_event(env.client.post('/api/resume/risk-identity'), 'risk_review_required')
    pause = env.client.get('/api/state/risk-identity').json()['pause']
    assert pause['kind'] == 'risk_review'
    assert (pause['task_id'], pause['revision']) == (plan['task_id'], plan['revision'])
    assert env.client.post('/api/resume/risk-identity').status_code == 409
    decision = {'thread_id': 'risk-identity', 'task_id': pause['task_id'],
                'revision': pause['revision'], 'review_id': pause['review_id'], 'status': 'approved'}
    assert env.client.post('/api/risk-review', json={**decision, 'task_id': 'different'}).status_code == 409
    assert env.client.post('/api/risk-review', json={**decision, 'revision': pause['revision'] + 1}).status_code == 409
    assert env.client.post('/api/risk-review', json=decision).status_code == 200
    assert env.client.post('/api/risk-review', json=decision).status_code == 200
    assert env.client.post('/api/risk-review', json={**decision, 'status': 'rejected'}).status_code == 409
    _sse_event(env.client.post('/api/resume/risk-identity'), 'result_review_required')
    assert len(env.calls) == 3
    assert env.client.post('/api/risk-review', json=decision).status_code == 200
    assert len(env.calls) == 3


@pytest.mark.parametrize('risk_choice', ['approved', 'rejected'])
def test_save_project_waits_for_specific_risk_approval(execution_app, monkeypatch, risk_choice):
    from pathlib import Path
    from agent.nodes import executor_node as executor_module
    env = execution_app

    class ProjectModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['risk_level'] = 'high'
            plan['expected_outputs'] = [{'output_id': 'project', 'description': 'New project', 'path': 'reviewed.qgz'}]
            plan['steps'].append({'step_id': 2, 'description': 'Save new project', 'allowed_tools': ['save_project'],
                                  'input_refs': [], 'output_refs': ['project'], 'risk_level': 'high', 'requires_review': True})
            plan['tool_calls'].append({'step_id': 2, 'tool_name': 'save_project', 'arguments': {'path': 'reviewed.qgz'},
                                       'risk_level': 'high', 'requires_review': True})
            return SimpleNamespace(content=json.dumps(plan))

    class ProjectGIS:
        async def list_tools(self):
            return [{'name': 'add_layer'}, {'name': 'save_project'}]

        async def call_tool(self, name, arguments):
            env.calls.append((name, arguments))
            if name == 'add_layer':
                return {'status': 'success', 'result': {'id': 'points'}}
            path = Path(arguments['path'])
            path.parent.mkdir(parents=True)
            from zipfile import ZipFile
            with ZipFile(path, 'w') as project:
                project.writestr('project.qgs', '<qgis/>')
            return {'status': 'success', 'result': {'success': True, 'path': str(path.resolve()), 'layer_count': 1}}

    async def get_gis():
        return ProjectGIS()

    monkeypatch.setattr(planner_module, '_get_llm', lambda state: ProjectModel())
    monkeypatch.setattr(executor_module, 'get_mcp_client', get_gis)
    plan = env.approve('project-review')
    assert plan['tool_calls'][1]['arguments']['path'].endswith('/project/reviewed.qgz')
    risk = _sse_event(env.client.post('/api/resume/project-review'), 'risk_review_required')
    assert risk['tool_call']['tool_name'] == 'save_project'
    assert [name for name, _ in env.calls] == ['add_layer']
    payload = {'thread_id': 'project-review', 'task_id': plan['task_id'], 'revision': plan['revision'],
               'review_id': risk['review_id'], 'status': risk_choice}
    assert env.client.post('/api/risk-review', json={**payload, 'review_id': 'stale'}).status_code == 409
    assert env.client.post('/api/risk-review', json=payload).status_code == 200
    assert env.client.post('/api/risk-review', json=payload).status_code == 200
    opposite = 'rejected' if risk_choice == 'approved' else 'approved'
    assert env.client.post('/api/risk-review', json={**payload, 'status': opposite}).status_code == 409
    response = env.client.post('/api/resume/project-review')
    _sse_event(response, 'result_review_required' if risk_choice == 'approved' else 'error')
    assert [name for name, _ in env.calls] == (['add_layer', 'save_project'] if risk_choice == 'approved' else ['add_layer'])


@pytest.mark.parametrize('failure_mode', ['persist-failure', 'unknown-response', 'disconnect'])
def test_unknown_outcome_requires_operator_evidence_and_never_replays(execution_app, monkeypatch, failure_mode):
    from copy import deepcopy
    from agent.nodes import executor_node as executor_module
    env = execution_app
    plan = env.approve('unknown-checked')
    if failure_mode == 'persist-failure':
        env.faults['after_dispatch'] = True
    else:
        class UnknownGIS:
            async def list_tools(self):
                return [{'name': 'add_layer'}]

            async def call_tool(self, name, arguments):
                env.calls.append((name, arguments))
                if len(env.calls) == 1:
                    if failure_mode == 'disconnect':
                        raise TimeoutError('QGIS connection timed out after dispatch')
                    return {'status': 'error', 'message': 'Unknown result after write', 'outcome': 'unknown'}
                return {'status': 'success', 'result': {'id': 'new-layer'}}

        async def get_unknown_gis():
            return UnknownGIS()

        monkeypatch.setattr(executor_module, 'get_mcp_client', get_unknown_gis)
    _sse_event(env.client.post('/api/resume/unknown-checked'), 'error')
    env.faults['after_dispatch'] = False
    assert env.client.get(f"/api/executions/{plan['task_id']}").json()['execution']['status'] == 'needs_attention'
    original_calls = list(env.calls)
    original_intent = deepcopy(env.records[plan['task_id']]['inflight_call'])
    original_attempts = deepcopy(env.records[plan['task_id']]['attempts'])
    state = env.client.get('/api/state/unknown-checked').json()
    assert state['next'] == (['executor_node'] if failure_mode == 'persist-failure' else ['reflector_node'])
    if failure_mode != 'persist-failure':
        assert len(original_attempts) == 1
        assert original_attempts[0]['observation']['outcome'] == 'unknown'
    assert state['can_start_task'] is False
    assert env.client.post('/api/chat/stream', json={
        'thread_id': 'unknown-checked', 'message': 'Do something else', 'files': ['file-1'],
    }).status_code == 409

    class ProbeGIS:
        async def call_tool(self, name, arguments):
            assert name == 'get_qgis_info'
            return {'status': 'success', 'result': {
                'process_id': 1234, 'instance_id': 'qgis-instance', 'profile_folder': '/test/profile'}}

    async def get_probe_gis():
        return ProbeGIS()

    def save_reconciliation(task_id, session_id, evidence):
        row = env.records[task_id]
        if row['status'] != 'needs_attention' or row['session_id'] != session_id:
            return False
        row['status'] = 'reconciled'
        row['reconciliation'] = evidence
        return True

    monkeypatch.setattr(backend, 'get_mcp_client', get_probe_gis)
    monkeypatch.setattr(backend, 'reconcile_task_execution', save_reconciliation, raising=False)
    url = f"/api/executions/{plan['task_id']}/reconciliation"
    probe = env.client.get(f'{url}/probe')
    assert probe.status_code == 200
    identity = probe.json()['qgis']
    assert identity['instance_id'] == 'qgis-instance'
    decision = {'thread_id': 'unknown-checked', 'checked': True,
                'note': 'Inspected the test profile, layer and project; no operation is running.', 'qgis': identity}
    assert env.client.post(url, json={**decision, 'checked': False}).status_code == 422
    assert env.client.post(url, json={**decision, 'qgis': {**identity, 'instance_id': 'old'}}).status_code == 409
    assert env.records[plan['task_id']]['status'] == 'needs_attention'
    assert env.client.post(url, json=decision).status_code == 200
    record = env.client.get(f"/api/executions/{plan['task_id']}").json()['execution']
    assert record['status'] == 'reconciled'
    assert record['reconciliation']['note'] == decision['note']
    assert env.client.post('/api/resume/unknown-checked').status_code == 409
    assert env.calls == original_calls
    old_row = env.records[plan['task_id']]
    for key, value in (
        ('task_id', 'other-task'), ('session_id', 'other-session'),
        ('plan_revision', plan['revision'] + 1),
        ('plan', {**old_row['plan'], 'task_summary': 'Different plan'}),
        ('reconciliation', None),
        ('reconciliation', {'note': 'Checked', 'qgis': {'process_id': 0, 'instance_id': '', 'profile_folder': ''}}),
    ):
        saved = old_row[key]
        old_row[key] = value
        assert env.client.get('/api/state/unknown-checked').json()['can_start_task'] is False
        assert env.client.post('/api/chat/stream', json={
            'thread_id': 'unknown-checked', 'message': 'Mismatch', 'files': ['file-1'],
        }).status_code == 409
        old_row[key] = saved
    assert env.client.get('/api/state/unknown-checked').json()['can_start_task'] is True
    fresh = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': 'unknown-checked', 'message': 'New task after inspection', 'files': ['file-1'],
    }), 'human_review_required')['task_plan']
    assert fresh['task_id'] != plan['task_id']
    assert env.calls == original_calls
    monkeypatch.setattr(backend, 'list_task_executions_for_session', lambda session: [])
    history = env.client.get('/api/sessions/unknown-checked/history').json()
    contents = [message['content'] for message in history['chat_messages']]
    assert contents.count('Load') == 1
    assert contents.count('New task after inspection') == 1
    assert any(plan['task_id'] in item and '未知' in item and '核查' in item and '未确认' in item
               and plan['task_summary'] in item for item in contents)
    assert history['history'][0]['task_plan']['revision'] == plan['revision']
    assert history['history'][0]['confirmed'] is None
    graph = env.graphs['current']
    env.graphs['current'] = build_graph(graph.checkpointer)
    assert env.client.get('/api/sessions/unknown-checked/history').json()['chat_messages'] == history['chat_messages']
    assert env.client.post('/api/review', json={
        'thread_id': 'unknown-checked', 'task_id': fresh['task_id'],
        'revision': fresh['revision'], 'approved': True,
    }).status_code == 200
    _sse_event(env.client.post('/api/resume/unknown-checked'), 'result_review_required')
    assert len(env.calls) == 2
    assert env.records[plan['task_id']]['status'] == 'reconciled'
    assert env.records[plan['task_id']]['inflight_call'] == original_intent
    assert env.records[plan['task_id']]['attempts'] == original_attempts


def test_duplicate_approval_while_gis_runs_returns_existing_decision(execution_app):
    from concurrent.futures import ThreadPoolExecutor
    env = execution_app
    plan = env.approve('duplicate-running')
    env.block['enabled'] = True
    payload = {'thread_id': 'duplicate-running', 'task_id': plan['task_id'],
               'revision': plan['revision'], 'approved': True}
    with ThreadPoolExecutor(max_workers=1) as workers:
        running = workers.submit(env.client.post, '/api/resume/duplicate-running')
        assert env.entered.wait(5)
        try:
            state = env.client.get('/api/state/duplicate-running').json()
            assert state['running'] is True and state['can_start_task'] is False
            assert state['continuation'] is None
            assert env.client.post('/api/review', json=payload).status_code == 200
            assert env.client.post('/api/review', json={**payload, 'approved': False, 'advise': 'Change'}).status_code == 409
        finally:
            env.release.set()
        _sse_event(running.result(timeout=10), 'result_review_required')
    assert len(env.calls) == 1


def test_new_chat_cannot_replace_a_plan_awaiting_review(execution_app):
    env = execution_app
    thread = 'protect-plan-pause'
    plan = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Load', 'files': ['file-1'],
    }), 'human_review_required')['task_plan']
    before = env.client.get(f'/api/state/{thread}').json()['pause']
    assert env.client.get(f'/api/state/{thread}').json()['can_start_task'] is False
    response = env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Another task', 'files': ['file-1'],
    })
    assert response.status_code == 409
    assert env.client.get(f'/api/state/{thread}').json()['pause'] == before
    assert env.client.post('/api/review', json={
        'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'], 'approved': True,
    }).status_code == 200
    _sse_event(env.client.post(f'/api/resume/{thread}'), 'result_review_required')
    assert len(env.calls) == 1


def test_approved_task_remains_resumable_after_gis_busy(execution_app):
    from concurrent.futures import ThreadPoolExecutor
    env = execution_app
    env.approve('busy-owner')
    plan = env.approve('busy-waiter')
    env.block['enabled'] = True
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(env.client.post, '/api/resume/busy-owner')
        assert env.entered.wait(5)
        try:
            assert env.client.post('/api/resume/busy-waiter').status_code == 409
            snapshot = env.client.get('/api/state/busy-waiter').json()
            assert snapshot['pause'] is None
            assert snapshot['can_start_task'] is False
            assert snapshot['continuation'] == {'kind': 'resume', 'task_id': plan['task_id'], 'revision': plan['revision']}
            assert env.client.post('/api/chat/stream', json={
                'thread_id': 'busy-waiter', 'message': 'Replace', 'files': ['file-1'],
            }).status_code == 409
        finally:
            env.release.set()
        _sse_event(first.result(timeout=10), 'result_review_required')
    _sse_event(env.client.post('/api/resume/busy-waiter'), 'result_review_required')
    assert len(env.calls) == 2


def test_saved_risk_and_result_decisions_project_continuation(risk_execution_app, monkeypatch):
    from agent.nodes import reflector_node as reflector_module
    env = risk_execution_app
    thread = 'continue-decisions'
    plan = env.approve(thread)
    risk = _sse_event(env.client.post(f'/api/resume/{thread}'), 'risk_review_required')
    before_risk = env.client.get(f'/api/state/{thread}').json()['pause']
    assert env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Replace risk pause', 'files': ['file-1'],
    }).status_code == 409
    after_risk = env.client.get(f'/api/state/{thread}').json()['pause']
    assert (after_risk['review_id'], after_risk['tool_call']) == (before_risk['review_id'], before_risk['tool_call'])
    assert len(env.calls) == 2
    payload = {'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'],
               'review_id': risk['review_id'], 'status': 'approved'}
    assert env.client.post('/api/risk-review', json=payload).status_code == 200
    continuation = {'kind': 'resume', 'task_id': plan['task_id'], 'revision': plan['revision']}
    assert env.client.get(f'/api/state/{thread}').json()['continuation'] == continuation
    _sse_event(env.client.post(f'/api/resume/{thread}'), 'result_review_required')
    pause = env.client.get(f'/api/state/{thread}').json()['pause']
    assert env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Replace result pause', 'files': ['file-1'],
    }).status_code == 409
    assert env.client.get(f'/api/state/{thread}').json()['pause'] == pause
    monkeypatch.setattr(backend, 'confirm_task_execution', lambda *args: True)
    monkeypatch.setattr(reflector_module, '_get_llm', lambda state: FakeModel())
    assert env.client.post('/api/confirm-result', json={
        'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'],
        'review_id': pause['review_id'], 'is_completed': True,
    }).status_code == 200
    assert env.client.get(f'/api/state/{thread}').json()['continuation'] == continuation
    assert env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Replace', 'files': ['file-1'],
    }).status_code == 409
    _sse_event(env.client.post(f'/api/resume/{thread}'), 'complete')
    assert env.client.get(f'/api/state/{thread}').json()['continuation'] is None
    assert len(env.calls) == 3


def test_named_sessions_keep_name_and_created_order_after_reload(monkeypatch):
    graph = build_graph(MemorySaver())
    ids = []

    async def get_graph():
        return graph

    async def list_ids(app):
        return list(ids)

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_graph)
    monkeypatch.setattr(backend, 'fetch_checkpoint_thread_ids', list_ids)
    monkeypatch.setattr(backend, 'sessions', {})
    client = TestClient(backend.app)
    first = client.post('/api/sessions', json={'session_name': '先前任务'}).json()
    ids.append(first['thread_id'])
    second = client.post('/api/sessions', json={'session_name': '恢复验证'}).json()
    ids.append(second['thread_id'])
    listed = client.get('/api/sessions').json()
    assert listed == [second, first]
    backend.sessions.clear()
    assert client.get('/api/sessions').json() == [second, first]
    assert client.get('/api/sessions').json() == [second, first]

    class MetadataModel(FakeModel):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            plan = json.loads(response.content)
            plan['required_inputs'] = []
            plan['steps'][0].update(allowed_tools=['get_layers'], input_refs=[])
            plan['tool_calls'][0].update(tool_name='get_layers', arguments={})
            return SimpleNamespace(content=json.dumps(plan))

    monkeypatch.setattr(planner_module, '_get_llm', lambda state: MetadataModel())
    _sse_event(client.post('/api/chat/stream', json={
        'thread_id': first['thread_id'], 'message': 'Inspect current layers',
    }), 'human_review_required')
    backend.sessions.clear()
    assert client.get('/api/sessions').json() == [second, first]


def test_new_chat_holds_thread_owner_while_reading_checkpoint(execution_app, monkeypatch):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    env = execution_app
    thread = 'chat-review-race'
    plan = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Load', 'files': ['file-1'],
    }), 'human_review_required')['task_plan']
    graph = env.graphs['current']
    entered, release = threading.Event(), threading.Event()
    block_once = {'value': True}

    class DelayedGraph:
        def __getattr__(self, name):
            return getattr(graph, name)

        async def aget_state(self, config):
            if config['configurable']['thread_id'] == thread and block_once['value']:
                block_once['value'] = False
                entered.set()
                assert await asyncio.to_thread(release.wait, 5)
            return await graph.aget_state(config)

    async def get_delayed_graph():
        return DelayedGraph()

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_delayed_graph)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(env.client.post, '/api/chat/stream', json={
            'thread_id': thread, 'message': 'Replace', 'files': ['file-1'],
        })
        assert entered.wait(5)
        try:
            assert env.client.post('/api/review', json={
                'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'], 'approved': True,
            }).status_code == 409
        finally:
            release.set()
        assert pending.result(timeout=10).status_code == 409
    assert env.client.get(f'/api/state/{thread}').json()['pause']['task_id'] == plan['task_id']
    assert env.calls == []


def test_rejected_plan_with_saved_decision_can_be_continued(execution_app):
    env = execution_app
    thread = 'continue-revision'
    plan = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Load', 'files': ['file-1'],
    }), 'human_review_required')['task_plan']
    assert env.client.post('/api/review', json={
        'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'],
        'approved': False, 'advise': 'Change the name',
    }).status_code == 200
    state = env.client.get(f'/api/state/{thread}').json()
    assert state['continuation'] == {'kind': 'resume', 'task_id': plan['task_id'], 'revision': plan['revision']}
    assert state['can_start_task'] is False
    revised = _sse_event(env.client.post(f'/api/resume/{thread}'), 'human_review_required')['task_plan']
    assert revised['task_id'] == plan['task_id'] and revised['revision'] == 2
    assert env.calls == []


def test_resume_owns_thread_before_reading_checkpoint(execution_app, monkeypatch):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    env = execution_app
    env.approve('resume-read-race')
    graph = env.graphs['current']
    entered, release = threading.Event(), threading.Event()
    block_once = {'value': True}

    class DelayedGraph:
        def __getattr__(self, name):
            return getattr(graph, name)

        async def aget_state(self, config):
            if config['configurable']['thread_id'] == 'resume-read-race' and block_once['value']:
                block_once['value'] = False
                entered.set()
                assert await asyncio.to_thread(release.wait, 5)
            return await graph.aget_state(config)

    async def get_delayed_graph():
        return DelayedGraph()

    monkeypatch.setattr(backend, 'get_app_with_checkpointer', get_delayed_graph)
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(env.client.post, '/api/resume/resume-read-race')
        assert entered.wait(5)
        try:
            assert env.client.post('/api/resume/resume-read-race').status_code == 409
        finally:
            release.set()
        _sse_event(first.result(timeout=10), 'result_review_required')
    assert len(env.calls) == 1


def test_rejected_risk_finishes_without_confirmation_and_allows_new_task_in_session(risk_execution_app):
    env = risk_execution_app
    thread = 'risk-rejected-next-task'
    first = env.approve(thread)
    risk = _sse_event(env.client.post(f'/api/resume/{thread}'), 'risk_review_required')
    assert env.client.post('/api/risk-review', json={
        'thread_id': thread, 'task_id': first['task_id'], 'revision': first['revision'],
        'review_id': risk['review_id'], 'status': 'rejected',
    }).status_code == 200
    _sse_event(env.client.post(f'/api/resume/{thread}'), 'error')
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas']
    assert env.records[first['task_id']]['status'] == 'failed'
    assert env.records[first['task_id']]['confirmed'] is None
    state = env.client.get(f'/api/state/{thread}').json()
    assert state['pause'] is None and state['continuation'] is None
    assert state['state']['is_completed'] is None
    assert state['can_start_task'] is True
    response = env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Try a separate task', 'files': ['file-1'],
    })
    second = _sse_event(response, 'human_review_required')['task_plan']
    assert second['task_id'] != first['task_id']
    assert second['review_status'] == 'pending_review'
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas']
    assert env.records[first['task_id']]['status'] == 'failed'
    assert env.records[first['task_id']]['confirmed'] is None


@pytest.mark.parametrize('mutation', ['task', 'session', 'revision', 'plan', 'confirmed', 'inflight', 'unknown',
                                     'missing-write-evidence', 'missing-record', 'database-unavailable'])
def test_failed_task_cannot_unlock_session_with_mismatched_or_uncertain_facts(risk_execution_app, monkeypatch, mutation):
    env = risk_execution_app
    thread = f'failed-facts-{mutation}'
    plan = env.approve(thread)
    risk = _sse_event(env.client.post(f'/api/resume/{thread}'), 'risk_review_required')
    assert env.client.post('/api/risk-review', json={
        'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'],
        'review_id': risk['review_id'], 'status': 'rejected',
    }).status_code == 200
    _sse_event(env.client.post(f'/api/resume/{thread}'), 'error')
    row = env.records[plan['task_id']]
    if mutation == 'task':
        row['task_id'] = 'different-task'
    elif mutation == 'session':
        row['session_id'] = 'different-session'
    elif mutation == 'revision':
        row['plan_revision'] += 1
    elif mutation == 'plan':
        row['plan'] = {**row['plan'], 'task_summary': 'Different plan'}
    elif mutation == 'confirmed':
        row['confirmed'] = True
    elif mutation == 'inflight':
        row['inflight_call'] = {'tool_name': 'save_project'}
    elif mutation == 'unknown':
        row['attempts'][0]['observation']['outcome'] = 'unknown'
    elif mutation == 'missing-write-evidence':
        row['attempts'][0]['observation']['outcome'] = None
    elif mutation == 'missing-record':
        monkeypatch.setattr(backend, 'get_task_execution_record', lambda task_id: None)
    else:
        def unavailable(task_id):
            raise RuntimeError('database unavailable')
        monkeypatch.setattr(backend, 'get_task_execution_record', unavailable)
    assert env.client.get(f'/api/state/{thread}').json()['can_start_task'] is False
    assert env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'Replace', 'files': ['file-1'],
    }).status_code == 409
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas']


def test_terminal_fact_is_checked_under_thread_owner(risk_execution_app, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    env = risk_execution_app
    thread = 'terminal-fact-owner'
    plan = env.approve(thread)
    risk = _sse_event(env.client.post(f'/api/resume/{thread}'), 'risk_review_required')
    assert env.client.post('/api/risk-review', json={
        'thread_id': thread, 'task_id': plan['task_id'], 'revision': plan['revision'],
        'review_id': risk['review_id'], 'status': 'rejected',
    }).status_code == 200
    _sse_event(env.client.post(f'/api/resume/{thread}'), 'error')
    entered, release = threading.Event(), threading.Event()
    query = backend.get_task_execution_record

    def delayed_query(task_id):
        entered.set()
        assert release.wait(5)
        return query(task_id)

    monkeypatch.setattr(backend, 'get_task_execution_record', delayed_query)
    payload = {'thread_id': thread, 'message': 'New task', 'files': ['file-1']}
    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(env.client.post, '/api/chat/stream', json=payload)
        assert entered.wait(5)
        try:
            assert env.client.post(f'/api/resume/{thread}').status_code == 409
            assert env.client.post('/api/chat/stream', json=payload).status_code == 409
        finally:
            release.set()
        new_plan = _sse_event(first.result(timeout=10), 'human_review_required')['task_plan']
    assert new_plan['task_id'] != plan['task_id']
    assert [name for name, _ in env.calls] == ['add_layer', 'capture_map_canvas']


def test_new_task_keeps_previous_rejected_risk_visible_in_session_history(risk_execution_app, monkeypatch):
    env = risk_execution_app
    thread = 'terminal-history-visible'
    first = env.approve(thread)
    risk = _sse_event(env.client.post(f'/api/resume/{thread}'), 'risk_review_required')
    assert env.client.post('/api/risk-review', json={
        'thread_id': thread, 'task_id': first['task_id'], 'revision': first['revision'],
        'review_id': risk['review_id'], 'status': 'rejected',
    }).status_code == 200
    _sse_event(env.client.post(f'/api/resume/{thread}'), 'error')
    original_calls = list(env.calls)
    monkeypatch.setattr(backend, 'list_task_executions_for_session', lambda session: [])
    second = _sse_event(env.client.post('/api/chat/stream', json={
        'thread_id': thread, 'message': 'New request after rejection', 'files': ['file-1'],
    }), 'human_review_required')['task_plan']
    assert second['task_id'] != first['task_id']
    assert env.calls == original_calls
    history = env.client.get(f'/api/sessions/{thread}/history').json()
    contents = [message['content'] for message in history['chat_messages']]
    assert contents.count('Load') == 1
    assert contents.count('New request after rejection') == 1
    assert any(first['task_id'] in item and '失败' in item and '未确认' in item for item in contents)
    assert history['history'][0]['task_plan']['task_id'] == first['task_id']
    assert history['history'][0]['confirmed'] is None
    graph = env.graphs['current']
    env.graphs['current'] = build_graph(graph.checkpointer)
    assert env.client.get(f'/api/sessions/{thread}/history').json()['chat_messages'] == history['chat_messages']
