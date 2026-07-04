# GeoTool-LLM Q0 Implementation History

This note replaces the earlier Q0 task checklist. The checklist items are complete enough to be represented as history, so this document keeps only what was implemented, where it lives, and how it is verified.

## Q0-1: Runtime Contract Boundary

The project froze the internal runtime contracts around:

- `agent.planning.plan_schema.TaskPlan`
- `agent.planning.plan_schema.PlanStep`
- `agent.tools.tool_contracts.ToolCallPlan`
- `agent.tools.tool_contracts.ToolObservation`
- `agent.tools.tool_contracts.StepAttempt`
- `agent.tools.tool_contracts.ExecutionTrace`

The current schema boundary is documented in [geotool_llm_integration.md](geotool_llm_integration.md). The runtime keeps owning Pydantic models; GeoTool exchange uses JSON-compatible dictionaries at adapter/export edges.

## Q0-2: GeoTool Schema Adapter

Implemented in:

- `agent/adapters/geotool_schema_adapter.py`
- `tests/test_geotool_schema_adapter.py`

The adapter supports:

- exporting QGIS `TaskPlan` to GeoTool-compatible task dictionaries;
- converting GeoTool task payloads to legacy UI `Plan`;
- converting GeoTool tool-call payloads to internal `ToolCallPlan`;
- exporting `ExecutionTrace` as GeoTool-compatible trace payloads.

The adapter intentionally does not import GeoTool-LLM source code.

## Q0-3: Minimal ToolCallPlanner

Implemented in:

- `agent/tools/tool_call_planner.py`
- `tests/test_tool_call_planner.py`

The planner compiles reviewed `TaskPlan` data into explicit `ToolCallPlan` objects. It supports metadata-provided tool calls, rule-based layer-loading calls, reviewed-step enforcement, and argument normalization for common LLM output such as `uri` / `layer_name`.

## Q0-4: ExecutionLoop Path

Implemented in:

- `agent/nodes/tool_call_execution_node.py`
- `agent/nodes/executor_node.py`
- `agent/tools/execution_loop.py`
- `tests/test_tool_call_execution_node.py`
- `tests/test_tool_call_execution_loop_integration.py`

The controlled path compiles an approved `TaskPlan`, evaluates tool policy, executes one or more MCP calls through `ExecutionLoop`, records `ExecutionTrace`, and preserves review pauses instead of silently executing high-risk calls.

## Q0-5: GeoTool Trace Seed Export

Implemented in:

- `agent/tools/export_geotool_trace.py`
- `tests/test_export_geotool_trace.py`

The exporter writes JSONL seed records containing task/query metadata, tool calls, observations, repair actions, final status, and source metadata. These records are intended as dataset seed material, not as a training pipeline by themselves.

## Q0-6: Controlled Execution Mode

Implemented in:

- `agent/config.py`
- `agent/nodes/executor_node.py`
- `tests/test_execution_mode_config.py`

The runtime uses `tool_call_plan`. The earlier `react_agent` baseline was removed
after the controlled execution loop became the only supported path.

## Q0-7: Tests and Quality Gate

The Q0 work is covered by targeted tests for:

- GeoTool adapter conversion;
- ToolCallPlan compilation and reviewed-step filtering;
- controlled execution node behavior;
- execution mode configuration;
- GeoTool trace export.

The broader smoke gate remains:

```bash
uv run pytest
uv run python -m compileall agent qgis_mcp_server ui/backend scripts
```

The project keeps advisory quality checks separate from required smoke tests so historical style issues do not block runtime validation.
