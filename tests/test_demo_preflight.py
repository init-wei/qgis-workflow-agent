from pathlib import Path

from scripts.demo_preflight import (
    DemoCheck,
    build_checks,
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
