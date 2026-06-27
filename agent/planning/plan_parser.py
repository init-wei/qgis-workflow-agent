#!/usr/bin/env python3
"""TaskPlan parser for LLM JSON output."""

import json
import re
from typing import Any

from pydantic import ValidationError

from agent.planning.plan_schema import TaskPlan


class PlanParseError(ValueError):
    """Raised when raw planner output cannot be parsed as a valid TaskPlan."""


_FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _extract_json_text(raw: str) -> str:
    text = raw.strip()
    match = _FENCED_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    return text


def parse_task_plan_json(raw: dict[str, Any] | str) -> TaskPlan:
    """Parse dict, plain JSON text, or a fenced JSON block into a TaskPlan."""

    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str):
        json_text = _extract_json_text(raw)
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise PlanParseError(f"Planner output is not valid JSON: {exc}") from exc
    else:
        raise PlanParseError(f"Unsupported planner output type: {type(raw).__name__}")

    try:
        return TaskPlan.model_validate(payload)
    except ValidationError as exc:
        raise PlanParseError(f"Planner output does not match TaskPlan schema: {exc}") from exc
