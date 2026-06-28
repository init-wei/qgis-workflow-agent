#!/usr/bin/env python3
"""Simplified evaluate and repair loop for one planned tool call."""

from pathlib import Path
from typing import Iterable

from agent.tools.step_executor import StepExecutor
from agent.tools.tool_contracts import (
    ErrorType,
    EvaluationDecision,
    ExecutionTrace,
    LoopResult,
    RepairAction,
    ToolCallPlan,
    ToolObservation,
)
from agent.tools.tool_registry import ToolRegistry

REPAIRABLE_ERROR_TYPES = {"parameter_error", "missing_file", "qgis_disconnected"}


class ErrorClassifier:
    """Classify the deliberately small Phase 4 error surface."""

    def classify(self, observation: ToolObservation) -> ErrorType:
        if observation.error_type in REPAIRABLE_ERROR_TYPES:
            return observation.error_type
        return "unknown_error"


class StepEvaluator:
    """Convert a tool observation into the next loop decision."""

    def __init__(self, classifier: ErrorClassifier | None = None):
        self.classifier = classifier or ErrorClassifier()

    def evaluate(
        self,
        observation: ToolObservation,
        attempt_number: int,
        max_attempts: int,
    ) -> EvaluationDecision:
        if observation.status == "success":
            return EvaluationDecision(
                outcome="success",
                reason="tool_succeeded",
                message=observation.message,
            )

        error_type = self.classifier.classify(observation)
        if attempt_number >= max_attempts:
            outcome = "ask_human" if error_type == "missing_file" else "abort"
            return EvaluationDecision(
                outcome=outcome,
                reason="max_attempts_exhausted",
                error_type=error_type,
                message=observation.message,
            )

        if error_type == "parameter_error":
            return EvaluationDecision(
                outcome="retry",
                reason="parameter_error_retry",
                error_type=error_type,
                message=observation.message,
            )
        if error_type == "missing_file":
            return EvaluationDecision(
                outcome="ask_human",
                reason="missing_file_requires_human",
                error_type=error_type,
                message=observation.message,
            )
        if error_type == "qgis_disconnected":
            return EvaluationDecision(
                outcome="retry" if observation.retryable else "abort",
                reason=(
                    "qgis_disconnect_retry"
                    if observation.retryable
                    else "qgis_disconnect_not_retryable"
                ),
                error_type=error_type,
                message=observation.message,
            )

        return EvaluationDecision(
            outcome="abort",
            reason="unsupported_error_type",
            error_type=error_type,
            message=observation.message,
        )


class Repairer:
    """Generate deterministic repair actions for Phase 4 supported errors."""

    def repair(self, call: ToolCallPlan, decision: EvaluationDecision) -> RepairAction:
        if decision.error_type == "parameter_error":
            repair_arguments = call.metadata.get("repair_arguments")
            if isinstance(repair_arguments, dict) and repair_arguments:
                return RepairAction(
                    action_type="update_arguments",
                    reason="parameter_repair_arguments_available",
                    updated_arguments={**call.arguments, **repair_arguments},
                )
            return RepairAction(
                action_type="ask_human",
                reason="missing_parameter_repair_arguments",
            )

        if decision.error_type == "qgis_disconnected" and decision.outcome == "retry":
            return RepairAction(
                action_type="update_arguments",
                reason="retry_original_arguments_after_disconnect",
                updated_arguments=dict(call.arguments),
            )

        if decision.error_type == "missing_file":
            return RepairAction(
                action_type="ask_human",
                reason="missing_file_requires_human",
            )

        return RepairAction(action_type="abort", reason="no_repair_available")


class ExecutionLoop:
    """Run one ToolCallPlan through execute, evaluate, and deterministic repair."""

    def __init__(
        self,
        mcp_client,
        registry: ToolRegistry,
        workspace_root: str | Path,
        max_attempts: int = 2,
        step_executor: StepExecutor | None = None,
        evaluator: StepEvaluator | None = None,
        repairer: Repairer | None = None,
    ):
        self.max_attempts = max_attempts
        self.step_executor = step_executor or StepExecutor(
            mcp_client=mcp_client,
            registry=registry,
            workspace_root=workspace_root,
        )
        self.evaluator = evaluator or StepEvaluator()
        self.repairer = repairer or Repairer()

    async def run(
        self,
        call: ToolCallPlan,
        trace: ExecutionTrace,
        allowed_tools: Iterable[str] | None = None,
        approved: bool = False,
    ) -> LoopResult:
        current_call = call
        final_decision: EvaluationDecision | None = None

        for attempt_number in range(1, self.max_attempts + 1):
            attempt = await self.step_executor.execute(
                current_call,
                trace,
                allowed_tools=allowed_tools,
                approved=approved,
            )
            decision = self.evaluator.evaluate(
                attempt.observation,
                attempt_number=attempt_number,
                max_attempts=self.max_attempts,
            )
            attempt.metadata["evaluation"] = decision.model_dump()
            final_decision = decision

            if decision.outcome == "success":
                return LoopResult(
                    status="success",
                    trace=trace,
                    attempts_used=len(trace.attempts),
                    decision=decision,
                )

            repair_action = self.repairer.repair(current_call, decision)
            attempt.metadata["repair_action"] = repair_action.model_dump()

            if repair_action.action_type == "ask_human":
                return LoopResult(
                    status="needs_review",
                    trace=trace,
                    attempts_used=len(trace.attempts),
                    decision=decision,
                )
            if repair_action.action_type == "abort" or decision.outcome == "abort":
                return LoopResult(
                    status="failed",
                    trace=trace,
                    attempts_used=len(trace.attempts),
                    decision=decision,
                )

            current_call = current_call.model_copy(
                update={"arguments": repair_action.updated_arguments}
            )

        decision = final_decision or EvaluationDecision(
            outcome="abort", reason="max_attempts_exhausted"
        )
        return LoopResult(
            status="needs_review" if decision.outcome == "ask_human" else "failed",
            trace=trace,
            attempts_used=len(trace.attempts),
            decision=decision,
        )
