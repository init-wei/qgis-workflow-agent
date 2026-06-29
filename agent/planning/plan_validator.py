#!/usr/bin/env python3
"""Validation rules for strict TaskPlan output."""

from agent.planning.plan_schema import PlanValidationIssue, PlanValidationReport, TaskPlan


COARSE_API_REFERENCES = {
    "gdal",
    "ogr",
    "osr",
    "processing",
    "pyqgis",
    "qgis",
    "QgsProject",
    "QgsVectorLayer",
    "QgsRasterLayer",
}

HIGH_RISK_KEYWORDS = (
    "delete",
    "remove",
    "overwrite",
    "save project",
    "save_project",
    "删除",
    "移除",
    "覆盖",
    "保存工程",
)


class PlanValidator:
    """Validate TaskPlan structure beyond pydantic type checks."""

    def validate(self, task_plan: TaskPlan) -> PlanValidationReport:
        issues: list[PlanValidationIssue] = []

        self._validate_inputs(task_plan, issues)
        self._validate_outputs(task_plan, issues)
        self._validate_steps(task_plan, issues)

        requires_review = (
            task_plan.risk_level == "high"
            or any(step.requires_review for step in task_plan.steps)
        )
        is_valid = not any(issue.severity == "error" for issue in issues)
        summary = self._summary(is_valid, issues, requires_review)

        return PlanValidationReport(
            is_valid=is_valid,
            issues=issues,
            requires_review=requires_review,
            summary=summary,
        )

    def _validate_inputs(self, task_plan: TaskPlan, issues: list[PlanValidationIssue]) -> None:
        for input_spec in task_plan.required_inputs:
            if not input_spec.path and not input_spec.source:
                issues.append(
                    PlanValidationIssue(
                        code="missing_input_path",
                        message=f"Input '{input_spec.input_id}' must include a path or source.",
                        field="required_inputs",
                    )
                )

    def _validate_outputs(self, task_plan: TaskPlan, issues: list[PlanValidationIssue]) -> None:
        if not task_plan.expected_outputs:
            if self._is_display_only_plan(task_plan):
                return
            issues.append(
                PlanValidationIssue(
                    code="missing_output",
                    message="TaskPlan must declare at least one expected output.",
                    field="expected_outputs",
                )
            )
            return

        for output in task_plan.expected_outputs:
            if not output.path or not output.description:
                issues.append(
                    PlanValidationIssue(
                        code="missing_output",
                        message=f"Output '{output.output_id}' must include a path and description.",
                        field="expected_outputs",
                    )
                )

    def _validate_steps(self, task_plan: TaskPlan, issues: list[PlanValidationIssue]) -> None:
        for step in task_plan.steps:
            for api_name in [*step.candidate_gdal_apis, *step.candidate_pyqgis_apis]:
                if self._is_coarse_api_reference(api_name):
                    issues.append(
                        PlanValidationIssue(
                            code="coarse_api_reference",
                            message=f"Step {step.step_id} uses coarse API reference '{api_name}'.",
                            severity="warning",
                            step_id=step.step_id,
                            field="candidate_apis",
                        )
                    )

            if self._is_high_risk_step(step) and not step.requires_review:
                step.requires_review = True
                issues.append(
                    PlanValidationIssue(
                        code="requires_review_missing",
                        message=f"Step {step.step_id} is high risk and must require review.",
                        step_id=step.step_id,
                        field="requires_review",
                    )
                )

    def _is_coarse_api_reference(self, api_name: str) -> bool:
        api_name = api_name.strip()
        if api_name in COARSE_API_REFERENCES:
            return True
        return api_name.lower() in {name.lower() for name in COARSE_API_REFERENCES}

    def _is_high_risk_step(self, step) -> bool:
        description = step.description.lower()
        return (
            step.risk_level == "high"
            or "execute_code" in step.allowed_tools
            or any(keyword in description for keyword in HIGH_RISK_KEYWORDS)
        )

    def _is_display_only_plan(self, task_plan: TaskPlan) -> bool:
        if not task_plan.steps:
            return False
        display_tools = {"add_layer", "load_vector_layer", "capture_map_canvas"}
        processing_tools = {"execute_processing", "execute_code"}
        for step in task_plan.steps:
            allowed_tools = set(step.allowed_tools)
            description = step.description.lower()
            if allowed_tools & processing_tools:
                return False
            if allowed_tools & display_tools:
                continue
            if any(token in description for token in ("load", "加载", "add layer", "截图", "screenshot")):
                continue
            return False
        return True

    def _summary(
        self,
        is_valid: bool,
        issues: list[PlanValidationIssue],
        requires_review: bool,
    ) -> str:
        if not issues:
            return "Plan validation passed." if not requires_review else "Plan validation passed; review required."

        issue_count = len(issues)
        review_note = " Review required." if requires_review else ""
        validity = "invalid" if not is_valid else "valid with warnings"
        return f"Plan is {validity}: {issue_count} issue(s).{review_note}"
