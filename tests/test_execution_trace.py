from datetime import UTC, datetime

from agent.tools.tool_contracts import (
    ExecutionTrace,
    StepAttempt,
    ToolCallPlan,
    ToolObservation,
)


def _successful_attempt() -> StepAttempt:
    started_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    finished_at = datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC)
    observation = ToolObservation(
        tool_name="capture_map_canvas",
        status="success",
        data={"path": "shared/screenshots/map.png"},
        artifacts=[{"kind": "path", "path": "shared/screenshots/map.png"}],
        raw_response={
            "status": "success",
            "result": {"path": "shared/screenshots/map.png"},
        },
    )
    return StepAttempt(
        attempt_id="attempt-1",
        step_id=1,
        tool_call=ToolCallPlan(
            step_id=1,
            tool_name="capture_map_canvas",
            arguments={"path": "shared/screenshots/map.png"},
        ),
        observation=observation,
        status="success",
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=1000,
        artifacts=observation.artifacts,
        metadata={"source": "unit-test"},
    )


def test_execution_trace_appends_attempt_and_tracks_latest_status():
    trace = ExecutionTrace(trace_id="trace-1", task_id="task-1")
    attempt = _successful_attempt()

    trace.append_attempt(attempt)

    assert trace.status == "success"
    assert trace.latest_attempt() == attempt
    assert trace.attempts == [attempt]


def test_execution_trace_failed_attempt_sets_failed_status():
    trace = ExecutionTrace(trace_id="trace-1")
    attempt = _successful_attempt().model_copy(update={"status": "failed"})

    trace.append_attempt(attempt)

    assert trace.status == "failed"
    assert trace.latest_attempt() == attempt


def test_execution_trace_json_round_trip_preserves_replay_fields():
    trace = ExecutionTrace(
        trace_id="trace-1",
        task_id="task-1",
        metadata={"session_id": "session-1"},
    )
    trace.append_attempt(_successful_attempt())

    reloaded = ExecutionTrace.model_validate_json(trace.model_dump_json())

    assert reloaded.trace_id == "trace-1"
    assert reloaded.task_id == "task-1"
    assert reloaded.status == "success"
    assert reloaded.metadata == {"session_id": "session-1"}
    assert reloaded.latest_attempt().attempt_id == "attempt-1"
    assert reloaded.latest_attempt().tool_call.tool_name == "capture_map_canvas"
    assert reloaded.latest_attempt().observation.raw_response == {
        "status": "success",
        "result": {"path": "shared/screenshots/map.png"},
    }
    assert reloaded.latest_attempt().artifacts == [
        {"kind": "path", "path": "shared/screenshots/map.png"}
    ]
