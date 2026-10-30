#!/usr/bin/env python3
"""Simplified evaluate and repair loop for one planned tool call."""

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable
from uuid import uuid4

from agent.tools.risk_review import RiskReviewManager
from agent.tools.retrieval import ApiDocRetriever, RetrievalQueryBuilder
from agent.tools.step_executor import StepExecutor
from agent.tools.tool_contracts import (
    ErrorType,
    EvaluationDecision,
    ExecutionTrace,
    HumanFeedback,
    LoopResult,
    RepairAction,
    StepAttempt,
    ToolCallPlan,
    ToolObservation,
)
from agent.tools.tool_registry import ToolRegistry

REPAIRABLE_ERROR_TYPES = {"parameter_error", "missing_file", "qgis_disconnected"}
READ_ONLY_TOOLS = {"ping", "get_qgis_info", "get_project_info", "get_layers", "get_layer_features"}


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
                outcome="ask_human",
                reason="parameter_change_requires_new_plan_review",
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
            return RepairAction(
                action_type="ask_human",
                reason="parameter_change_requires_new_plan_review",
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
        risk_review_manager: RiskReviewManager | None = None,
        retriever: ApiDocRetriever | None = None,
        retrieval_query_builder: RetrievalQueryBuilder | None = None,
    ):
        self.max_attempts = max_attempts
        self.step_executor = step_executor or StepExecutor(
            mcp_client=mcp_client,
            registry=registry,
            workspace_root=workspace_root,
        )
        self.evaluator = evaluator or StepEvaluator()
        self.repairer = repairer or Repairer()
        self.risk_review_manager = risk_review_manager or RiskReviewManager()
        self.retriever = retriever
        self.retrieval_query_builder = (
            retrieval_query_builder or RetrievalQueryBuilder()
        )

    async def run(
        self,
        call: ToolCallPlan,
        trace: ExecutionTrace,
        allowed_tools: Iterable[str] | None = None,
        approved: bool = False,
        human_feedback: HumanFeedback | None = None,
    ) -> LoopResult:
        current_call = call
        final_decision: EvaluationDecision | None = None
        risk_review_metadata: dict | None = None

        if human_feedback is not None:
            if not self.risk_review_manager.feedback_matches_call(human_feedback, call):
                return self._record_review_block(
                    call=call,
                    trace=trace,
                    feedback=human_feedback,
                    reason="risk_review_call_mismatch",
                )
            if human_feedback.status == "rejected":
                return self._record_review_block(
                    call=call,
                    trace=trace,
                    feedback=human_feedback,
                    reason="risk_review_rejected",
                )
            if human_feedback.status == "pending":
                return self._record_review_block(
                    call=call,
                    trace=trace,
                    feedback=human_feedback,
                    reason="risk_review_pending",
                    status="needs_review",
                    outcome="ask_human",
                )
            if human_feedback.status == "modified":
                return self._record_review_block(
                    call=call,
                    trace=trace,
                    feedback=human_feedback,
                    reason="modified_call_requires_new_plan_review",
                )
            if human_feedback.status == "approved":
                current_call = self.risk_review_manager.apply_feedback(
                    call, human_feedback
                )
                approved = True
                risk_review_metadata = self.risk_review_manager.review_metadata(
                    human_feedback,
                    original_call=call,
                    effective_call=current_call,
                )

        if human_feedback is None and self._should_pause_for_risk_review(
            call, allowed_tools
        ):
            feedback = self.risk_review_manager.build_pending_feedback(call)
            return self._record_review_block(
                call=call,
                trace=trace,
                feedback=feedback,
                reason="risk_review_pending",
                status="needs_review",
                outcome="ask_human",
            )

        for attempt_number in range(1, self.max_attempts + 1):
            retrieval_context = await self._retrieve_initial_context(current_call)
            if retrieval_context is not None:
                current_call = current_call.model_copy(
                    update={
                        "metadata": {
                            **current_call.metadata,
                            "retrieval_context": retrieval_context.model_dump(
                                mode="json"
                            ),
                        }
                    }
                )

            attempt = await self.step_executor.execute(
                current_call,
                trace,
                allowed_tools=allowed_tools,
                approved=approved,
            )
            if retrieval_context is not None:
                attempt.metadata["retrieval_context"] = retrieval_context.model_dump(
                    mode="json"
                )
            if risk_review_metadata is not None:
                attempt.metadata["risk_review"] = risk_review_metadata
            observation = attempt.observation
            raw = observation.raw_response
            if current_call.tool_name not in READ_ONLY_TOOLS and (
                observation.outcome == "unknown"
                or observation.status in {"disconnected", "malformed"}
                or (isinstance(raw, dict) and "exception_type" in raw)
            ):
                decision = EvaluationDecision(
                    outcome="abort",
                    reason="unknown_write_outcome",
                    error_type=observation.error_type,
                    message=observation.message,
                )
                attempt.metadata["evaluation"] = decision.model_dump()
                return LoopResult(
                    status="failed",
                    trace=trace,
                    attempts_used=len(trace.attempts),
                    decision=decision,
                )
            decision = self.evaluator.evaluate(
                observation,
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
            repair_retrieval_context = await self._retrieve_error_context(
                current_call, attempt.observation
            )
            if repair_retrieval_context is not None:
                attempt.metadata["repair_retrieval_context"] = (
                    repair_retrieval_context.model_dump(mode="json")
                )
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
                update={
                    "arguments": repair_action.updated_arguments,
                    "metadata": {
                        **current_call.metadata,
                        **(
                            {
                                "repair_retrieval_context": (
                                    repair_retrieval_context.model_dump(mode="json")
                                )
                            }
                            if repair_retrieval_context is not None
                            else {}
                        ),
                    },
                }
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

    async def _retrieve_initial_context(self, call: ToolCallPlan):
        if self.retriever is None:
            return None
        context = self.retrieval_query_builder.build_initial_context(call)
        return await self.retriever.retrieve(context)

    async def _retrieve_error_context(
        self, call: ToolCallPlan, observation: ToolObservation
    ):
        if self.retriever is None:
            return None
        if observation.error_type not in {
            "parameter_error",
            "missing_file",
            "execution_error",
        }:
            return None
        context = self.retrieval_query_builder.build_error_context(call, observation)
        return await self.retriever.retrieve(context)

    def _should_pause_for_risk_review(
        self, call: ToolCallPlan, allowed_tools: Iterable[str] | None
    ) -> bool:
        if not self.risk_review_manager.requires_risk_review(call):
            return False

        policy_decision = self.step_executor.policy.evaluate(
            call, allowed_tools=allowed_tools, approved=False
        )
        return policy_decision.allowed or policy_decision.requires_review

    def _record_review_block(
        self,
        call: ToolCallPlan,
        trace: ExecutionTrace,
        feedback: HumanFeedback,
        reason: str,
        status: str = "failed",
        outcome: str = "abort",
    ) -> LoopResult:
        started_at = datetime.now(UTC)
        start_counter = perf_counter()
        decision = EvaluationDecision(
            outcome=outcome,
            reason=reason,
            message=feedback.comment,
            metadata={
                "review_id": feedback.review_id,
                "review_status": feedback.status,
            },
        )
        observation = ToolObservation(
            tool_name=call.tool_name,
            status="error",
            error_type="unknown_error",
            message=reason,
            raw_response={"risk_review": feedback.model_dump(mode="json")},
            retryable=False,
        )
        finished_at = datetime.now(UTC)
        attempt = StepAttempt(
            attempt_id=str(uuid4()),
            step_id=call.step_id,
            tool_call=call,
            observation=observation,
            status="blocked",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, round((perf_counter() - start_counter) * 1000)),
            artifacts=[],
            metadata={
                "evaluation": decision.model_dump(),
                "risk_review": self.risk_review_manager.review_metadata(
                    feedback, original_call=call
                ),
            },
        )
        trace.append_attempt(attempt)
        return LoopResult(
            status=status,
            trace=trace,
            attempts_used=len(trace.attempts),
            decision=decision,
        )
