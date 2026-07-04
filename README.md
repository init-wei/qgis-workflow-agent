# QGIS Workflow Agent

QGIS Workflow Agent is a local prototype for running simple QGIS tasks from natural language. The current usable path is:

```text
User request
  -> DeepSeek/OpenAI-compatible planner
  -> TaskPlan review
  -> ToolCallPlan compilation
  -> ToolPolicy / optional risk review
  -> QGIS MCP execution
  -> result confirmation
  -> final summary
```

The project is intentionally conservative: the default path uses explicit tool-call plans instead of an unconstrained black-box agent, records `ExecutionTrace`, and pauses for human review before plan execution, risky operations, and final result acceptance.

## Current Capabilities

- Generate a structured `TaskPlan` from a Chinese or English GIS request.
- Review and approve the generated plan in the Web UI.
- Compile approved plans into explicit `ToolCallPlan` objects.
- Execute supported QGIS MCP tools through the controlled `ExecutionLoop`.
- Record normalized observations, attempts, artifacts, repair decisions, and trace metadata.
- Pause for high-risk actions such as `execute_code`, delete/remove, overwrite, and save-project operations.
- Run a minimal real E2E task that loads `shared/uploads/phase11_points.geojson` into QGIS.
- Replay static demo traces and inspect them through `/trace-demo`.
- Export GeoTool interop v0.1 trace seed JSONL for downstream dataset work.

## Current Limits

- The proven real E2E scope is intentionally small: layer loading plus review/confirmation.
- Processing workflows are supported by the contracts, but complex parameter repair still needs more validation.
- RAG is connected as retrieval evidence for execution/repair context; it is not a complete parameter-schema verifier yet.
- `SkillDraft` extraction creates draft evidence only. Skills are not registered, retrieved, or auto-executed.
- Static demo cases are replay evidence, not a real QGIS performance benchmark.
- The legacy `react_agent` mode remains available only as a baseline comparison path. The default mode is `tool_call_plan`.

Switch execution modes with:

```powershell
$env:QGIS_AGENT_EXECUTION_MODE = "tool_call_plan"
$env:QGIS_AGENT_EXECUTION_MODE = "react_agent"
```

`tool_call_plan` runs the controlled `TaskPlan -> ToolCallPlan -> ExecutionLoop`
route. `react_agent` keeps the legacy create-agent route available for
comparison and fallback.

## Requirements

- Python 3.12+
- uv
- Docker Desktop
- PostgreSQL from `docker-compose.yml`
- QGIS Desktop with the project QGIS MCP plugin enabled
- DeepSeek or another OpenAI-compatible API key
- Node.js / npm for the frontend

## Configuration

Copy `.env.example` to `.env` and set:

```bash
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/qgis_db
OPENAI_API_KEY=your_deepseek_key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL_NAME=deepseek-chat
QGIS_MCP_SERVER_URL=http://localhost:8000/mcp
WEB_UI_PORT=9000
WEB_UI_HOST=0.0.0.0
```

The API key must not be printed in logs. `scripts/health_check.py` only validates the key configuration and can optionally make a live model call.

## Setup

Install Python dependencies:

```bash
uv sync
```

Start PostgreSQL and initialize tables:

```bash
docker compose up -d postgres
uv run python scripts/setup_database.py
```

Start QGIS Desktop, enable the QGIS MCP plugin, then start the HTTP MCP wrapper:

```bash
uv run python qgis_mcp_server/qgis_mcp_server.py
```

Start the backend:

```bash
uv run python ui/backend/main.py
```

For frontend development:

```bash
cd ui/frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Health Check

Run the local environment check:

```bash
uv run python scripts/health_check.py --skip-llm
```

Run a live DeepSeek/OpenAI-compatible check:

```bash
uv run python scripts/health_check.py --llm-live
```

If the HTTP MCP wrapper is not already running, this command can start it temporarily:

```bash
uv run python scripts/health_check.py --start-http-mcp --skip-llm
```

For the full manual E2E flow, see [docs/minimal_real_e2e_task.md](docs/minimal_real_e2e_task.md).

## Useful Commands

```bash
# Python syntax/import smoke
uv run python -m compileall agent qgis_mcp_server ui/backend scripts

# Default smoke tests
uv run pytest

# Demo replay
uv run python scripts/replay_demo_cases.py

# Demo readiness check
uv run python scripts/demo_preflight.py

# Real QGIS MCP controlled-execution smoke.
# Writes ignored daily-run artifacts under .target/demo by default.
uv run python scripts/run_controlled_execution_smoke.py
uv run python scripts/demo_preflight.py --skip-services --geotool-root D:\project_files\GeoTool-LLM

# Intentionally refresh tracked demo evidence under exports/geotool_traces
uv run python scripts/run_controlled_execution_smoke.py --update-demo-artifacts

# Frontend build
cd ui/frontend
npm run build
```

## Project Layout

```text
agent/
  adapters/        GeoTool-compatible schema export/import helpers
  nodes/           LangGraph nodes for planning, execution, review, reflection
  planning/        TaskPlan schema, parser, validator, legacy UI adapter
  tools/           MCP client, contracts, policy, execution loop, trace export
qgis_mcp_server/   HTTP MCP wrapper and QGIS plugin bridge
ui/backend/        FastAPI backend, SSE, review APIs, demo APIs
ui/frontend/       Vue frontend
docs/              Current documentation and historical implementation notes
demo_cases/        Static replay fixtures
tests/             Unit and smoke tests
```

## Documentation

- [docs/README.md](docs/README.md): documentation index.
- [docs/minimal_real_e2e_task.md](docs/minimal_real_e2e_task.md): real local E2E task.
- [docs/demo_runbook.md](docs/demo_runbook.md): stable demo routes, startup order, and fallback plan.
- [docs/web_ui.md](docs/web_ui.md): Web UI flow and backend endpoints.
- [docs/geotool_llm_integration.md](docs/geotool_llm_integration.md): GeoTool schema boundary.
- [docs/geotool_llm_history.md](docs/geotool_llm_history.md): implemented Q0 history.
- [docs/benchmark_notes.md](docs/benchmark_notes.md): demo replay and benchmark limits.
