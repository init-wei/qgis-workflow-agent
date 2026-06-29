#!/usr/bin/env python3
"""Small helper for explicit ToolCallPlan execution.

The main LangGraph path currently uses `executor_node`. This helper gives tests
and future nodes a narrow, reusable seam for Q0 explicit execution without
changing the graph contract again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from uuid import uuid4

from pydantic import BaseModel, Field

from agent.tools.execution_loop import ExecutionLoop
from agent.tools.tool_contracts import EvaluationDecision, ExecutionTrace, ToolCallPlan
from agent.tools.tool_registry import ToolRegistry


class ToolCallExecutionResult(BaseModel):
    """Result for a sequence of explicit ToolCallPlan executions."""

    status: str = Field(description="success, failed, or needs_review")
    trace: ExecutionTrace = Field(description="Execution trace for all attempts")
    decision: EvaluationDecision = Field(description="Final loop decision")


async def execute_tool_call_plan_sequence(
    tool_calls: list[ToolCallPlan],
    *,
    mcp_client,
    registry: ToolRegistry,
    workspace_root: str | Path,
    allowed_tools: Iterable[str] | None = None,
    task_id: str | None = None,
    trace: ExecutionTrace | None = None,
) -> ToolCallExecutionResult:
    """Run ToolCallPlan objects through ExecutionLoop until one stops."""

    execution_trace = trace or ExecutionTrace(
        trace_id=f"trace-{uuid4()}",
        task_id=task_id,
        metadata={"runtime": "tool_call_plan_sequence"},
    )
    loop = ExecutionLoop(
        mcp_client=mcp_client,
        registry=registry,
        workspace_root=workspace_root,
    )
    final_result = None
    for call in tool_calls:
        final_result = await loop.run(
            call,
            execution_trace,
            allowed_tools=allowed_tools,
            approved=call.requires_review is False,
        )
        if final_result.status != "success":
            return ToolCallExecutionResult(
                status=final_result.status,
                trace=execution_trace,
                decision=final_result.decision,
            )

    if final_result is None:
        final_decision = EvaluationDecision(
            outcome="abort", reason="no_tool_calls", message="No tool calls to execute."
        )
        execution_trace.status = "failed"
        return ToolCallExecutionResult(
            status="failed", trace=execution_trace, decision=final_decision
        )

    execution_trace.status = "success"
    return ToolCallExecutionResult(
        status="success",
        trace=execution_trace,
        decision=final_result.decision,
    )
