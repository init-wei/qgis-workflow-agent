# GeoTool-LLM Integration Contract

This document freezes the GeoTool interop v0.1 schema boundary between
`qgis-workflow-agent` and GeoTool-LLM. The runtime keeps owning its Pydantic
models; GeoTool exchange uses plain `dict` / JSON payloads through
`agent.adapters.geotool_schema_adapter`.

## Runtime-Owned Types

These types remain internal to `qgis-workflow-agent`:

- `agent.planning.plan_schema.TaskPlan`
- `agent.planning.plan_schema.PlanStep`
- `agent.tools.tool_contracts.ToolCallPlan`
- `agent.tools.tool_contracts.ToolObservation`
- `agent.tools.tool_contracts.StepAttempt`
- `agent.tools.tool_contracts.ExecutionTrace`
- legacy UI compatibility types in `agent.state.Plan` and `agent.state.Step`

The adapter must not import GeoTool-LLM source code. It only emits and consumes JSON-compatible dictionaries.

## Current QGIS TaskPlan Fields

| QGIS field | Meaning | GeoTool export |
| --- | --- | --- |
| `task_id` | Stable task identifier | `task_id` |
| `task_summary` | User-facing task goal | `goal` |
| `risk_level` | Overall risk level | `risk_level` |
| `review_status` | Human review state | `metadata.qgis_review_status` |
| `required_inputs` | Input specs with path/source | `required_inputs` |
| `expected_outputs` | Expected output specs | `expected_outputs` |
| `steps` | Ordered operation plan | `steps` |
| `metadata` | Runtime-only context | `metadata` |

## Current QGIS PlanStep Fields

| QGIS field | GeoTool field | Notes |
| --- | --- | --- |
| `step_id` | `step_id` | Exported as a string to match GeoTool schemas. |
| `description` | `description` | Human-readable step instruction. |
| `step_type` | `intent` and `metadata.qgis_step_type` | Defaults to `qgis_operation`. |
| `candidate_gdal_apis` | `metadata.candidate_gdal_apis` | Evidence only, not executable authority. |
| `candidate_pyqgis_apis` | `metadata.candidate_pyqgis_apis` | Evidence only, not executable authority. |
| `allowed_tools` | `candidate_tools` | Tool scope approved with the plan. |
| `input_refs` | `input_refs` | References `required_inputs`. |
| `output_refs` | `output_refs` | References `expected_outputs`. |
| `risk_level` | `risk_level` | Used by risk policy and review manager. |
| `requires_review` | `requires_review` | Carries human-review requirements. |

## Current QGIS ToolCallPlan Fields

GeoTool interop v0.1 uses canonical `calls`. Legacy inbound `tool_calls` is
accepted only as a transition input. qgis exports `calls`.

| QGIS field | GeoTool field | Notes |
| --- | --- | --- |
| `step_id` | `step_id` | Exported as a string. Calls whose step was not reviewed are rejected before execution. |
| `tool_name` | `tool` | MCP tool name. |
| `arguments` | `args` | JSON-compatible tool arguments. |
| `risk_level` | `risk_level` | `low`, `medium`, or `high`. |
| `requires_review` | `requires_review` | High-risk calls pause before MCP execution. |
| `metadata.call_id` | `call_id` | Optional external call ID. |
| `metadata.depends_on` | `depends_on` | Optional call dependency list. |
| `metadata.output_ref` | `output_ref` | Optional produced reference. |

## Current QGIS ExecutionTrace Fields

| QGIS field | GeoTool export | Notes |
| --- | --- | --- |
| `trace_id` | `trace_id` | Stable trace identifier. |
| `task_id` | `task_id` | Links back to TaskPlan. |
| `status` | `final_status` | `success`, `failed`, or `needs_review`. |
| `attempts[].tool_call` | `tool_call_plan.calls[]` / observations | Tool calls are exported as canonical GeoTool calls. |
| `attempts[].observation` | `observations[]` | Uses canonical `call_id`, `tool`, `status`, `output_ref`, `artifacts`, `error_type`, `error_message`, `raw_response`, and `metadata`. |
| `attempts[].metadata.repair_action` | `repair_actions[]` | Maps qgis `update_arguments` to GeoTool `update_args`; qgis evidence is preserved in `metadata`. |

## Adapter Mapping

The Q0 adapter functions are:

- `qgis_task_plan_to_geotool_dict(task_plan, user_query)`
- `geotool_task_plan_to_qgis_legacy_plan(payload)`
- `geotool_tool_call_plan_to_qgis_calls(payload)`
- `qgis_execution_trace_to_geotool_dict(trace, user_query)`

The exported trace seed format is:

```json
{
  "trace_id": "trace-001",
  "task_id": "task-001",
  "user_query": "加载一个矢量图层并截图",
  "task_plan": {},
  "tool_call_plan": {
    "task_id": "task-001",
    "calls": []
  },
  "observations": [
    {
      "call_id": "call-001",
      "tool": "add_layer",
      "status": "success",
      "output_ref": "loaded_points",
      "artifacts": [],
      "error_type": null,
      "error_message": null,
      "raw_response": null,
      "metadata": {
        "attempt_id": "attempt-001",
        "qgis_step_id": 1,
        "qgis_status": "success"
      }
    }
  ],
  "repair_actions": [],
  "final_status": "success",
  "metadata": {
    "source_project": "qgis-workflow-agent",
    "export_version": "geotool-interop-v0.1",
    "schema_version": "geotool-interop-v0.1"
  }
}
```

## First-Stage Non-Goals

The v0.1 adapter does not align these fields yet:

- full GeoTool registry identifiers;
- model-training labels for SFT/DPO;
- parameter-level API schemas;
- SkillRegistry entries;
- frontend trace evidence panels.

## Compatibility Strategy

`qgis-workflow-agent` continues using its internal Pydantic models and legacy UI `Plan` compatibility layer. GeoTool-compatible payloads are generated at the edges for integration tests, JSONL export, and later model training. If GeoTool-LLM changes its schema, the adapter should absorb that difference without changing the runtime execution contracts.

## Execution Mode Switching

The controlled route remains the default:

```powershell
$env:QGIS_AGENT_EXECUTION_MODE = "tool_call_plan"
```

The legacy LangGraph create-agent route remains available for baseline
comparison:

```powershell
$env:QGIS_AGENT_EXECUTION_MODE = "react_agent"
```
