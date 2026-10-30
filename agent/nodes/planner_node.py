#!/usr/bin/env python3
"""
Planner Node - 规划器节点
负责意图识别、Cookbook检索、生成执行计划
"""

from typing import Dict, Any
import logging
from pathlib import Path
import re
from dotenv import load_dotenv
from jinja2 import Template

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm import create_llm

from agent.planning.plan_parser import parse_task_plan_json
from agent.planning.plan_validator import PlanValidator
from agent.planning.processing_preflight import validate_processing_calls
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import task_output_directory
from agent.state import AgentState

load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _get_llm(state: AgentState):
    return create_llm(state.get("llm_config")).bind(response_format={"type": "json_object"})

# 定义System Prompt模板
SYSTEM_PROMPT_TEMPLATE = Template("""你是一个专业的QGIS地理数据处理专家。你的任务是将用户的自然语言需求转化为可执行的QGIS操作步骤。

## 输出格式
请严格按照以下JSON格式输出：

{
  "task_id": "稳定的任务ID，例如 task_align_layers",
  "revision": 1,
  "task_summary": "任务简述（例如: Align raster and vector layers）",
  "risk_level": "low | medium | high",
  "review_status": "pending_review",
  "required_inputs": [
    {
      "input_id": "input_1",
      "description": "输入数据说明",
      "file_id": "用户选择的 file_id",
      "layer_name": "用户选择的图层名；单层文件也填写",
      "path": "所选上传文件的服务器路径",
      "source": "原始文件名"
    }
  ],
  "expected_outputs": [],
  "steps": [
    {
      "step_id": 1,
      "description": "加载用户选定的矢量图层",
      "step_type": "qgis_operation",
      "candidate_gdal_apis": [],
      "candidate_pyqgis_apis": ["QgsProject.addMapLayer"],
      "allowed_tools": ["add_layer"],
      "input_refs": ["input_1"],
      "output_refs": [],
      "risk_level": "low | medium | high",
      "requires_review": false
    }
  ],
  "tool_calls": [
    {
      "step_id": 1,
      "tool_name": "add_layer",
      "arguments": {"path": "exact selected file path", "layer_type": "vector", "name": "selected source name"},
      "risk_level": "low",
      "requires_review": false
    }
  ],
  "metadata": {}
}

## API使用与命名规范（重要）
请务必使用**完整的API名称格式**：

GDAL API往往用于数据处理，PyQGIS API往往用于QGIS内部的图层以及可视化操作


## 注意事项
1. 如果有用户修改意见，请结合意见进行调整
2. 如果有上一轮的执行总结，先了解之前内容是否成功执行，若成功则无需重复步骤，如果失败则在本轮中进行修正
3. 步骤不要拆分太细，每个步骤应该是中低复杂度的目标，且涉及文件必须包含路径信息
4. 文件的导入与导出不要使用GDAL或者PYQGIS的代码，只需在步骤中api留空
5. candidate_gdal_apis和candidate_pyqgis_apis列出所有可能用到的API名称，尽量精确到方法级别（不需要参数细节），如果该步骤不需要使用某类API，可以留空
6. 参考相似案例可以提高准确性
7. 必须返回有效的JSON格式，不要添加任何额外的解释文字
8. 任一步骤需要删除、覆盖、保存工程或 execute_code 时，risk_level 必须为 high 且 requires_review 必须为 true
9. TaskPlan.tool_calls 是实际执行的唯一调用序列；每个 step 必须恰有一个 tool_call，step_id 必须匹配，调用参数需完整并引用 selected file_id 提供的路径
11. 有文件产物时 expected_outputs.path 只填写新的文件名（例如 buffer.gpkg），output_id 只使用英文字母、数字、下划线或连字符；output_refs 指向该产物 ID，调用的输出参数使用完全相同文件名。服务器会在展示审核前绑定为本任务/修订的受控完整路径。禁止绝对输出路径、已有文件和临时输出。步骤与调用的 risk_level 和 requires_review 必须一致，调用顺序必须与步骤一致。
10. 只允许使用有限 MCP 工具。禁止 execute_code、remove_layer、覆盖输入和任意 Processing 算法；可用算法只有 native:reprojectlayer、native:buffer、native:clip、native:dissolve、qgis:statisticsbycategories、native:basicstatisticsforfields
""")

