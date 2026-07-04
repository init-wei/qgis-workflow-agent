from pathlib import Path
import subprocess

from scripts.demo_preflight import (
    DemoCheck,
    build_checks,
    check_geotool_trace_validation,
    check_controlled_smoke_trace,
    check_frontend_dist,
    check_static_demo_cases,
    summarize_demo_checks,
)


def test_summarize_demo_checks_marks_required_failure_as_fail():
    checks = [
        DemoCheck("Static trace demo", True, True, "3 cases available"),
        DemoCheck("Real MCP smoke", False, False, "not run"),
        DemoCheck("Frontend build", False, True, "dist missing"),
    ]

    summary = summarize_demo_checks(checks)

    assert "[PASS] Static trace demo: 3 cases available" in summary
    assert "[WARN] Real MCP smoke: not run" in summary
    assert "[FAIL] Frontend build: dist missing" in summary


def test_static_demo_case_check_requires_all_three_demo_cases(tmp_path):
    demo_dir = tmp_path / "demo_cases"
    demo_dir.mkdir()
    for name in [
        "01_simple_success.json",
        "02_failure_repair.json",
        "03_skill_draft_preview.json",
    ]:
        (demo_dir / name).write_text("{}", encoding="utf-8")

    result = check_static_demo_cases(tmp_path)

    assert result.ok is True
    assert result.required is True
    assert "3 static demo cases" in result.message


def test_static_demo_case_check_reports_missing_case(tmp_path):
    (tmp_path / "demo_cases").mkdir()
    (tmp_path / "demo_cases" / "01_simple_success.json").write_text("{}", encoding="utf-8")

    result = check_static_demo_cases(tmp_path)

    assert result.ok is False
    assert "02_failure_repair.json" in result.message
    assert "03_skill_draft_preview.json" in result.message


def test_frontend_dist_check_accepts_built_spa(tmp_path):
    dist = tmp_path / "ui" / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<div id='app'></div>", encoding="utf-8")

    result = check_frontend_dist(tmp_path)

    assert result.ok is True
    assert "ui/frontend/dist" in result.message


def test_controlled_smoke_trace_check_validates_success_trace(tmp_path):
    trace_path = tmp_path / "exports" / "geotool_traces" / "controlled_smoke_v0_1.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        '{"final_status":"success","observations":[{"status":"success"},{"status":"success"}]}\n',
        encoding="utf-8",
    )

    result = check_controlled_smoke_trace(tmp_path)

    assert result.ok is True
    assert result.required is False
    assert "final_status=success" in result.message


def test_controlled_smoke_trace_check_accepts_custom_trace_path(tmp_path):
    trace_path = tmp_path / ".target" / "demo" / "controlled_smoke_v0_1.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        '{"final_status":"success","observations":[{"status":"success"}]}\n',
        encoding="utf-8",
    )

    result = check_controlled_smoke_trace(tmp_path, trace_path)

    assert result.ok is True
    assert "final_status=success" in result.message


def test_controlled_smoke_trace_check_reports_failed_trace(tmp_path):
    trace_path = tmp_path / "exports" / "geotool_traces" / "controlled_smoke_v0_1.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        '{"final_status":"failed","observations":[{"status":"success"},{"status":"failed"}]}\n',
        encoding="utf-8",
    )

    result = check_controlled_smoke_trace(tmp_path)

    assert result.ok is False
    assert result.required is False
    assert "final_status=failed" in result.message


def test_build_checks_can_skip_external_services():
    checks = build_checks(
        root=Path("D:/project_files/QGIS-Agent-Refactoring"),
        skip_services=True,
        skip_external_smoke=True,
    )

    names = [check.name for check in checks]

    assert "PostgreSQL" not in names
    assert "QGIS socket" not in names
    assert "HTTP MCP" not in names
    assert "Real MCP smoke" not in names


