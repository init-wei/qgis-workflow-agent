#!/usr/bin/env python3
"""Human-in-the-loop risk review helpers for controlled execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent.tools.tool_contracts import HumanFeedback, ToolCallPlan

RISKY_TOOL_KEYWORDS = (
    "delete",
    "remove",
    "overwrite",
    "save_project",
    "save-project",
    "save project",
)


class RiskReviewManager:
    """Build and apply risk-review feedback for planned tool calls."""

    def requires_risk_review(self, call: ToolCallPlan) -> bool:
        if call.tool_name == "execute_code":
            return True
        if call.risk_level == "high" or call.requires_review:
            return True
        return self._contains_risky_semantics(call.tool_name) or (
            self._contains_risky_semantics(call.arguments)
        )

    def build_pending_feedback(
        self,
        call: ToolCallPlan,
        reason: str = "risk_review_required",
        comment: str | None = None,
        reviewer: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HumanFeedback:
        return HumanFeedback(
            review_id=str(uuid4()),
            status="pending",
            tool_call=call,
            reason=reason,
            comment=comment,
            reviewer=reviewer,
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )

    def feedback_matches_call(
        self, feedback: HumanFeedback, call: ToolCallPlan
    ) -> bool:
        return (
            feedback.tool_call.step_id == call.step_id
            and feedback.tool_call.tool_name == call.tool_name
        )

    def apply_feedback(
        self, call: ToolCallPlan, feedback: HumanFeedback
    ) -> ToolCallPlan:
        if feedback.status != "modified":
            return call
        return call.model_copy(update={"arguments": feedback.modified_arguments or {}})

    def review_metadata(
        self,
        feedback: HumanFeedback,
        original_call: ToolCallPlan,
        effective_call: ToolCallPlan | None = None,
    ) -> dict[str, Any]:
        metadata = feedback.model_dump(mode="json")
        if feedback.status == "modified":
            metadata["original_arguments"] = dict(original_call.arguments)
            metadata["modified_arguments"] = dict(
                (effective_call or original_call).arguments
            )
        return metadata

    def _contains_risky_semantics(self, value: Any) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, str):
            normalized = value.lower().replace("-", "_")
            return any(
                keyword.replace("-", "_") in normalized
                for keyword in RISKY_TOOL_KEYWORDS
            )
        if isinstance(value, dict):
            for key, nested_value in value.items():
                if self._contains_risky_semantics(str(key)):
                    if nested_value is False or nested_value is None:
                        continue
                    return True
                if self._contains_risky_semantics(nested_value):
                    return True
            return False
        if isinstance(value, list):
            return any(self._contains_risky_semantics(item) for item in value)
        return False
