import json
from pathlib import Path

from agent.tools.export_geotool_trace import (
    GeoToolTraceSeed,
    build_geotool_trace_seed,
    export_geotool_trace_jsonl,
)
from datetime import UTC, datetime

from agent.tools.tool_contracts import (
    ExecutionTrace,
    StepAttempt,
    ToolCallPlan,
    ToolObservation,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "geotool_interop"


def _attempt(status="success", error_type=None, repair=False):
    metadata = {}
    if repair:
        metadata["repair_action"] = {
            "action_type": "update_arguments",
            "reason": "parameter_repair_arguments_available",
            "updated_arguments": {"path": "shared/uploads/points.geojson"},
        }
    return StepAttempt(
        attempt_id=f"attempt-{status}",
        step_id=1,
        tool_call=ToolCallPlan(
            step_id=1,
            tool_name="add_layer",
            arguments={"path": "shared/uploads/points.geojson"},
        ),
        observation=ToolObservation(
            tool_name="add_layer",
            status=status if status == "success" else "error",
            error_type=error_type,
            message="ok" if status == "success" else "missing path",
        ),
        status=status if status == "success" else "failed",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        duration_ms=1,
        artifacts=[{"path": "shared/uploads/points.geojson"}] if status == "success" else [],
        metadata=metadata,
    )


def test_build_geotool_trace_seed_contains_observations_and_repair_actions():
    trace = ExecutionTrace(trace_id="trace-repair", task_id="task-1")
    trace.append_attempt(_attempt(status="failed", error_type="parameter_error", repair=True))
    trace.append_attempt(_attempt())

    seed = build_geotool_trace_seed(trace, user_query="加载图层")

    assert seed.trace_id == "trace-repair"
    assert seed.user_query == "加载图层"
    assert seed.final_status == "success"
    assert seed.tool_call_plan["calls"][0]["tool"] == "add_layer"
    assert seed.tool_call_plan["calls"][0]["step_id"] == "1"
    assert seed.observations[0]["tool"] == "add_layer"
    assert "tool_name" not in seed.observations[0]
    assert seed.observations[0]["status"] == "failed"
    assert seed.observations[0]["error_type"] == "parameter_error"
    assert seed.repair_actions[0]["action_type"] == "update_args"
    assert seed.repair_actions[0]["metadata"]["qgis_reason"] == (
        "parameter_repair_arguments_available"
    )


def test_export_geotool_trace_jsonl_writes_readable_file(tmp_path):
    seeds = [
        GeoToolTraceSeed(
            trace_id="trace-success-1",
            task_id="task-1",
            user_query="加载图层",
            task_plan={"task_id": "task-1"},
            tool_call_plan={
                "task_id": "task-1",
                "calls": [
                    {
                        "call_id": "call-1",
                        "step_id": "1",
                        "tool": "add_layer",
                        "args": {},
                    }
                ],
            },
            observations=[{"call_id": "call-1", "tool": "add_layer", "status": "success"}],
            repair_actions=[],
            final_status="success",
        ),
        GeoToolTraceSeed(
            trace_id="trace-failed-1",
            task_id="task-2",
            user_query="加载缺失文件",
            task_plan={"task_id": "task-2"},
            tool_call_plan={
                "task_id": "task-2",
                "calls": [
                    {
                        "call_id": "call-2",
                        "step_id": "1",
                        "tool": "add_layer",
                        "args": {},
                    }
                ],
            },
            observations=[
                {
                    "call_id": "call-2",
                    "tool": "add_layer",
                    "status": "failed",
                    "error_type": "missing_file",
                }
            ],
            repair_actions=[],
            final_status="failed",
        ),
    ]

    output_path = export_geotool_trace_jsonl(seeds, tmp_path / "seed.jsonl")

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["metadata"]["source_project"] == "qgis-workflow-agent"
    assert rows[1]["observations"][0]["error_type"] == "missing_file"


def test_export_geotool_trace_jsonl_supports_q0_seed_batch(tmp_path):
    seeds = [
        GeoToolTraceSeed(
            trace_id=f"trace-success-{index}",
            task_id=f"task-success-{index}",
            user_query=f"success task {index}",
            task_plan={"task_id": f"task-success-{index}"},
            tool_call_plan={
                "task_id": f"task-success-{index}",
                "calls": [
                    {
                        "call_id": f"call-success-{index}",
                        "step_id": "1",
                        "tool": "add_layer",
                        "args": {},
                    }
                ],
            },
            observations=[
                {
                    "call_id": f"call-success-{index}",
                    "tool": "add_layer",
                    "status": "success",
                }
            ],
            repair_actions=[],
            final_status="success",
        )
        for index in range(1, 4)
    ] + [
        GeoToolTraceSeed(
            trace_id="trace-repaired-1",
            task_id="task-repaired-1",
            user_query="parameter error then repair",
            task_plan={"task_id": "task-repaired-1"},
            tool_call_plan={
                "task_id": "task-repaired-1",
                "calls": [
                    {
                        "call_id": "call-repaired-1",
                        "step_id": "1",
                        "tool": "add_layer",
                        "args": {},
                    }
                ],
            },
            observations=[
                {
                    "call_id": "call-repaired-1",
                    "tool": "add_layer",
                    "status": "failed",
                    "error_type": "parameter_error",
                },
                {
                    "call_id": "call-repaired-1",
                    "tool": "add_layer",
                    "status": "success",
                },
            ],
            repair_actions=[
                {
                    "task_id": "task-repaired-1",
                    "failed_call_id": "call-repaired-1",
                    "error_type": "parameter_error",
                    "observed_error": "missing path",
                    "diagnosis": "path was omitted",
                    "action_type": "update_args",
                    "updated_args": {"path": "shared/uploads/points.geojson"},
                }
            ],
            final_status="success",
        ),
        GeoToolTraceSeed(
            trace_id="trace-failed-2",
            task_id="task-failed-2",
            user_query="missing file needs review",
            task_plan={"task_id": "task-failed-2"},
            tool_call_plan={
                "task_id": "task-failed-2",
                "calls": [
                    {
                        "call_id": "call-failed-2",
                        "step_id": "1",
                        "tool": "add_layer",
                        "args": {},
                    }
                ],
            },
            observations=[
                {
                    "call_id": "call-failed-2",
                    "tool": "add_layer",
                    "status": "failed",
                    "error_type": "missing_file",
                }
            ],
            repair_actions=[],
            final_status="failed",
        ),
    ]

    output_path = export_geotool_trace_jsonl(seeds, tmp_path / "seed.jsonl")

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5
    assert sum(1 for row in rows if row["final_status"] == "success") == 4
    assert sum(1 for row in rows if row["observations"][0].get("error_type")) == 2


def test_built_trace_seed_matches_v0_1_fixture_shape():
    fixture = json.loads(
        (FIXTURE_DIR / "execution_trace_success_v0_1.json").read_text(
            encoding="utf-8"
        )
    )
    trace = ExecutionTrace(trace_id="trace-success-1", task_id="task-1")
    trace.append_attempt(_attempt())

    seed = build_geotool_trace_seed(trace, user_query="加载图层")
    payload = seed.model_dump(mode="json")

    assert set(payload) == set(fixture)
    assert set(payload["tool_call_plan"]) == set(fixture["tool_call_plan"])
    assert set(payload["tool_call_plan"]["calls"][0]) == set(
        fixture["tool_call_plan"]["calls"][0]
    )
    assert set(payload["observations"][0]) == set(fixture["observations"][0])
    assert payload["metadata"]["export_version"] == "geotool-interop-v0.1"
