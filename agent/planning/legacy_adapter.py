#!/usr/bin/env python3
"""Adapters between the strict TaskPlan contract and the legacy UI Plan shape."""

from uuid import uuid4

from agent.planning.plan_schema import PlanStep, TaskPlan
from agent.state import Plan, Step


def _risk_from_metadata(metadata: dict) -> str:
    risk_level = metadata.get("risk_level", "medium")
    if risk_level not in {"low", "medium", "high"}:
        return "medium"
    return risk_level


def _review_status_from_metadata(metadata: dict) -> str:
    review_status = metadata.get("review_status", "draft")
    if review_status not in {"draft", "pending_review", "approved", "rejected"}:
        return "draft"
    return review_status


def legacy_plan_to_task_plan(plan: Plan, user_query: str | None = None) -> TaskPlan:
    """Convert the current Plan/Step shape into a TaskPlan for compatibility."""

    metadata = plan.metadata.copy()
    task_id = metadata.get("task_plan_id") or f"task-{uuid4()}"
    metadata["legacy_task"] = plan.task
    if user_query:
        metadata["user_query"] = user_query

    steps = [
        PlanStep(
            step_id=step.step_id,
            description=step.description,
            candidate_gdal_apis=list(step.gdal_api),
            candidate_pyqgis_apis=list(step.pyqgis_api),
            allowed_tools=list(metadata.get("allowed_tools", [])),
            input_refs=[],
            output_refs=[],
            risk_level=_risk_from_metadata(metadata),
            requires_review=_risk_from_metadata(metadata) == "high",
        )
        for step in plan.steps
    ]

    return TaskPlan(
        task_id=task_id,
        task_summary=plan.task,
        steps=steps,
        risk_level=_risk_from_metadata(metadata),
        review_status=_review_status_from_metadata(metadata),
        required_inputs=[],
        expected_outputs=[],
        metadata=metadata,
    )


def task_plan_to_legacy_plan(task_plan: TaskPlan) -> Plan:
    """Convert a TaskPlan into the legacy Plan shape used by UI/API-RAG/Executor."""

    metadata = task_plan.metadata.copy()
    metadata.update(
        {
            "task_plan_id": task_plan.task_id,
            "risk_level": task_plan.risk_level,
            "review_status": task_plan.review_status,
            "validation_summary": metadata.get("validation_summary", ""),
        }
    )

    return Plan(
        task=task_plan.task_summary,
        steps=[
            Step(
                step_id=step.step_id,
                description=step.description,
                gdal_api=list(step.candidate_gdal_apis),
                pyqgis_api=list(step.candidate_pyqgis_apis),
            )
            for step in task_plan.steps
        ],
        metadata=metadata,
    )