# 定义User Message模板
USER_MESSAGE_TEMPLATE = Template("""用户需求: {{ input_query }}
{% if uploaded_files %}

用户已明确选择的受控上传文件（这些路径和ID是唯一可用的输入，不得猜测或替换）：
{% for file in uploaded_files %}
- file_id: {{ file.file_id }}
  filename: {{ file.filename }}
  path: {{ file.qgis_path }}
  layer_name: {{ file.layer_name }}
  layer_info: {{ file.layer_info }}
{% endfor %}
请在 required_inputs 中引用对应 file_id，并确保加载调用路径与该记录一致。
{% endif %}
{% if log_summary %}

上一轮执行总结:
{{ log_summary }}
{% endif %}
{% if advise %}

用户修改意见:
{{ advise }}
{% endif %}
""")


async def planner_node(state: AgentState) -> Dict[str, Any]:
    """Generate and validate the one reviewable TaskPlan for the current task."""

    logger.info("Planner Node: generating TaskPlan")
    session_id = state["session_id"]
    task_id = state.get("task_id") or session_id
    input_query = state["input_query"]
    advise = state.get("advise")
    log_summary = state.get("log_summary")
    uploaded_files = state.get("uploaded_files", [])
    retry_count = state.get("retry_count", 0)
    previous_plan = state.get("task_plan")
    prior_revision = (
        previous_plan.get("revision", 0)
        if isinstance(previous_plan, dict)
        else getattr(previous_plan, "revision", 0)
    )
    revision = prior_revision + 1 if previous_plan else 1
    llm = _get_llm(state)

    user_message = USER_MESSAGE_TEMPLATE.render(
        input_query=input_query,
        log_summary=log_summary,
        advise=advise,
        uploaded_files=uploaded_files,
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT_TEMPLATE.render()),
        HumanMessage(content=user_message),
    ]

    try:
        response = await llm.ainvoke(messages)
        task_plan = parse_task_plan_json(response.content)
        task_plan = task_plan.model_copy(
            update={
                "task_id": task_id,
                "revision": revision,
                "review_status": "pending_review",
            }
        )
        task_plan = bind_uploaded_files(task_plan, uploaded_files)
        task_plan = bind_task_outputs(task_plan)

        metadata = dict(task_plan.metadata)
        metadata["iteration"] = retry_count + 1
        task_plan = task_plan.model_copy(update={"metadata": metadata})
        validation_report = PlanValidator().validate(task_plan)
        if not validation_report.is_valid:
            errors = [issue for issue in validation_report.issues if issue.severity == "error"][:5]
            details = "; ".join(f"{issue.code}: {issue.message[:200]}" for issue in errors)
            return {
                "task_plan": None,
                "plan_validation": validation_report,
                "planning_error": f"{validation_report.summary} {details}",
                "retry_count": retry_count + 1,
            }


        await validate_processing_calls(task_plan)

        return {
            "task_plan": task_plan,
            "plan_validation": validation_report,
            "planning_error": None,
            "retry_count": retry_count + 1,
            "advise": None,
        }
    except Exception as exc:
        logger.exception("TaskPlan generation failed")
        return {
            "task_plan": None,
            "plan_validation": None,
            "planning_error": str(exc),
            "retry_count": retry_count + 1,
        }


