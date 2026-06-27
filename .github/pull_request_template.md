# Pull Request

## Summary

- What changed:
- Why it is needed:
- Related issue:

## Engineering Phase

- [ ] Phase 0: project structure, CI, test baseline
- [ ] Phase 1: state contract and plan schema
- [ ] Phase 2: MCP adapter and tool policy
- [ ] Phase 3: ExecutionTrace and step-level executor
- [ ] Phase 4: execution loop, evaluation, repair
- [ ] Phase 5: API-RAG and error-driven retrieval
- [ ] Phase 6: HITL review system
- [ ] Phase 7: SkillDraft extraction
- [ ] Phase 8: Skill registry and reuse
- [ ] Phase 9: trace viewer or skill panel
- [ ] Phase 10: benchmark, demo, packaging
- [ ] Other:

## Area

- [ ] Planner / plan validation
- [ ] API-RAG / document parsing
- [ ] MCP client / QGIS plugin integration
- [ ] Tool policy / path policy / code policy
- [ ] Step executor / execution loop
- [ ] ExecutionTrace / observability
- [ ] HITL review flow
- [ ] Skill extraction / registry
- [ ] Database / migrations
- [ ] FastAPI backend / SSE
- [ ] Vue frontend
- [ ] Tests / CI / docs

## Compatibility And Risk

- Backward-compatible with existing `agent/` state and API routes: yes / no / not applicable
- Requires database migration: yes / no
- Requires QGIS desktop or MCP server changes: yes / no
- Touches high-risk operations such as `execute_code`, file overwrite, delete, or project save: yes / no

Notes:

## Validation

Commands run:

```text
# paste exact commands and results here
```

Manual validation:

- [ ] Not needed
- [ ] Mock MCP path tested
- [ ] Real QGIS MCP path tested
- [ ] UI flow tested
- [ ] Screenshot / artifact output checked

## Review Checklist

- [ ] Plan or state schema changes are documented.
- [ ] Tool calls have explicit policy/risk behavior where relevant.
- [ ] Errors are observable and include enough context for repair/debugging.
- [ ] New behavior has unit or integration coverage, or the test gap is explained.
- [ ] No secrets, local absolute paths, or user data are committed.
- [ ] README/docs are updated when user-facing behavior changes.
