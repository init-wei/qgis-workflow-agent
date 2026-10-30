"""Expose the exact calls already reviewed as part of a TaskPlan."""

from agent.planning.plan_schema import TaskPlan, ToolCallPlan


def compile_task_plan_to_tool_calls(task_plan: TaskPlan) -> list[ToolCallPlan]:
    """Return TaskPlan calls verbatim; no call is inferred from step text."""

    return list(task_plan.tool_calls)
