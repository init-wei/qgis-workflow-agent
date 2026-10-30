#!/usr/bin/env python3
"""
Planner Node - 规划器节点
负责意图识别、Cookbook检索、生成执行计划
"""

from typing import Dict, Any
import json
import logging
import math
from pathlib import Path
import re
from dotenv import load_dotenv
from jinja2 import Template

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm import create_llm

from agent.planning.plan_parser import parse_task_plan_json
from agent.planning.plan_schema import TaskPlan
from agent.planning.plan_validator import PlanValidator
from agent.planning.processing_preflight import processing_capabilities, validate_processing_calls
from agent.spatial_validation import preflight_spatial_plan
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import task_output_directory
from agent.state import AgentState

load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?'
_UNIT = r'(?:kilomet(?:er|re)s?|km|met(?:er|re)s?|m|公里|千米|公尺|米|degrees?|度|°)'
_LINEAR_UNIT_END = r'(?![A-Za-z0-9_²³/^*×]|平方|立方)(?!\s*(?:square(?:d)?\b|cube(?:d)?\b|[*^/×]))'
_DISTANCE_VALUE = rf'(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT}){_LINEAR_UNIT_END}'
_DISTANCE_CUE = r'(?:buffer(?:\s+(?:distance|radius))?|缓冲(?:区)?(?:距离|半径)?)'
_DISTANCE_EXPRESSIONS = [
    re.compile(rf'{_DISTANCE_CUE}\s*(?:of|by|为|是|为?半径|距离|:|：|=)?\s*{_DISTANCE_VALUE}', re.I),
    re.compile(rf"buffer\s+(?:(?!(?:and|then|or|display|show|view|reproject|scale)\b)[A-Za-z][A-Za-z'_-]*\s+){{1,6}}(?:by|with\s+(?:(?:a|the)\s+)?(?:distance|radius)(?:\s+of)?|at\s+a\s+distance\s+of)\s*{_DISTANCE_VALUE}", re.I),
    re.compile(rf'(?<![A-Za-z0-9_./\\-]){_DISTANCE_VALUE}\s*(?:的\s*)?(?:buffer|缓冲)', re.I),
    re.compile(rf'{_DISTANCE_CUE}\s*(?:为|是|:|：|=)?\s*(?P<number>{_NUMBER})\s*[,，;；]?\s*(?:unit|单位)\s*(?:为|是|:|：|=)?\s*(?P<unit>{_UNIT}){_LINEAR_UNIT_END}', re.I),
]
_DISTANCE_REVISION = re.compile(
    rf'(?:改成|改为|调整为|设为|change(?:\s+it)?\s+to|set(?:\s+it)?\s+to)\s*{_DISTANCE_VALUE}', re.I,
)
_FILE_REFERENCE = re.compile(
    # A Chinese save/output instruction may touch its filename without a space.
    # Keep that instruction's preceding buffer distance outside the file token.
    r'''(?:(?!(?:保存为|另存为|输出到|输出为|保存到))[^\s,，;；。!?！？"'<>])+\.(?:geojson|json|gpkg|shp|sqlite3?|db)(?:\|[^\s,，;；。!?！？"'<>]+)?''', re.I,
)
_DISPLAY_NAME = re.compile(
    r'''(?:图层名(?:称)?为|名称为|命名为|叫(?:做|作)?|\b(?:named|called)\s+)\s*'''
    r'''(?:"[^"\n]*"|'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|'''
    r'''(?!\s*["'“‘])(?:(?!(?:再|然后|接着|随后|后再|\bthen\b|\band\b))[^,，;；。.!?！？\n])+)''', re.I,
)
_DISTANCE_CHOICES = re.compile(
    rf'[\s,，(（\[]*(?:(?:or\b|to\b|或(?:者)?|至|到|[-–—~～±])\s*{_NUMBER}'
    rf'|and\s+{_NUMBER}\s*{_UNIT}{_LINEAR_UNIT_END})', re.I,
)


def _reviewed_buffer_distances(plan, request: str, advice: str | None) -> None:
    """Require an explicit physical distance, never infer it from filenames or CRS text."""
    buffers = [call for call in plan.tool_calls if call.tool_name == 'execute_processing'
               and call.arguments.get('algorithm') == 'native:buffer']
    if not buffers:
        return
    request = _FILE_REFERENCE.sub(' ', request or '')
    advice = _FILE_REFERENCE.sub(' ', advice) if advice else None
    matches = [match for expression in _DISTANCE_EXPRESSIONS
               for match in expression.finditer(request or '')]
    if advice:
        revised = [match for expression in _DISTANCE_EXPRESSIONS for match in expression.finditer(advice)]
        revised.extend(_DISTANCE_REVISION.finditer(advice))
        if revised:
            matches = revised
    if not matches:
        raise ValueError('请提供明确的缓冲距离及单位，并连同完整任务需求重新提交（例如缓冲 100 米）。')
    # Patterns may overlap, but each explicit distance belongs to one buffer in
    # request order. A number mentioned elsewhere cannot authorize another step.
    matches = sorted({match.span('number'): match for match in matches}.values(),
                     key=lambda match: match.start('number'))
    distances = []
    named_distances = []
    for match in matches:
        if _DISTANCE_CHOICES.match(match.string, match.end('unit')):
            raise ValueError('缓冲距离包含多个候选或范围，请明确一个距离及单位后重新提交完整需求。')
        unit = match.group('unit').lower()
        if unit in {'度', '°', 'degree', 'degrees'}:
            raise ValueError('缓冲距离不能将度当作米；请提供米或公里并说明完整任务需求。')
        value = float(match.group('number'))
        if unit in {'km', 'kilometer', 'kilometers', 'kilometre', 'kilometres', '公里', '千米'}:
            value *= 1000
        if not math.isfinite(value):
            raise ValueError('缓冲距离必须是有限数值，请说明完整任务需求。')
        # A display name may repeat an operation's distance, but a second
        # operation with the same value still requires a second planned buffer.
        in_name = any(name.start() <= match.start('number') < name.end()
                      for name in _DISPLAY_NAME.finditer(match.string))
        (named_distances if in_name else distances).append(value)
    if any(value not in distances for value in named_distances):
        raise ValueError('出现未明确对应的缓冲距离，请核对显示名称和操作距离并重新提交完整需求。')
    if len(distances) != len(buffers):
        raise ValueError('请按执行顺序明确每个缓冲步骤的距离及单位，不能可靠对应时请重新提交完整需求。')
    for call, requested_distance in zip(buffers, distances):
        parameters = call.arguments.get('parameters')
        distance = parameters.get('DISTANCE') if isinstance(parameters, dict) else None
        if type(distance) not in (int, float):
            # The QGIS definition reports missing and incorrectly typed parameters.
            continue
        if (not math.isfinite(distance)
                or not math.isclose(distance, requested_distance, rel_tol=1e-12, abs_tol=1e-9)):
            raise ValueError('计划中的缓冲距离与用户明确给出的距离不一致，请核对并重新提交完整需求。')

