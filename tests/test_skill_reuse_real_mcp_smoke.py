import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_controlled_execution_smoke import (
    MCPPreflightResult,
    check_mcp_preflight,
    skip_reason_for_preflight,
)
from scripts.run_skill_reuse_smoke import (
    DEFAULT_SKILL_REUSE_SCREENSHOT_PATH,
    DEFAULT_SKILL_REUSE_TRACE_PATH,
    build_skill_reuse_task_plan,
    resolve_skill_reuse_outputs,
    run_skill_reuse_smoke,
)


@pytest.mark.external
def test_skill_reuse_real_mcp_smoke_exports_geotool_trace(tmp_path):
    if os.getenv("QGIS_AGENT_RUN_EXTERNAL_SMOKE") != "1":
        pytest.skip("Set QGIS_AGENT_RUN_EXTERNAL_SMOKE=1 to run real Skill reuse smoke")
    preflight = asyncio.run(check_mcp_preflight())
    if not preflight.ok:
        pytest.skip(skip_reason_for_preflight(preflight))

    output_path = asyncio.run(
        run_skill_reuse_smoke(
            output_path=tmp_path / "skill_reuse_smoke.jsonl",
            screenshot_path=tmp_path / "skill_reuse_smoke.png",
        )
    )

    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").strip()


def test_skill_reuse_smoke_defaults_to_ignored_run_artifacts():
    trace_path, screenshot_path = resolve_skill_reuse_outputs(
        output_path=None,
        screenshot_path=None,
    )

    assert trace_path == DEFAULT_SKILL_REUSE_TRACE_PATH
    assert screenshot_path == DEFAULT_SKILL_REUSE_SCREENSHOT_PATH
    assert trace_path.parts[:2] == (".target", "demo")
    assert screenshot_path.parts[:2] == (".target", "demo")


def test_skill_reuse_task_plan_uses_registered_skill_metadata():
    task_plan, replay = build_skill_reuse_task_plan(".target/demo/reuse.png")

    assert replay.registered_skill
    assert replay.skill_execution_plan
    assert task_plan.task_id == "skill-reuse-live-smoke"
    assert task_plan.metadata["registered_skill_id"] == replay.registered_skill["skill_id"]
    assert task_plan.metadata["tool_calls"][0]["tool_name"] == "add_layer"
    assert task_plan.metadata["tool_calls"][1]["tool_name"] == "capture_map_canvas"
    assert task_plan.metadata["tool_calls"][1]["arguments"]["path"] == ".target/demo/reuse.png"


def test_skill_reuse_smoke_script_help_imports_from_repo_root():
    script = Path(__file__).parents[1] / "scripts" / "run_skill_reuse_smoke.py"

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


def test_skill_reuse_preflight_skip_reason_uses_controlled_smoke_helper():
    result = MCPPreflightResult(
        ok=False,
        url="http://localhost:8000/mcp",
        message="not reachable",
    )

    reason = skip_reason_for_preflight(result)

    assert "QGIS_MCP_SERVER_URL=http://localhost:8000/mcp" in reason
    assert "not reachable" in reason