def test_geotool_trace_validation_runs_geotool_cli(tmp_path, monkeypatch):
    qgis_root = tmp_path / "qgis"
    geotool_root = tmp_path / "geotool"
    trace_path = qgis_root / "exports" / "geotool_traces" / "controlled_smoke_v0_1.jsonl"
    script_path = geotool_root / "training" / "scripts" / "validate_qgis_trace.py"
    trace_path.parent.mkdir(parents=True)
    script_path.parent.mkdir(parents=True)
    trace_path.write_text('{"final_status":"success"}\n', encoding="utf-8")
    script_path.write_text("print('ok')\n", encoding="utf-8")
    calls = []

    def fake_run(command, cwd, text, capture_output, check):
        calls.append((command, cwd, text, capture_output, check))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="QGIS trace validation: ok\ntraces: 1\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = check_geotool_trace_validation(qgis_root, geotool_root, trace_path)

    assert result.ok is True
    assert result.required is False
    assert result.name == "GeoTool trace validation"
    assert "QGIS trace validation: ok" in result.message
    assert calls == [
        (
            [
                "uv",
                "run",
                "python",
                "training/scripts/validate_qgis_trace.py",
                str(trace_path),
            ],
            geotool_root,
            True,
            True,
            False,
        )
    ]


def test_geotool_trace_validation_reports_cli_failure(tmp_path, monkeypatch):
    qgis_root = tmp_path / "qgis"
    geotool_root = tmp_path / "geotool"
    trace_path = qgis_root / "exports" / "geotool_traces" / "controlled_smoke_v0_1.jsonl"
    script_path = geotool_root / "training" / "scripts" / "validate_qgis_trace.py"
    trace_path.parent.mkdir(parents=True)
    script_path.parent.mkdir(parents=True)
    trace_path.write_text('{"final_status":"failed"}\n', encoding="utf-8")
    script_path.write_text("raise SystemExit(1)\n", encoding="utf-8")

    def fake_run(command, cwd, text, capture_output, check):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="QGIS trace validation: failed\nsource_project mismatch\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = check_geotool_trace_validation(qgis_root, geotool_root, trace_path)

    assert result.ok is False
    assert result.required is False
    assert "source_project mismatch" in result.message


def test_build_checks_adds_geotool_trace_validation_when_configured(tmp_path, monkeypatch):
    qgis_root = tmp_path / "qgis"
    geotool_root = tmp_path / "geotool"
    (qgis_root / "demo_cases").mkdir(parents=True)
    for name in [
        "01_simple_success.json",
        "02_failure_repair.json",
        "03_skill_draft_preview.json",
    ]:
        (qgis_root / "demo_cases" / name).write_text("{}", encoding="utf-8")
    dist = qgis_root / "ui" / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<div id='app'></div>", encoding="utf-8")

    monkeypatch.setattr(
        "scripts.demo_preflight.check_geotool_trace_validation",
        lambda root, geotool_root, smoke_trace_path: DemoCheck(
            "GeoTool trace validation", True, False, "ok"
        ),
    )

    smoke_trace_path = qgis_root / ".target" / "demo" / "controlled_smoke_v0_1.jsonl"
    checks = build_checks(
        root=qgis_root,
        skip_services=True,
        skip_external_smoke=True,
        geotool_root=geotool_root,
        smoke_trace_path=smoke_trace_path,
    )

    assert [check.name for check in checks][-1] == "GeoTool trace validation"


def test_build_checks_uses_custom_smoke_trace_for_preflight(tmp_path):
    qgis_root = tmp_path / "qgis"
    (qgis_root / "demo_cases").mkdir(parents=True)
    for name in [
        "01_simple_success.json",
        "02_failure_repair.json",
        "03_skill_draft_preview.json",
    ]:
        (qgis_root / "demo_cases" / name).write_text("{}", encoding="utf-8")
    dist = qgis_root / "ui" / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<div id='app'></div>", encoding="utf-8")
    smoke_trace_path = qgis_root / ".target" / "demo" / "controlled_smoke_v0_1.jsonl"
    smoke_trace_path.parent.mkdir(parents=True)
    smoke_trace_path.write_text(
        '{"final_status":"success","observations":[{"status":"success"}]}\n',
        encoding="utf-8",
    )

    checks = build_checks(
        root=qgis_root,
        skip_services=True,
        smoke_trace_path=smoke_trace_path,
    )

    real_smoke = next(check for check in checks if check.name == "Real MCP smoke")

    assert real_smoke.ok is True
