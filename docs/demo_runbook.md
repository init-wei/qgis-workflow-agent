> 原型历史/基线说明。V1 当前范围和开发入口见[文档索引](README.md)，不可据此宣称重构已完成。
>
> T06 已移除下文的自动 Skill 注册、`run_skill_reuse_smoke.py` 和对应预检选项。保留本页作为历史，不按旧快速执行路径验证 V1；当前模板复用须经[正式规划与审核](onboarding/skills.md)。

# Demo Runbook

This runbook prepares the current demo story:

```text
QGIS Agent controlled execution
  -> real QGIS MCP smoke
  -> GeoTool-compatible trace export
  -> GeoTool-LLM trace ingestion and seed-data scaffold
```

The safest public positioning is "controlled QGIS execution plus GeoTool
training-data loop", not "a fully trained GIS LLM".

## Demo Routes

### Route 1: Static Trace Demo

Use this as the fallback and opening route. It does not require QGIS, an LLM, or
PostgreSQL.

```powershell
uv run python scripts/demo_preflight.py --skip-services --skip-external-smoke
uv run python scripts/replay_demo_cases.py
uv run python ui/backend/main.py
```

Open:

```text
http://localhost:9000/trace-demo
```

Show:

- `simple_success`: planned tool call and successful artifact.
- `failure_repair`: failed attempt followed by repair.
- `skill_draft_preview`: successful trace generalized into a SkillDraft,
  demo-registered under safety gates, and rendered as a controlled execution
  plan preview.

### Route 2: Real QGIS MCP Smoke

Use this as the live execution highlight. It requires QGIS Desktop with the MCP
plugin running on `localhost:9876`, plus the HTTP MCP wrapper on `localhost:8000`.

```powershell
uv run python scripts/demo_preflight.py
uv run python scripts/run_controlled_execution_smoke.py
```

Expected output:

```text
GeoTool trace JSONL: .target\demo\controlled_smoke_v0_1.jsonl
```

Expected daily-run artifacts:

```text
.target/demo/controlled_smoke_v0_1.jsonl
.target/demo/smoke_points.png
```

The trace should contain `final_status=success` and successful `add_layer` /
`capture_map_canvas` observations. To intentionally refresh the tracked demo
evidence under `exports/geotool_traces`, run:

```powershell
uv run python scripts/run_controlled_execution_smoke.py --update-demo-artifacts
```

### Route 3: GeoTool Cross-Repo Validation

Use this to show that the QGIS trace is useful downstream.

```powershell
uv run python scripts/demo_preflight.py --skip-services --geotool-root D:\project_files\GeoTool-LLM
```

Then show:

```text
D:\project_files\GeoTool-LLM\data\reports\seed_quality_report.md
D:\project_files\GeoTool-LLM\data\reports\baseline_mock_eval_report.md
```

### Route 4: Skill Registration Execution Preview

Use this to show how a successful trace can become reusable without letting a
model freely execute arbitrary code.

```powershell
uv run python scripts/demo_preflight.py --skip-services --skip-external-smoke
uv run python ui/backend/main.py
```

Open:

```text
http://localhost:9000/trace-demo
```

Select `Registered skill execution preview from a successful trace`, then show:

- `SkillDraft Preview`: generalized variables and reuse preview.
- `Registered Skill`: approved demo registration metadata.
- `Controlled Execution Plan`: explicit tool call arguments rendered from
  replacement variables.

Position this as a controlled reuse preview. Live execution of a registered
skill must still use the normal `ToolCallPlan -> ToolPolicy -> ExecutionLoop`
route and any configured review gates.

### Route 5: Real Skill Reuse Smoke

Use this after Route 2 proves QGIS MCP is reachable. It renders the registered
Skill reuse fixture into explicit `add_layer` and `capture_map_canvas` calls,
then runs those calls through the same controlled executor path as the live
smoke.

```powershell
uv run python scripts/run_skill_reuse_smoke.py
uv run python scripts/demo_preflight.py --skill-reuse-trace .target\demo\skill_reuse_smoke_v0_1.jsonl --geotool-root D:\project_files\GeoTool-LLM
```

The preflight command reports separate GeoTool validation checks for the
controlled smoke trace and the Skill reuse trace when both artifacts are
available.

Expected output:

```text
Skill reuse GeoTool trace JSONL: .target\demo\skill_reuse_smoke_v0_1.jsonl
```

Expected daily-run artifacts:

```text
.target/demo/skill_reuse_smoke_v0_1.jsonl
.target/demo/skill_reuse_smoke.png
```

This route demonstrates registered Skill reuse under controlled execution. If
the QGIS MCP endpoint is unavailable, skip it and use Route 4 to show the
registered plan preview.

## Full Demo Startup

From `D:\project_files\QGIS-Agent-Refactoring`:

```powershell
docker compose up -d postgres
uv run python scripts/setup_database.py
```

Start QGIS Desktop and enable the QGIS MCP plugin. Confirm QGIS listens on
`localhost:9876`, then start the HTTP MCP wrapper:

```powershell
uv run python qgis_mcp_server/qgis_mcp_server.py
```

In another terminal:

```powershell
uv run python scripts/demo_preflight.py
uv run python ui/backend/main.py
```

Open:

```text
http://localhost:9000
http://localhost:9000/trace-demo
```

## Optional Live Planner

Only use the live planner if the API key is configured and the live check passes:

```powershell
uv run python scripts/health_check.py --llm-live
```

Recommended prompt:

```text
请在 QGIS 中加载 shared/uploads/phase11_points.geojson 这个矢量点图层，图层名称使用 phase11_points。只需要加载图层，不要删除、覆盖或保存工程。
```

If the LLM is slow or unavailable, use Route 1 and Route 2 instead.

## Failure Handling

| Symptom | Action |
| --- | --- |
| `PostgreSQL` fails | Run `docker compose up -d postgres` and `uv run python scripts/setup_database.py`. |
| `QGIS socket` fails | Start QGIS Desktop, enable the MCP plugin, and confirm it listens on `localhost:9876`. |
| `HTTP MCP` fails | Run `uv run python qgis_mcp_server/qgis_mcp_server.py`. |
| `Frontend build` fails | Run `cd ui/frontend; npm install; npm run build`. |
| `Real MCP smoke` warns | Skip live smoke and show `/trace-demo`; this warning does not block the static demo. |
| `Skill reuse smoke` warns | Run `uv run python scripts/run_skill_reuse_smoke.py` after QGIS MCP is reachable, or show Route 4. |
| LLM live call fails | Do not use the live planner; use static trace replay and controlled smoke. |

## Pre-Demo Quality Gates

Run these before a recorded or public demo:

```powershell
uv run pytest -q
uv run ruff check .
cd ui/frontend
npm run build
cd ..\..
uv run python scripts/demo_preflight.py
```

From `D:\project_files\GeoTool-LLM`:

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy geotool_core data_pipeline evals
```
