import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_controlled_execution_smoke import (
    DEFAULT_SMOKE_SCREENSHOT_PATH,
    DEFAULT_SMOKE_TRACE_PATH,
    DEMO_SCREENSHOT_PATH,
    DEMO_TRACE_PATH,
    MCPPreflightResult,
    build_smoke_task_plan,
    check_mcp_preflight,
    resolve_smoke_outputs,
    run_smoke,
    skip_reason_for_preflight,
)


@pytest.mark.external
def test_controlled_executor_real_mcp_smoke_exports_geotool_trace(tmp_path):
    if os.getenv("QGIS_AGENT_RUN_EXTERNAL_SMOKE") != "1":
        pytest.skip("Set QGIS_AGENT_RUN_EXTERNAL_SMOKE=1 to run real MCP smoke")
    preflight = asyncio.run(check_mcp_preflight())
    if not preflight.ok:
        pytest.skip(skip_reason_for_preflight(preflight))

    output_path = asyncio.run(run_smoke(tmp_path / "controlled_smoke.jsonl"))

    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").strip()


def test_external_smoke_preflight_skip_reason_names_mcp_url():
    result = MCPPreflightResult(
        ok=False,
        url="http://localhost:8000/mcp",
        message="All connection attempts failed",
    )

    reason = skip_reason_for_preflight(result)

    assert "QGIS_MCP_SERVER_URL=http://localhost:8000/mcp" in reason
    assert "All connection attempts failed" in reason


def test_controlled_smoke_defaults_to_ignored_run_artifacts():
    trace_path, screenshot_path = resolve_smoke_outputs(
        output_path=None,
        screenshot_path=None,
        update_demo_artifacts=False,
    )

    assert trace_path == DEFAULT_SMOKE_TRACE_PATH
    assert screenshot_path == DEFAULT_SMOKE_SCREENSHOT_PATH
    assert trace_path.parts[:2] == (".target", "demo")
    assert screenshot_path == Path("smoke_points.png")


def test_controlled_smoke_can_resolve_tracked_demo_artifacts():
    trace_path, screenshot_path = resolve_smoke_outputs(
        output_path=None,
        screenshot_path=None,
        update_demo_artifacts=True,
    )

    assert trace_path == DEMO_TRACE_PATH
    assert screenshot_path == DEMO_SCREENSHOT_PATH


def test_controlled_smoke_task_plan_uses_resolved_screenshot_path():
    task_plan = build_smoke_task_plan("smoke_points.png")

    assert task_plan.expected_outputs[0].path == str(Path.cwd() / "shared/outputs/smoke-load-layer-capture/r1/map_screenshot/smoke_points.png")
    assert (
        task_plan.tool_calls[1].arguments["path"]
        == task_plan.expected_outputs[0].path
    )


def test_controlled_execution_smoke_script_help_imports_from_repo_root():
    script = Path(__file__).parents[1] / "scripts" / "run_controlled_execution_smoke.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--output" in result.stdout
    assert "--screenshot-output" in result.stdout
    assert "--update-demo-artifacts" in result.stdout
