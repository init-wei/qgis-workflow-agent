"""Spatial checks through a read-only system-GDAL process, separate from the Agent venv.

The caller is responsible for controlled paths and for binding them to the approved
plan and the actual producer output.  The inspector seam also admits a deterministic
fake in offline tests.  A processing artifact is deliverable only after ``passed``.
"""

import asyncio
import json
from pathlib import Path
import subprocess


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_spatial.py"


def _system_inspector(request):
    try:
        completed = subprocess.run(
            ["/usr/bin/python3", str(_SCRIPT)], input=json.dumps(request, allow_nan=False),
            capture_output=True, text=True, timeout=120, check=False,
        )
        report = json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Spatial inspector unavailable or returned invalid JSON.") from exc
    if completed.returncode != 0 and (
        not isinstance(report, dict) or report.get("status") != "failed"
    ):
        raise ValueError("Spatial inspector failed without a valid failure report.")
    return report


def _checked(report):
    if (not isinstance(report, dict)
            or not isinstance(report.get("status"), str)
            or report.get("status") not in {"passed", "failed", "not_applicable"}
            or not isinstance(report.get("checks"), list)
            or not isinstance(report.get("errors"), list)
            or not isinstance(report.get("facts"), dict)
            or any(not ((isinstance(check, str) and check)
                       or (isinstance(check, dict) and isinstance(check.get("check"), str) and check["check"]))
                   for check in report["checks"])
            or any(not isinstance(error, str) or not error for error in report["errors"])
            or (report["status"] == "failed") != bool(report["errors"])):
        raise ValueError("Spatial inspector returned an invalid report.")
    return report


async def preflight_spatial_plan(plan, *, inspector=None):
    """Check original layers and planned buffer units before any Processing write."""
    if not any(call.tool_name == "execute_processing" for call in plan.tool_calls):
        return {"status": "passed", "checks": [], "errors": [], "facts": {}}
    request = {"mode": "preflight", "plan": plan.model_dump(mode="json")}
    try:
        raw = await asyncio.to_thread(inspector or _system_inspector, request)
    except Exception as exc:
        raise ValueError("Spatial inspector is unavailable.") from exc
    report = _checked(raw)
    if report["status"] != "passed":
        raise ValueError("Spatial preflight failed: " + "; ".join(report["errors"] or ["not applicable"]))
    return report


async def validate_spatial_step(call, *, input_paths, output_paths, inspector=None):
    """Read the actual output against actual input; return compact evidence or failure."""
    if call.tool_name != "execute_processing":
        return {"status": "not_applicable", "checks": [], "errors": [], "facts": {}}
    request = {
        "mode": "validate", "call": call.model_dump(mode="json"),
        "input_paths": dict(input_paths), "output_paths": dict(output_paths),
    }
    try:
        return _checked(await asyncio.to_thread(inspector or _system_inspector, request))
    except Exception:
        return {"status": "failed", "checks": [],
                "errors": ["Spatial inspector unavailable or returned an invalid report."], "facts": {}}
