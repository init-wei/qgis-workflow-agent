from fastapi.testclient import TestClient

from agent.tools.tool_contracts import ExecutionTrace
from ui.backend.main import app


client = TestClient(app)


def test_demo_cases_endpoint_lists_fixture_summaries():
    response = client.get("/api/demo-cases")

    assert response.status_code == 200
    payload = response.json()
    assert [case["case_id"] for case in payload["cases"]] == [
        "simple_success",
        "failure_repair",
        "skill_draft_preview",
    ]
    assert payload["cases"][0]["trace_status"] == "success"


def test_demo_case_trace_endpoint_returns_serializable_trace():
    response = client.get("/api/demo-cases/failure_repair/trace")

    assert response.status_code == 200
    payload = response.json()
    trace = ExecutionTrace.model_validate(payload["trace"])
    assert trace.trace_id == "demo-failure-repair"
    assert [attempt.status for attempt in trace.attempts] == ["failed", "success"]


def test_demo_case_detail_endpoint_includes_replay_summary():
    response = client.get("/api/demo-cases/skill_draft_preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["case"]["case_id"] == "skill_draft_preview"
    assert payload["replay"]["status"] == "success"
    assert payload["replay"]["skill_draft"]["is_registered"] is False


def test_unknown_demo_case_endpoint_returns_404():
    response = client.get("/api/demo-cases/not-found")

    assert response.status_code == 404
