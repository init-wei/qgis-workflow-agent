# Project Documentation

This folder is the single home for project documentation. Current operational docs live at the top level; historical implementation notes keep only what was actually built.

## Current Docs

- [minimal_real_e2e_task.md](minimal_real_e2e_task.md): manual E2E flow for the proven QGIS layer-loading path.
- [web_ui.md](web_ui.md): current Web UI flow and backend endpoints.
- [geotool_llm_integration.md](geotool_llm_integration.md): schema boundary between this runtime and GeoTool-LLM.
- [benchmark_notes.md](benchmark_notes.md): static demo replay evidence and benchmark limits.

## Historical Implementation Notes

- [geotool_llm_history.md](geotool_llm_history.md): completed Q0 work, mapped to code and tests.

## Documentation Policy

- Keep current docs factual and runnable.
- Do not keep open-ended phase task lists after the work is implemented.
- Preserve history as concise implementation notes: what changed, where it lives, and how it was verified.
- Move speculative or superseded design material out of the active docs path unless it still describes current behavior.
