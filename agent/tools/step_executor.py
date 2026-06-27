#!/usr/bin/env python3
"""Minimal controlled executor for one planned MCP tool call."""

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable
from uuid import uuid4

from agent.tools.mcp_response_parser import MCPResponseParser
from agent.tools.tool_contracts import (
    ExecutionTrace,
    StepAttempt,
    ToolCallPlan,
    ToolObservation,
)
from agent.tools.tool_policy import ToolPolicy
from agent.tools.tool_registry import ToolRegistry


class StepExecutor:
    """Execute a single ToolCallPlan and append a replay-friendly trace attempt."""

    def __init__(
        self,
        mcp_client,
        registry: ToolRegistry,
        workspace_root: str | Path,
        parser: MCPResponseParser | None = None,
        policy: ToolPolicy | None = None,
    ):
        self.mcp_client = mcp_client
        self.registry = registry
        self.parser = parser or MCPResponseParser()
        self.policy = policy or ToolPolicy(
            registry=registry, workspace_root=workspace_root
        )

    async def execute(
        self,
        call: ToolCallPlan,
        trace: ExecutionTrace,
        allowed_tools: Iterable[str] | None = None,
        approved: bool = False,
    ) -> StepAttempt:
        started_at = datetime.now(UTC)
        start_counter = perf_counter()

        policy_decision = self.policy.evaluate(
            call, allowed_tools=allowed_tools, approved=approved
        )
        if not policy_decision.allowed:
            observation = ToolObservation(
                tool_name=call.tool_name,
                status="error",
                error_type="unknown_error",
                message=policy_decision.message or policy_decision.reason,
                raw_response=policy_decision.model_dump(),
                retryable=False,
            )
            attempt = self._build_attempt(
                call=call,
                observation=observation,
                status="blocked",
                started_at=started_at,
                start_counter=start_counter,
                metadata={"policy_reason": policy_decision.reason},
            )
            trace.append_attempt(attempt)
            return attempt

        try:
            raw_response = await self.mcp_client.call_tool(
                call.tool_name, call.arguments
            )
        except BaseException as exc:
            observation = self.parser.parse(call.tool_name, exc)
        else:
            observation = self.parser.parse(call.tool_name, raw_response)

        status = "success" if observation.status == "success" else "failed"
        attempt = self._build_attempt(
            call=call,
            observation=observation,
            status=status,
            started_at=started_at,
            start_counter=start_counter,
        )
        trace.append_attempt(attempt)
        return attempt

    def _build_attempt(
        self,
        call: ToolCallPlan,
        observation: ToolObservation,
        status: str,
        started_at: datetime,
        start_counter: float,
        metadata: dict | None = None,
    ) -> StepAttempt:
        finished_at = datetime.now(UTC)
        duration_ms = max(0, round((perf_counter() - start_counter) * 1000))
        return StepAttempt(
            attempt_id=str(uuid4()),
            step_id=call.step_id,
            tool_call=call,
            observation=observation,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            artifacts=list(observation.artifacts),
            metadata=metadata or {},
        )
