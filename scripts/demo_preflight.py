#!/usr/bin/env python3
"""Preflight checks for the QGIS Agent + GeoTool demo."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.health_check import (  # noqa: E402
    CheckResult,
    check_database,
    check_http_mcp,
    check_qgis_socket,
)

REQUIRED_DEMO_CASES = (
    "01_simple_success.json",
    "02_failure_repair.json",
    "03_skill_draft_preview.json",
)


@dataclass(frozen=True)
class DemoCheck:
    name: str
    ok: bool
    required: bool
    message: str


def _from_health_check(result: CheckResult, required: bool = True) -> DemoCheck:
    return DemoCheck(result.name, result.ok, required, result.message)


def check_static_demo_cases(root: Path = PROJECT_ROOT) -> DemoCheck:
    demo_dir = root / "demo_cases"
    missing = [name for name in REQUIRED_DEMO_CASES if not (demo_dir / name).exists()]
    if missing:
        return DemoCheck(
            "Static trace demo",
            False,
            True,
            "Missing demo case files: " + ", ".join(missing),
        )
    return DemoCheck(
        "Static trace demo",
        True,
        True,
        f"{len(REQUIRED_DEMO_CASES)} static demo cases available.",
    )


def check_frontend_dist(root: Path = PROJECT_ROOT) -> DemoCheck:
    index_path = root / "ui" / "frontend" / "dist" / "index.html"
    if index_path.exists():
        return DemoCheck(
            "Frontend build",
            True,
            True,
            "Built SPA found at ui/frontend/dist/index.html.",
        )
    return DemoCheck(
        "Frontend build",
        False,
        True,
        "ui/frontend/dist/index.html is missing. Run 'cd ui/frontend; npm run build'.",
    )


DEFAULT_RUN_SMOKE_TRACE_PATH = Path(".target/demo/controlled_smoke_v0_1.jsonl")
DEFAULT_DEMO_SMOKE_TRACE_PATH = Path("exports/geotool_traces/controlled_smoke_v0_1.jsonl")


def _resolve_trace_path(root: Path, trace_path: Path | None) -> Path:
    if trace_path is not None and trace_path.is_absolute():
        return trace_path
    if trace_path is not None:
        return root / trace_path

    run_trace_path = root / DEFAULT_RUN_SMOKE_TRACE_PATH
    if run_trace_path.exists():
        return run_trace_path
    return root / DEFAULT_DEMO_SMOKE_TRACE_PATH


def check_controlled_smoke_trace(
    root: Path = PROJECT_ROOT,
    trace_path: Path | None = None,
) -> DemoCheck:
    trace_path = _resolve_trace_path(root, trace_path)
    if not trace_path.exists():
        return DemoCheck(
            "Real MCP smoke",
            False,
            False,
            "controlled_smoke_v0_1.jsonl is missing. Run scripts/run_controlled_execution_smoke.py when QGIS is available.",
        )

    try:
        first_line = trace_path.read_text(encoding="utf-8").splitlines()[0]
        payload = json.loads(first_line)
    except Exception as exc:  # noqa: BLE001 - surface operator-friendly preflight detail.
        return DemoCheck("Real MCP smoke", False, False, f"Could not parse smoke trace: {exc}")

    final_status = payload.get("final_status")
    observations = payload.get("observations", [])
    statuses = [
        observation.get("status")
        for observation in observations
        if isinstance(observation, dict)
    ]
    if final_status == "success" and statuses and all(status == "success" for status in statuses):
        return DemoCheck(
            "Real MCP smoke",
            True,
            False,
            f"Trace present with final_status=success and {len(statuses)} successful observations.",
        )
    return DemoCheck(
        "Real MCP smoke",
        False,
        False,
        f"Trace present but final_status={final_status!s}, observation_statuses={statuses!r}.",
    )


def check_geotool_trace_validation(
    root: Path = PROJECT_ROOT,
    geotool_root: Path | None = None,
    smoke_trace_path: Path | None = None,
) -> DemoCheck:
    if geotool_root is None:
        return DemoCheck(
            "GeoTool trace validation",
            False,
            False,
            "GeoTool root is not configured. Pass --geotool-root to validate downstream trace import.",
        )

    trace_path = _resolve_trace_path(root, smoke_trace_path)
    if not trace_path.exists():
        return DemoCheck(
            "GeoTool trace validation",
            False,
            False,
            "controlled_smoke_v0_1.jsonl is missing. Run scripts/run_controlled_execution_smoke.py first.",
        )

    validator_path = geotool_root / "training" / "scripts" / "validate_qgis_trace.py"
    if not validator_path.exists():
        return DemoCheck(
            "GeoTool trace validation",
            False,
            False,
            f"GeoTool validator not found at {validator_path}.",
        )

    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "training/scripts/validate_qgis_trace.py",
            str(trace_path),
        ],
        cwd=geotool_root,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (result.stdout or result.stderr).strip()
    if result.returncode == 0:
        return DemoCheck("GeoTool trace validation", True, False, output)
    return DemoCheck("GeoTool trace validation", False, False, output)


def build_checks(
    root: Path = PROJECT_ROOT,
    skip_services: bool = False,
    skip_external_smoke: bool = False,
    geotool_root: Path | None = None,
    smoke_trace_path: Path | None = None,
) -> list[DemoCheck]:
    resolved_smoke_trace_path = _resolve_trace_path(root, smoke_trace_path)
    checks = [
        check_static_demo_cases(root),
        check_frontend_dist(root),
    ]

    if not skip_services:
        checks.extend(
            [
                _from_health_check(check_database()),
                _from_health_check(check_qgis_socket()),
                _from_health_check(check_http_mcp()),
            ]
        )

    if not skip_external_smoke:
        checks.append(check_controlled_smoke_trace(root, resolved_smoke_trace_path))

    if geotool_root is not None:
        checks.append(check_geotool_trace_validation(root, geotool_root, resolved_smoke_trace_path))

    return checks


def summarize_demo_checks(checks: Sequence[DemoCheck]) -> str:
    lines = []
    for check in checks:
        if check.ok:
            status = "PASS"
        elif check.required:
            status = "FAIL"
        else:
            status = "WARN"
        lines.append(f"[{status}] {check.name}: {check.message}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether the local demo environment is ready.")
    parser.add_argument("--skip-services", action="store_true", help="Skip PostgreSQL, QGIS socket, and HTTP MCP checks.")
    parser.add_argument("--skip-external-smoke", action="store_true", help="Skip the optional real MCP smoke trace check.")
    parser.add_argument(
        "--smoke-trace",
        type=Path,
        default=None,
        help="Optional smoke trace JSONL path to validate. Relative paths are resolved from the QGIS-Agent root.",
    )
    parser.add_argument(
        "--geotool-root",
        type=Path,
        default=None,
        help="Optional GeoTool-LLM repository path for downstream trace validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    checks = build_checks(
        root=PROJECT_ROOT,
        skip_services=args.skip_services,
        skip_external_smoke=args.skip_external_smoke,
        geotool_root=args.geotool_root,
        smoke_trace_path=args.smoke_trace,
    )
    print(summarize_demo_checks(checks))
    required_ok = all(check.ok for check in checks if check.required)
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
