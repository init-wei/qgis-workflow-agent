"""Planning provenance is observable through HTTP without live model or GIS calls."""

import copy
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
from langgraph.checkpoint.memory import MemorySaver
import pytest

from agent.graph import build_graph
from agent.nodes import executor_node as executor_module
from agent.nodes import planner_node as planner_module
from agent.state import create_initial_state
from conftest import fake_spatial_inspector
import ui.backend.main as backend


def _proposal():
    return {
        "task_id": "model-task", "task_summary": "Load selected vector",
        "risk_level": "low", "review_status": "pending_review",
        "required_inputs": [{"input_id": "source", "description": "Selected input", "file_id": "source",
                             "path": "model-path"}],
        "expected_outputs": [],
        "steps": [{"step_id": 1, "description": "Load selected vector", "allowed_tools": ["add_layer"],
                   "input_refs": ["source"], "output_refs": [], "risk_level": "low", "requires_review": False}],
        "tool_calls": [{"step_id": 1, "tool_name": "add_layer", "arguments": {
            "path": "model-path", "layer_type": "vector", "name": "Selected vector"}}],
        "metadata": {},
    }


class ScriptedModel:
    def __init__(self):
        self.outcomes = []
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        assert self.outcomes, "Unexpected extra model generation"
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(content=json.dumps(result) if isinstance(result, dict) else result)