def bind_uploaded_files(task_plan, uploaded_files: list[dict[str, Any]]):
    """Bind model-selected inputs to server-owned uploaded file identities and paths."""

    if not uploaded_files:
        if task_plan.required_inputs or any(
            call.tool_name in {"add_layer", "execute_processing"}
            for call in task_plan.tool_calls
        ):
            raise ValueError("请先上传或选择输入文件，再继续。")
        return task_plan
    if not task_plan.required_inputs:
        raise ValueError("TaskPlan omitted the selected uploaded file input.")

    files_by_id = {str(file["file_id"]): file for file in uploaded_files}
    bound_inputs = []
    used_file_ids: set[str] = set()
    for input_spec in task_plan.required_inputs:
        file_id = input_spec.file_id
        if not file_id and len(uploaded_files) == 1 and len(task_plan.required_inputs) == 1:
            file_id = str(uploaded_files[0]["file_id"])
        selected = files_by_id.get(str(file_id)) if file_id else None
        if selected is None:
            raise ValueError(f"TaskPlan input '{input_spec.input_id}' does not reference a selected file_id.")
        used_file_ids.add(str(file_id))
        bound_inputs.append(
            input_spec.model_copy(
                update={
                    "file_id": str(file_id),
                    "path": selected["qgis_path"],
                    "source": selected["filename"],
                    "layer_name": selected["layer_name"],
                    "sha256": selected.get("sha256"),
                }
            )
        )

    inputs_by_id = {input_spec.input_id: input_spec for input_spec in bound_inputs}
    steps_by_id = {step.step_id: step for step in task_plan.steps}
    replacements = {original.path: bound.path for original, bound in zip(task_plan.required_inputs, bound_inputs)
                    if original.path}

    def bind_value(value):
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, dict):
            return {key: bind_value(nested) for key, nested in value.items()}
        if isinstance(value, list):
            return [bind_value(nested) for nested in value]
        return value

    bound_calls = []
    for call in task_plan.tool_calls:
        step = steps_by_id.get(call.step_id)
        arguments = bind_value(call.arguments)
        if call.tool_name == "add_layer" and step and len(step.input_refs) == 1:
            input_spec = inputs_by_id.get(step.input_refs[0])
            if input_spec is None:
                if step.input_refs[0] in {output.output_id for output in task_plan.expected_outputs}:
                    bound_calls.append(call.model_copy(update={"arguments": arguments}))
                    continue
                raise ValueError(f"Step {step.step_id} does not reference its selected input.")
            selected = files_by_id[input_spec.file_id]
            name = arguments.get("name")
            if name is not None and not isinstance(name, str):
                raise ValueError("The display name must be text.")
            arguments.update(
                {
                    "path": input_spec.path,
                    "layer_type": "vector",
                    "name": name if name and name.strip() else (input_spec.layer_name or Path(selected["filename"]).stem),
                }
            )
        bound_calls.append(call.model_copy(update={"arguments": arguments}))

    if not used_file_ids:
        raise ValueError("TaskPlan does not use a selected uploaded file.")
    return task_plan.model_copy(
        update={"required_inputs": bound_inputs, "tool_calls": bound_calls}
    )


def bind_task_outputs(task_plan):
    """Materialize leaf output filenames once, before displaying the reviewable plan."""
    directory = task_output_directory(task_plan.task_id, task_plan.revision, Path.cwd())
    replacements = {}
    outputs = []
    for output in task_plan.expected_outputs:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", output.output_id):
            raise ValueError("Output ID cannot identify an artifact directory")
        path = output.path
        if path and Path(path).name == path and path not in {".", ".."} and "\\" not in path:
            bound = str(directory / output.output_id / path)
            replacements[path] = bound
            output = output.model_copy(update={"path": bound})
        outputs.append(output)

    def replace(value):
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, dict):
            return {key: replace(nested) for key, nested in value.items()}
        if isinstance(value, list):
            return [replace(nested) for nested in value]
        return value

    return task_plan.model_copy(update={
        "expected_outputs": outputs,
        "tool_calls": [call.model_copy(update={"arguments": replace(call.arguments)})
                       for call in task_plan.tool_calls],
    })