def _get_llm(state: AgentState):
    return create_llm(state.get("llm_config")).bind(response_format={"type": "json_object"})

# 定义System Prompt模板
SYSTEM_PROMPT_TEMPLATE = Template("""你是一个专业的QGIS地理数据处理专家。你的任务是将用户的自然语言需求转化为可执行的QGIS操作步骤。

## 权威 TaskPlan JSON Schema
字段、类型与允许的附加属性以以下实际 schema 为准；不得添加未定义字段。
{{ task_plan_schema }}

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
8. 保存新工程使用 save_project，参数仅有 {"path": "新工程.qgz"}；对应步骤和调用都必须为 high 且 requires_review 为 true，并声明相同的产物引用。禁止删除、覆盖及 execute_code。
9. TaskPlan.tool_calls 是实际执行的唯一调用序列；每个 step 必须恰有一个 tool_call，step_id 必须匹配，调用参数需完整并引用 selected file_id 提供的路径
11. 有文件产物时 expected_outputs.path 只填写新的文件名（例如 buffer.gpkg），output_id 只使用英文字母、数字、下划线或连字符；output_refs 指向该产物 ID，调用的输出参数使用完全相同文件名。服务器会在展示审核前绑定为本任务/修订的受控完整路径。禁止绝对输出路径、已有文件和临时输出。步骤与调用的 risk_level 和 requires_review 必须一致，调用顺序必须与步骤一致。
10. 只允许 ping、get_qgis_info、get_project_info、get_layers、add_layer、get_layer_features、execute_processing、capture_map_canvas、save_project。禁止 execute_code、remove_layer、覆盖输入和任意 Processing 算法；可用算法只有 native:reprojectlayer、native:buffer、native:clip、native:dissolve、qgis:statisticsbycategories、native:basicstatisticsforfields
12. Processing 参数、算法可用性以本机只读能力事实为准；能力不可用时不得猜测。缺少 CRS、目标 CRS、缓冲距离或单位，及无法确定米与度的转换时应要求用户补充。步骤依赖使用前一步的 output_id，并在实际调用参数中引用该产物的同一文件名。
13. 每个 native:buffer 的 tool_call 顶层必须显式填写 "distance_unit": "m"，QGIS DISTANCE 的数值对应米。用户未说明距离单位时不得猜测；公里可明确换算成米并供审核。输入若不是米制投影，应先明确重投影到适合距离计算的 CRS。distance_unit 是审核信息，不放入 arguments。
""")

# 定义User Message模板
USER_MESSAGE_TEMPLATE = Template("""用户需求: {{ input_query }}
\n本机 QGIS Processing 只读能力事实（仅下列本机可用算法可用于计划）：
{{ capabilities }}
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


async def planner_node(state: AgentState, *, processing_inspector=None, spatial_inspector=None) -> Dict[str, Any]:
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
    revision = state.get("planning_revision") or (prior_revision + 1 if previous_plan else 1)
    llm = _get_llm(state)

    capabilities = await processing_capabilities(processing_inspector)
    user_message = USER_MESSAGE_TEMPLATE.render(
        input_query=input_query,
        log_summary=log_summary,
        advise=advise,
        uploaded_files=uploaded_files,
        capabilities=capabilities,
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT_TEMPLATE.render(
            task_plan_schema=json.dumps(TaskPlan.model_json_schema(), ensure_ascii=False),
        )),
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
                "planning_revision": revision,
                "retry_count": retry_count + 1,
            }


        _reviewed_buffer_distances(task_plan, input_query, advise)
        await validate_processing_calls(task_plan, inspector=processing_inspector)
        await preflight_spatial_plan(task_plan, inspector=spatial_inspector)

        return {
            "task_plan": task_plan,
            "plan_validation": validation_report,
            "planning_error": None,
            "planning_revision": revision,
            "retry_count": retry_count + 1,
            "advise": None,
        }
    except Exception as exc:
        logger.exception("TaskPlan generation failed")
        return {
            "task_plan": None,
            "plan_validation": None,
            "planning_error": str(exc),
            "planning_revision": revision,
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