@pytest.fixture(params=["false", "true"], ids=["A0", "A1"])
def workflow(monkeypatch, tmp_path, request):
    """Own every external seam while retaining the production graph and HTTP routes."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QGIS_AGENT_SKILLS_ENABLED", request.param)
    monkeypatch.setenv("QGIS_AGENT_LANGSMITH_ENABLED", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    source = tmp_path / "source.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}')
    record = {"file_id": "source", "filename": source.name, "storage_path": source.name,
              "layers": [{"layer_name": "source", "crs": "EPSG:32650", "geometry": "Point", "is_2d": True}]}
    model = ScriptedModel()
    spatial = {"inspect": fake_spatial_inspector}

    def processing_inspector(requests):
        assert requests == {"capabilities": True}
        return {"status": "success", "result": {"processing_capabilities": []}}

    graph = build_graph(MemorySaver(), processing_inspector=processing_inspector,
                        spatial_inspector=lambda request: spatial["inspect"](request))

    async def get_graph():
        return graph

    async def forbid_gis():
        pytest.fail("Unapproved planning must not connect to MCP or execute GIS")

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", lambda _: record)
    monkeypatch.setattr(backend, "save_task_execution_record", lambda **_: None)
    monkeypatch.setattr(backend, "get_task_execution_record", lambda _: None)
    monkeypatch.setattr(planner_module, "_get_llm", lambda _: model)
    monkeypatch.setattr(executor_module, "get_mcp_client", forbid_gis)
    return SimpleNamespace(client=TestClient(backend.app), graph=graph, model=model, spatial=spatial, root=tmp_path,
                           real_get_llm=REAL_GET_LLM)


REAL_GET_LLM = planner_module._get_llm


def _submit(workflow, outcomes, *, thread="planning", message="Load selected vector"):
    workflow.model.outcomes = list(outcomes)
    response = workflow.client.post("/api/chat/stream", json={
        "thread_id": thread, "message": message, "files": ["source"],
    })
    assert response.status_code == 200
    snapshot = workflow.client.get(f"/api/state/{thread}")
    assert snapshot.status_code == 200
    assert not (workflow.root / "shared" / "outputs").exists()
    return response, snapshot.json()


def _diagnostic(attempt, kind, received):
    return {"attempt": attempt, "failure_kind": kind, "model_response_received": received}


@pytest.mark.parametrize("content", ["{bad-json", {"task_summary": 42}], ids=["json", "schema"])
def test_model_output_rejection_is_structured_and_exhausts_only_two_attempts(workflow, content):
    response, snapshot = _submit(workflow, [content, content])
    state = snapshot["state"]
    assert "event: error" in response.text
    assert "human_review_required" not in response.text
    assert state["task_plan"] is None
    assert state["planning_failure_kind"] == "invalid_plan"
    assert state["planning_error"]
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, "invalid_plan", True), _diagnostic(2, "invalid_plan", True),
    ]
    assert state["retry_count"] == workflow.model.calls == 2
    assert snapshot["next"] == []
    assert snapshot["pause"] is None and snapshot["continuation"] is None


def test_pure_reference_validation_is_invalid_plan(workflow):
    proposal = _proposal()
    proposal["steps"][0]["input_refs"] = ["source", "missing"]
    _, snapshot = _submit(workflow, [proposal, proposal])
    state = snapshot["state"]
    assert state["planning_failure_kind"] == "invalid_plan"
    assert "unknown_input_reference" in state["planning_error"]
    assert state["plan_validation"]["is_valid"] is False


def test_provider_http_timeout_is_environment_failure(workflow):
    failure = httpx.ReadTimeout("invalid plan in provider text is not model evidence")
    _, snapshot = _submit(workflow, [failure, failure])
    state = snapshot["state"]
    assert state["task_plan"] is None
    assert state["planning_failure_kind"] == "environment_error"
    assert state["planning_error"] == str(failure)
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, "environment_error", False), _diagnostic(2, "environment_error", False),
    ]
    assert state["retry_count"] == workflow.model.calls == 2


def test_untyped_provider_error_is_unknown_without_a_response(workflow):
    failure = ValueError("invalid plan is only an arbitrary provider message")
    _, snapshot = _submit(workflow, [failure, failure])
    state = snapshot["state"]
    assert state["planning_failure_kind"] == "unknown"
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, "unknown", False), _diagnostic(2, "unknown", False),
    ]


def test_missing_key_is_queryable_without_a_model_request(workflow, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(planner_module, "_get_llm", workflow.real_get_llm)
    _, snapshot = _submit(workflow, [])
    state = snapshot["state"]
    assert state["task_plan"] is None
    assert state["planning_failure_kind"] == "environment_error"
    assert "OPENAI_API_KEY is not configured" in state["planning_error"]
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, "environment_error", False), _diagnostic(2, "environment_error", False),
    ]
    assert state["retry_count"] == 2
    assert workflow.model.calls == 0


@pytest.mark.parametrize("failure", [
    ValueError("invalid plan: GDAL inspector missing"),
    {"status": "failed", "checks": [], "errors": ["No module named osgeo"], "facts": {}},
], ids=["value-error", "failed-report"])
def test_untyped_preflight_failure_is_unknown_even_when_text_looks_specific(workflow, failure):
    def inspect(_request):
        if isinstance(failure, Exception):
            raise failure
        return failure

    workflow.spatial["inspect"] = inspect
    _, snapshot = _submit(workflow, [_proposal(), _proposal()])
    state = snapshot["state"]
    assert state["planning_failure_kind"] == "unknown"
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, "unknown", True), _diagnostic(2, "unknown", True),
    ]


def test_inspector_start_failure_keeps_its_environment_cause(workflow):
    def inspect(_request):
        raise OSError("Inspector executable unavailable")

    workflow.spatial["inspect"] = inspect
    _, snapshot = _submit(workflow, [_proposal(), _proposal()])
    state = snapshot["state"]
    assert state["planning_failure_kind"] == "environment_error"
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, "environment_error", True), _diagnostic(2, "environment_error", True),
    ]


def test_parameter_rejection_before_generation_is_not_a_model_failure(workflow):
    _, snapshot = _submit(workflow, [], message="重投影到 EPSG:32650 或 EPSG:32651")
    state = snapshot["state"]
    assert state["task_plan"] is None
    assert state["planning_failure_kind"] == "request_rejected"
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, "request_rejected", False), _diagnostic(2, "request_rejected", False),
    ]
    assert workflow.model.calls == 0
    assert snapshot["next"] == []


@pytest.mark.parametrize(("first", "kind", "received"), [
    ("{bad-json", "invalid_plan", True),
    (httpx.ReadTimeout("provider unavailable"), "environment_error", False),
], ids=["json-then-valid", "timeout-then-valid"])
def test_successful_retry_preserves_first_attempt_source(workflow, first, kind, received):
    response, snapshot = _submit(workflow, [first, _proposal()])
    state = snapshot["state"]
    assert "human_review_required" in response.text
    assert state["planning_error"] is None
    assert state["planning_failure_kind"] is None
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, kind, received), _diagnostic(2, None, True),
    ]
    assert state["task_plan"]["review_status"] == "pending_review"
    assert snapshot["pause"]["kind"] == "plan_review"


@pytest.mark.parametrize(("outcomes", "kinds", "received"), [
    ([httpx.ReadTimeout("provider unavailable"), "{bad-json"], ["environment_error", "invalid_plan"], [False, True]),
    (["{bad-json", httpx.ReadTimeout("provider unavailable")], ["invalid_plan", "environment_error"], [True, False]),
], ids=["timeout-then-json", "json-then-timeout"])
def test_final_error_does_not_overwrite_first_attempt(workflow, outcomes, kinds, received):
    _, snapshot = _submit(workflow, outcomes)
    state = snapshot["state"]
    assert state["planning_failure_kind"] == kinds[-1]
    assert state["planning_attempt_diagnostics"] == [
        _diagnostic(1, kinds[0], received[0]), _diagnostic(2, kinds[1], received[1]),
    ]


def test_model_metadata_cannot_forge_diagnostics(workflow):
    proposal = _proposal()
    proposal["metadata"] = {
        "planning_failure_kind": "environment_error",
        "planning_attempt_diagnostics": [_diagnostic(1, "environment_error", False)],
    }
    _, snapshot = _submit(workflow, [proposal])
    state = snapshot["state"]
    assert state["planning_failure_kind"] is None
    assert state["planning_attempt_diagnostics"] == [_diagnostic(1, None, True)]
    assert "planning_attempt_diagnostics" not in state["task_plan"]["metadata"]


def test_legacy_checkpoint_does_not_invent_first_attempt_provenance(workflow):
    legacy = create_initial_state("legacy", "Show current layers", task_id="legacy-task")
    legacy.pop("planning_failure_kind")
    legacy.pop("planning_attempt_diagnostics")
    legacy.update(retry_count=1, planning_error="legacy text cannot prove invalid_plan")
    # Seed an old checkpoint at the existing planner continuation, then exercise
    # only public query/resume operations to verify migration-free behavior.
    workflow.graph.update_state({"configurable": {"thread_id": "legacy"}}, legacy, as_node="human_review_node")
    before = workflow.client.get("/api/state/legacy").json()["state"]
    assert before.get("planning_failure_kind") is None
    assert not before.get("planning_attempt_diagnostics")
    proposal = _proposal()
    proposal["required_inputs"] = []
    proposal["steps"][0].update(allowed_tools=["get_layers"], input_refs=[])
    proposal["tool_calls"][0].update(tool_name="get_layers", arguments={})
    workflow.model.outcomes = [proposal]
    response = workflow.client.post("/api/resume/legacy")
    assert "human_review_required" in response.text
    after = workflow.client.get("/api/state/legacy").json()["state"]
    assert after["planning_failure_kind"] is None
    assert after["planning_attempt_diagnostics"] == [_diagnostic(2, None, True)]
    assert after["task_id"] == "legacy-task" and after["retry_count"] == 2


def test_same_thread_new_task_clears_previous_diagnostics(workflow):
    _, first = _submit(workflow, ["{bad-json", "{bad-json"])
    response, second = _submit(workflow, [_proposal()])
    assert "human_review_required" in response.text
    assert second["state"]["task_id"] != first["state"]["task_id"]
    assert second["state"]["planning_failure_kind"] is None
    assert second["state"]["planning_attempt_diagnostics"] == [_diagnostic(1, None, True)]
    assert second["state"]["retry_count"] == 1


def test_rejection_clears_diagnostics_before_new_revision(workflow):
    _, initial = _submit(workflow, ["{bad-json", _proposal()])
    original = initial["state"]["task_plan"]
    response = workflow.client.post("/api/review", json={
        "thread_id": "planning", "task_id": original["task_id"], "revision": original["revision"],
        "approved": False, "advise": "Change the display name",
    })
    assert response.status_code == 200
    rejected = workflow.client.get("/api/state/planning").json()["state"]
    assert rejected["planning_failure_kind"] is None
    assert rejected["planning_attempt_diagnostics"] == []
    assert rejected["retry_count"] == 0
    assert rejected["planning_revision"] == original["revision"] + 1
    workflow.model.outcomes = [_proposal()]
    resumed = workflow.client.post("/api/resume/planning")
    assert "human_review_required" in resumed.text
    revised = workflow.client.get("/api/state/planning").json()["state"]
    assert revised["task_plan"]["task_id"] == original["task_id"]
    assert revised["task_plan"]["revision"] == original["revision"] + 1
    assert revised["planning_attempt_diagnostics"] == [_diagnostic(1, None, True)]


def test_filesystem_safety_report_is_not_assumed_to_be_model_error(workflow):
    proposal = _proposal()
    proposal["expected_outputs"] = [{"output_id": "screenshot", "description": "Screenshot", "path": "outside.png"}]
    proposal["steps"].append({"step_id": 2, "description": "Capture", "allowed_tools": ["capture_map_canvas"],
                              "input_refs": [], "output_refs": ["screenshot"],
                              "risk_level": "low", "requires_review": False})
    proposal["tool_calls"].append({"step_id": 2, "tool_name": "capture_map_canvas",
                                   "arguments": {"path": "outside.png"}})
    # An absolute path outside the controlled output tree proves the safety
    # report's mixed-origin code, without creating any output directory.
    proposal["expected_outputs"][0]["path"] = str(workflow.root / "outside.png")
    proposal["tool_calls"][1]["arguments"]["path"] = str(workflow.root / "outside.png")
    _, snapshot = _submit(workflow, [copy.deepcopy(proposal), proposal])
    state = snapshot["state"]
    assert "unsafe_output_path" in state["planning_error"]
    assert state["planning_failure_kind"] == "unknown"
