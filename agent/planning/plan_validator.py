#!/usr/bin/env python3
"""Validation rules for strict TaskPlan output."""

from pathlib import Path

from agent.planning.plan_schema import PlanValidationIssue, PlanValidationReport, TaskPlan
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import (
    call_output_paths, new_output_path, resolve_owned_output_path,
)
from qgis_mcp_server.qgis_mcp_plugin.processing_policy import SUPPORTED_PROCESSING_ALGORITHMS


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

SUPPORTED_MCP_TOOLS = {
    "ping",
    "get_qgis_info",
    "get_project_info",
    "get_layers",
    "add_layer",
    "get_layer_features",
    "execute_processing",
    "capture_map_canvas",
    "save_project",
}



class PlanValidator:
    """Validate TaskPlan structure beyond pydantic type checks."""

    def validate(
        self, task_plan: TaskPlan, *, completed_step_ids: frozenset[int] = frozenset()
    ) -> PlanValidationReport:
        """Validate a plan; the runtime alone supplies a durably verified completed prefix."""
        issues: list[PlanValidationIssue] = []

        self._validate_inputs(task_plan, issues)
        self._validate_outputs(task_plan, issues)
        self._validate_steps(task_plan, issues)
        self._validate_references(task_plan, issues)
        self._validate_tool_calls(task_plan, issues)
        self._validate_output_safety(task_plan, issues, completed_step_ids)

        requires_review = (
            task_plan.risk_level == "high"
            or any(
                step.requires_review or self._is_high_risk_step(step)
                for step in task_plan.steps
            )
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
            if self._does_not_create_files(task_plan):
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
        seen_step_ids: set[int] = set()
        for step in task_plan.steps:
            if step.step_id in seen_step_ids:
                issues.append(
                    PlanValidationIssue(
                        code="duplicate_step_id",
                        message=f"Step ID {step.step_id} is used more than once.",
                        step_id=step.step_id,
                        field="step_id",
                    )
                )
            seen_step_ids.add(step.step_id)

            for tool_name in step.allowed_tools:
                if tool_name not in SUPPORTED_MCP_TOOLS:
                    issues.append(
                        PlanValidationIssue(
                            code="unsupported_tool",
                            message=f"Tool '{tool_name}' is not supported.",
                            step_id=step.step_id,
                            field="allowed_tools",
                        )
                    )

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
                issues.append(
                    PlanValidationIssue(
                        code="requires_review_missing",
                        message=f"Step {step.step_id} is high risk and must require review.",
                        step_id=step.step_id,
                        field="requires_review",
                    )
                )

    def _validate_references(
        self, task_plan: TaskPlan, issues: list[PlanValidationIssue]
    ) -> None:
        inputs_by_id = {input_spec.input_id: input_spec for input_spec in task_plan.required_inputs}
        outputs_by_id = {output.output_id: output for output in task_plan.expected_outputs}
        output_producers: dict[str, int] = {}

        if len(inputs_by_id) != len(task_plan.required_inputs):
            issues.append(
                PlanValidationIssue(
                    code="duplicate_input_id",
                    message="Input IDs must be unique.",
                    field="required_inputs",
                )
            )
        if len(outputs_by_id) != len(task_plan.expected_outputs):
            issues.append(
                PlanValidationIssue(
                    code="duplicate_output_id",
                    message="Output IDs must be unique.",
                    field="expected_outputs",
                )
            )

        for step in task_plan.steps:
            if len(set(step.input_refs)) != len(step.input_refs):
                issues.append(
                    PlanValidationIssue(
                        code="duplicate_input_reference",
                        message=f"Step {step.step_id} repeats an input reference.",
                        step_id=step.step_id,
                        field="input_refs",
                    )
                )
            if len(set(step.output_refs)) != len(step.output_refs):
                issues.append(
                    PlanValidationIssue(
                        code="duplicate_output_reference",
                        message=f"Step {step.step_id} repeats an output reference.",
                        step_id=step.step_id,
                        field="output_refs",
                    )
                )

            for input_ref in step.input_refs:
                if input_ref in inputs_by_id:
                    continue
                producer_step = output_producers.get(input_ref)
                if producer_step is None:
                    issues.append(
                        PlanValidationIssue(
                            code="unknown_input_reference",
                            message=f"Step {step.step_id} references unknown input '{input_ref}'.",
                            step_id=step.step_id,
                            field="input_refs",
                        )
                    )
                elif producer_step >= step.step_id:
                    issues.append(
                        PlanValidationIssue(
                            code="unordered_input_reference",
                            message=f"Step {step.step_id} references output '{input_ref}' before it is produced.",
                            step_id=step.step_id,
                            field="input_refs",
                        )
                    )

            for output_ref in step.output_refs:
                if output_ref not in outputs_by_id:
                    issues.append(
                        PlanValidationIssue(
                            code="unknown_output_reference",
                            message=f"Step {step.step_id} references unknown output '{output_ref}'.",
                            step_id=step.step_id,
                            field="output_refs",
                        )
                    )
                elif output_ref in output_producers:
                    issues.append(
                        PlanValidationIssue(
                            code="duplicate_output_producer",
                            message=f"Output '{output_ref}' is produced by multiple steps.",
                            step_id=step.step_id,
                            field="output_refs",
                        )
                    )
                else:
                    output_producers[output_ref] = step.step_id

        used_inputs = {ref for step in task_plan.steps for ref in step.input_refs}
        for input_id in inputs_by_id:
            if input_id not in used_inputs:
                issues.append(PlanValidationIssue(
                    code="unused_input", message=f"Input '{input_id}' is not consumed by a step.",
                    field="required_inputs",
                ))

        for output_id in outputs_by_id:
            if output_id not in output_producers:
                issues.append(
                    PlanValidationIssue(
                        code="unproduced_output",
                        message=f"Expected output '{output_id}' is not produced by a step.",
                        field="expected_outputs",
                    )
                )

    def _validate_tool_calls(
        self, task_plan: TaskPlan, issues: list[PlanValidationIssue]
    ) -> None:
        if "tool_calls" in task_plan.metadata:
            issues.append(
                PlanValidationIssue(
                    code="hidden_tool_calls",
                    message="Executable calls must be declared in TaskPlan.tool_calls.",
                    field="metadata.tool_calls",
                )
            )

        if [call.step_id for call in task_plan.tool_calls] != [step.step_id for step in task_plan.steps]:
            issues.append(
                PlanValidationIssue(
                    code="unordered_tool_calls",
                    message="Tool calls must match the declared step sequence exactly.",
                    field="tool_calls",
                )
            )

        steps_by_id = {step.step_id: step for step in task_plan.steps}
        calls_by_step: dict[int, list] = {}
        for call in task_plan.tool_calls:
            calls_by_step.setdefault(call.step_id, []).append(call)
            step = steps_by_id.get(call.step_id)
            if step is None:
                issues.append(
                    PlanValidationIssue(
                        code="unknown_tool_call_step",
                        message=f"Tool call references unknown step {call.step_id}.",
                        step_id=call.step_id,
                        field="tool_calls",
                    )
                )
            elif call.tool_name not in step.allowed_tools:
                issues.append(
                    PlanValidationIssue(
                        code="tool_call_not_allowed",
                        message=f"Step {call.step_id} does not allow tool '{call.tool_name}'.",
                        step_id=call.step_id,
                        field="tool_calls",
                    )
                )

            if step is not None and (
                call.risk_level != step.risk_level
                or call.requires_review != step.requires_review
            ):
                issues.append(
                    PlanValidationIssue(
                        code="inconsistent_call_risk",
                        message=f"Step {call.step_id} and its call must declare identical risk and review requirements.",
                        step_id=call.step_id,
                        field="tool_calls",
                    )
                )

            if call.tool_name == 'save_project' and (call.risk_level != 'high' or not call.requires_review):
                issues.append(PlanValidationIssue(
                    code='project_save_requires_review',
                    message='Saving a new project must be declared high risk and require separate review.',
                    step_id=call.step_id, field='tool_calls',
                ))

            if step is not None:
                references = {item.input_id: item.path for item in task_plan.required_inputs}
                references.update({item.output_id: item.path for item in task_plan.expected_outputs})
                actual_inputs = []
                if call.tool_name == "add_layer":
                    actual_inputs = [call.arguments.get("path")]
                elif call.tool_name == "execute_processing":
                    parameters = call.arguments.get("parameters", {})
                    if isinstance(parameters, dict):
                        actual_inputs = [value for key, value in parameters.items()
                                         if "INPUT" in key.upper().split("_") or key.upper() == "OVERLAY"]
                declared_inputs = [references.get(ref) for ref in step.input_refs]
                if sorted(str(path) for path in actual_inputs) != sorted(str(path) for path in declared_inputs):
                    issues.append(PlanValidationIssue(
                        code="call_input_reference_mismatch",
                        message=f"Step {call.step_id} must consume exactly its declared input references.",
                        step_id=call.step_id, field="tool_calls.arguments",
                    ))

            if call.tool_name == "add_layer" and step is not None:
                references = {item.input_id: item.path for item in task_plan.required_inputs}
                references.update({item.output_id: item.path for item in task_plan.expected_outputs})
                if len(step.input_refs) != 1 or step.input_refs[0] not in references:
                    issues.append(
                        PlanValidationIssue(
                            code="load_input_reference_required",
                            message=f"Step {call.step_id} must reference the layer it loads.",
                            step_id=call.step_id,
                            field="input_refs",
                        )
                    )
                elif call.arguments.get("path") != references[step.input_refs[0]]:
                    issues.append(
                        PlanValidationIssue(
                            code="load_input_path_mismatch",
                            message=f"Step {call.step_id} must load the path of its declared input.",
                            step_id=call.step_id,
                            field="tool_calls.arguments.path",
                        )
                    )

            if call.tool_name not in SUPPORTED_MCP_TOOLS:
                issues.append(
                    PlanValidationIssue(
                        code="unsupported_tool_call",
                        message=f"Tool call '{call.tool_name}' is not supported.",
                        step_id=call.step_id,
                        field="tool_calls",
                    )
                )
            else:
                argument_error = self._tool_argument_error(call.tool_name, call.arguments)
                if argument_error:
                    issues.append(
                        PlanValidationIssue(
                            code="invalid_tool_arguments",
                            message=f"Step {call.step_id}: {argument_error}",
                            step_id=call.step_id,
                            field="tool_calls.arguments",
                        )
                    )

        for step in task_plan.steps:
            step_calls = calls_by_step.get(step.step_id, [])
            if not step_calls:
                issues.append(
                    PlanValidationIssue(
                        code="missing_tool_call",
                        message=f"Step {step.step_id} must declare its exact tool call.",
                        step_id=step.step_id,
                        field="tool_calls",
                    )
                )
            elif len(step_calls) > 1:
                issues.append(
                    PlanValidationIssue(
                        code="duplicate_tool_call",
                        message=f"Step {step.step_id} has more than one tool call.",
                        step_id=step.step_id,
                        field="tool_calls",
                    )
                )

    def _validate_output_safety(
        self, task_plan: TaskPlan, issues: list[PlanValidationIssue],
        completed_step_ids: frozenset[int],
    ) -> None:
        """New destinations stay exclusive; completed prefix files stay owned and readable."""
        prefix = {step.step_id for step in task_plan.steps[:len(completed_step_ids)]}
        if completed_step_ids != prefix:
            issues.append(PlanValidationIssue(
                code="invalid_completed_prefix", message="Completed steps must be an ordered prefix.",
                field="steps",
            ))
            completed_step_ids = frozenset()
        outputs = {item.output_id: item.path for item in task_plan.expected_outputs}
        completed_paths = {
            outputs[ref] for step in task_plan.steps if step.step_id in completed_step_ids
            for ref in step.output_refs if ref in outputs
        }
        destinations = [path for path in outputs.values() if path]
        steps = {step.step_id: step for step in task_plan.steps}
        for call in task_plan.tool_calls:
            actual = list(call_output_paths(call.tool_name, call.arguments))
            step = steps.get(call.step_id)
            expected = [outputs.get(ref) for ref in step.output_refs] if step else []
            if sorted(actual) != sorted(path for path in expected if path):
                issues.append(PlanValidationIssue(
                    code="output_reference_mismatch",
                    message=f"Step {call.step_id} must write exactly its declared output references.",
                    field="tool_calls.arguments", step_id=call.step_id,
                ))
            destinations.extend(actual)
        if len(set(outputs.values())) != len(outputs):
            issues.append(PlanValidationIssue(
                code="duplicate_output_path", message="Outputs must use distinct new paths.",
                field="expected_outputs",
            ))
        directories = [Path(path).resolve().parent for path in outputs.values() if path]
        if len(set(directories)) != len(directories):
            issues.append(PlanValidationIssue(
                code="duplicate_output_directory",
                message="Each artifact must reserve a different new directory.",
                field="expected_outputs",
            ))
        inputs = {Path(item.path.split("|", 1)[0]).resolve()
                  for item in task_plan.required_inputs if item.path}
        for destination in destinations:
            try:
                if destination in completed_paths:
                    target = resolve_owned_output_path(destination, Path.cwd(),
                                                       task_id=task_plan.task_id, revision=task_plan.revision)
                    if not target.is_file():
                        raise ValueError("Completed output is no longer available")
                    try:
                        with target.open('rb') as stream:
                            stream.read(1)
                    except OSError as exc:
                        raise ValueError("Completed output is not readable") from exc
                else:
                    target = new_output_path(destination, Path.cwd(),
                                             task_id=task_plan.task_id, revision=task_plan.revision)
                if target in inputs:
                    raise ValueError("Output cannot overwrite a selected input file")
            except ValueError as exc:
                issues.append(PlanValidationIssue(
                    code="unsafe_output_path", message=str(exc), field="expected_outputs",
                ))

    def _tool_argument_error(self, tool_name: str, arguments: dict) -> str | None:
        from qgis_mcp_server.qgis_mcp_server import validate_tool_arguments

        try:
            validate_tool_arguments(tool_name, arguments)
        except ValueError as exc:
            return str(exc)
        if tool_name == "add_layer":
            if not isinstance(arguments.get("path"), str) or not arguments["path"]:
                return "add_layer requires a path."
            if arguments.get("layer_type") not in {"vector", "raster"}:
                return "add_layer requires layer_type 'vector' or 'raster'."
        elif tool_name == "get_layer_features":
            if not isinstance(arguments.get("layer_id"), str) or not arguments["layer_id"]:
                return "get_layer_features requires layer_id."
        elif tool_name == "execute_processing":
            algorithm = arguments.get("algorithm")
            parameters = arguments.get("parameters")
            if algorithm not in SUPPORTED_PROCESSING_ALGORITHMS:
                return f"Unsupported processing algorithm '{algorithm}'."
            if not isinstance(parameters, dict) or not parameters:
                return "execute_processing requires non-empty parameters."
        elif tool_name in {"capture_map_canvas", "save_project"}:
            if not isinstance(arguments.get("path"), str) or not arguments["path"]:
                return f"{tool_name} requires a path."
            if tool_name == 'save_project' and Path(arguments['path']).suffix != '.qgz':
                return "save_project requires a new .qgz project."
        elif arguments:
            return f"{tool_name} does not accept arguments."
        return None

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

    def _does_not_create_files(self, task_plan: TaskPlan) -> bool:
        # Metadata and loading can return business results without creating a file.
        non_file_tools = {
            "ping", "get_qgis_info", "get_project_info", "get_layers",
            "get_layer_features", "add_layer",
        }
        return bool(task_plan.steps and task_plan.tool_calls) and all(
            call.tool_name in non_file_tools for call in task_plan.tool_calls
        )

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
