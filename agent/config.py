#!/usr/bin/env python3
"""Runtime configuration helpers."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

ExecutionMode = Literal["react_agent", "tool_call_plan"]
DEFAULT_EXECUTION_MODE: ExecutionMode = "tool_call_plan"
VALID_EXECUTION_MODES: set[str] = {"react_agent", "tool_call_plan"}


def load_execution_mode(pyproject_path: str | Path | None = "pyproject.toml") -> ExecutionMode:
    """Load the execution mode from env first, then pyproject, then default."""

    env_mode = os.getenv("QGIS_AGENT_EXECUTION_MODE")
    if env_mode:
        return _validate_execution_mode(env_mode)

    if pyproject_path is None:
        return DEFAULT_EXECUTION_MODE

    path = Path(pyproject_path)
    if not path.exists():
        return DEFAULT_EXECUTION_MODE

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    mode = (
        data.get("tool", {})
        .get("qgis_agent", {})
        .get("execution", {})
        .get("mode")
    )
    if mode is None:
        return DEFAULT_EXECUTION_MODE
    return _validate_execution_mode(str(mode))


def _validate_execution_mode(mode: str) -> ExecutionMode:
    if mode not in VALID_EXECUTION_MODES:
        raise ValueError(
            "Invalid QGIS agent execution mode. Expected one of: "
            f"{', '.join(sorted(VALID_EXECUTION_MODES))}."
        )
    return mode  # type: ignore[return-value]
