# Phase 9/10 Benchmark Notes

## Purpose

Phase 9/10 packages replayable evidence for the controlled execution architecture. The current benchmark artifacts compare what can be inspected from static traces, not real QGIS runtime speed or production success rates.

## Reproducible Commands

```bash
uv run python scripts/replay_demo_cases.py
uv run python scripts/replay_demo_cases.py --case-id failure_repair
uv run pytest tests/test_demo_cases.py tests/test_backend_demo_cases.py -q
```

These commands run without QGIS Desktop, MCP, PostgreSQL, or an LLM key.

## Demo Cases

| Case | Evidence | What It Demonstrates |
| --- | --- | --- |
| `simple_success` | one successful `ExecutionTrace` attempt | tool call, normalized observation, artifact recording |
| `failure_repair` | failed attempt, repair action, successful retry | parameter error is explainable and repair metadata is replayable |
| `skill_draft_preview` | successful trace, `SkillDraft`, reuse preview | a trace can be generalized without registering or executing a skill |

## Baseline vs Controlled Execution

The baseline black-box executor can produce a final message, but it does not consistently expose a structured sequence of tool calls, observations, repair decisions, and artifacts. The controlled execution path records these fields in `ExecutionTrace`, making demo replay and review possible.

Current evidence supports these claims:

- Trace attempts are JSON-serializable and replayable from fixtures.
- Repair metadata can be inspected after a parameter error.
- SkillDraft extraction can generalize a successful trace into variables.
- The demo API and trace viewer inspect static evidence without external services.

Current evidence does not support these claims:

- Controlled execution is faster than legacy execution.
- Controlled execution has a higher real-world QGIS task success rate.
- SkillDraft reuse is production-ready or automatically registered.

## Limits

The fixtures are mock traces. They are useful for architecture inspection, CI validation, demos, and documentation, but they are not a substitute for a real QGIS benchmark suite. A future benchmark should run fixed datasets through both the legacy executor and controlled loop, record identical task definitions, and publish the raw traces and outputs.
