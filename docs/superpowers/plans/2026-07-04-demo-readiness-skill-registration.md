# Demo Readiness With Skill Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make QGIS-Agent-Refactoring demo-ready by stabilizing controlled QGIS execution, trace replay, GeoTool validation, and demo-only automatic Skill registration into controlled execution-plan rendering.

**Architecture:** Keep LangGraph as the orchestration layer and keep QGIS MCP execution behind reviewed `ToolCallPlan` objects. Skill reuse starts from successful `ExecutionTrace` evidence, extracts a `SkillDraft`, registers only low/medium-risk no-review steps in a demo registry, and renders a `SkillExecutionPlan` that can be inspected before any live execution.

**Tech Stack:** Python 3.12, Pydantic, LangGraph, LangChain MCP adapters, FastAPI, Vue, Element Plus, pytest, ruff.

---

### Task 1: Demo-Only Skill Registration

**Files:**
- Create: `agent/tools/skill_registry.py`
- Modify: `agent/tools/demo_cases.py`
- Test: `tests/test_skill_registry.py`
- Test: `tests/test_demo_cases.py`

- [x] **Step 1: Add a controlled in-memory registry**

Create `RegisteredSkill`, `SkillExecutionPlan`, `SkillRegistrationError`, and `SkillRegistry`. The registry must reject empty drafts, unapproved manual drafts, high-risk steps, and review-required steps.

- [x] **Step 2: Render registered skills into tool calls**

Add `build_skill_execution_plan(skill, replacements)` so demo fixtures can turn placeholders such as `${input_path_1}` into explicit `ToolCallPlan` calls.

- [x] **Step 3: Wire demo replay opt-in**

Extend `replay_demo_case()` so fixtures with `metadata.auto_register_skill = true` expose `registered_skill` and `skill_execution_plan`.

- [x] **Step 4: Verify registry behavior**

Run:

```powershell
uv run pytest tests/test_skill_registry.py tests/test_demo_cases.py -q
```

Expected: all tests pass.

### Task 2: Trace Demo UI Visibility

**Files:**
- Modify: `ui/frontend/src/types/index.ts`
- Modify: `ui/frontend/src/views/TraceDemoView.vue`
- Test: `tests/test_backend_demo_cases.py`

- [x] **Step 1: Add typed replay fields**

Expose `has_registered_skill`, `registered_skill`, and `skill_execution_plan` to the frontend type definitions.

- [x] **Step 2: Display registered skill evidence**

Add a read-only `Registered Skill` panel on `/trace-demo` showing registration metadata and the controlled execution plan.

- [x] **Step 3: Verify backend payload**

Run:

```powershell
uv run pytest tests/test_backend_demo_cases.py -q
```

Expected: detail endpoint includes the registered skill and execution plan.

### Task 3: Demo Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/demo_runbook.md`
- Modify: `docs/benchmark_notes.md`
- Modify: `docs/web_ui.md`

- [x] **Step 1: Update current capabilities**

Describe SkillDraft extraction plus demo-only Skill registration and controlled execution-plan rendering.

- [x] **Step 2: Update limits**

State that registered Skill execution is still an inspection/demo path; live execution must continue through reviewed `ToolCallPlan` and policy gates.

- [x] **Step 3: Add a runbook route**

Add a demo route for opening `/trace-demo`, selecting the Skill registration case, and showing `Registered Skill` plus `Controlled Execution Plan`.

### Task 4: Quality Gates

**Files:**
- Test: project quality gates

- [x] **Step 1: Run focused Python tests**

```powershell
uv run pytest tests/test_skill_registry.py tests/test_demo_cases.py tests/test_backend_demo_cases.py -q
```

Expected: all tests pass.

- [x] **Step 2: Run full Python test suite**

```powershell
uv run pytest -q
```

Expected: all default tests pass; external smoke may remain skipped unless QGIS external smoke is enabled.

- [x] **Step 3: Run lint**

```powershell
uv run ruff check .
```

Expected: all checks pass.

- [x] **Step 4: Build frontend**

```powershell
cd ui/frontend
npm run build
cd ..\..
```

Expected: frontend builds successfully.

- [x] **Step 5: Run demo preflight**

```powershell
uv run python scripts/demo_preflight.py --skip-services --skip-external-smoke --geotool-root D:\project_files\GeoTool-LLM
```

Expected: static demo, frontend build, and GeoTool trace validation pass.
