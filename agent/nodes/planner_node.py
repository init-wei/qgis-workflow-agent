#!/usr/bin/env python3
"""
Planner Node - 规划器节点
负责根据需求、能力事实和可选参考示例生成任务计划
"""

from typing import Dict, Any
import json
import logging
import math
import os
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
from agent.tools.skill_registry import load_skill_registry, render_template
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import task_output_directory
from qgis_mcp_server.qgis_mcp_plugin.processing_policy import SUPPORTED_PROCESSING_ALGORITHMS
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
    rf'(?:改成|改为|改到|换成|换为|调整为|调整到|设为|change(?:\s+it)?\s+to|set(?:\s+it)?\s+to)\s*{_DISTANCE_VALUE}', re.I,
)
_FILE_REFERENCE = re.compile(
    # A Chinese save/output instruction may touch its filename without a space.
    # Keep that instruction's preceding buffer distance outside the file token.
    r'''(?:(?!(?:保存为|另存为|输出到|输出为|保存到))[^\s,，;；。!?！？"'<>])+\.(?:geojson|json|gpkg|shp|sqlite3?|db)(?:\|[^\s,，;；。!?！？"'<>]+)?''', re.I,
)
_DISPLAY_NAME = re.compile(
    r'''(?:(?:显示名(?:称)?|图层名(?:称)?|名称|\b(?:layer\s+name|display\s+name|name))'''
    r'''\s*(?:改为|改成|改到|换成|换为|调整为|调整到|设为|为|是|changed?\s+to|set\s+to|is|to|:|：|=)'''
    r'''|命名为|叫(?:做|作)?|\b(?:named|called)\s+)\s*'''
    r'''(?:"[^"\n]*"|'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|'''
    r'''(?!\s*["'“‘])(?:(?!(?:再|然后|接着|随后|后再|\bthen\b|\band\b))[^,，;；。.!?！？\n])+)''', re.I,
)
_DISTANCE_CHOICES = re.compile(
    rf'[\s,，(（\[]*(?:(?:or\b|to\b|或(?:者)?|至|到|[-–—~～±])\s*{_NUMBER}'
    rf'|and\s+{_NUMBER}\s*{_UNIT}{_LINEAR_UNIT_END})', re.I,
)


def _reviewed_buffer_distances(plan, request: str, advice: str | None, previous_distances=None):
    """Require an explicit physical distance, never infer it from filenames or CRS text."""
    buffers = [call for call in plan.tool_calls if call.tool_name == 'execute_processing'
               and call.arguments.get('algorithm') == 'native:buffer']
    if not buffers:
        return
    request = _FILE_REFERENCE.sub(' ', request or '')
    advice = _FILE_REFERENCE.sub(' ', advice) if advice else None
    matches = [match for expression in _DISTANCE_EXPRESSIONS
               for match in expression.finditer(request or '')]
    revised = []
    if advice:
        revised = [match for expression in _DISTANCE_EXPRESSIONS for match in expression.finditer(advice)]
        revised.extend(_DISTANCE_REVISION.finditer(advice))
        if revised:
            matches = revised
        else:
            opinion = _DISPLAY_NAME.sub(' ', advice)
            if (re.search(_DISTANCE_VALUE, opinion, re.I)
                    or re.search(r'(?:缓冲|距离|半径|\b(?:buffer|distance|radius)\b).*\d', opinion, re.I)
                    or re.fullmatch(r'\s*(?:改成|改为|改到|换成|换为|调整为|调整到|设为|change(?:\s+it)?\s+to|set(?:\s+it)?\s+to)\s*' + _NUMBER + r'\s*[。.!]?\s*', opinion, re.I)):
                raise ValueError('请明确修改后的缓冲距离及单位，不能沿用旧距离掩盖不完整的修改意见。')
    if not matches and previous_distances is None:
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
    if previous_distances is not None and not revised:
        distances = list(previous_distances)
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
    return distances

# Deliberately recognize only the necessary choices of the supported workflows.
# Unknown phrasing asks for clarification rather than guessing a business value.
_FIELD = r'''(?:`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|“[^”\n]+”|‘[^’\n]+’|[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+?)'''
_FIELDS = rf'{_FIELD}(?:\s*(?:、|,|\band\b)\s*{_FIELD})*'
_FIELD_END = r'(?=\s*(?:字段|分组|分类|融合|统计|的|[,，;；。.!?！？]|$|\b(?:fields?|statistics|stats|dissolve|then|and|count)\b))'
_GROUP_FIELDS = re.compile(
    rf'(?:按\s*(?:字段\s*)?|\b(?:group(?:ed)?\s+by|by|using)\s+(?:fields?\s+)?)'
    rf'(?P<fields>{_FIELDS}){_FIELD_END}', re.I,
)
_ALL_DISSOLVE = re.compile(
    r'(?:全部|所有)(?:要素)?\s*融合|融合\s*(?:全部|所有)(?:要素)?|'
    r'\bdissolve\s+all\b|\bdissolve\b[^，;；。.!?！？]*\ball\s+features\b', re.I,
)
_TARGET_CRS = re.compile(
    r'(?:重投影|reproject)[^，,;；。.!?！？\n]*?(?:到|为|\bto\b|\binto\b)\s*'
    r'(?:(?:目标\s*)?(?:CRS|坐标系)\s*[:：=]?\s*)?(EPSG\s*:\s*\d+)(?![\w:]|\.\d|\s*[/|±~])'
    r'|(?:目标\s*(?:CRS|坐标系)?|target\s+(?:CRS)?)\s*(?:改为|改成|改到|换成|换为|调整到|设为|为|是|:|：|=|to)?\s*'
    r'(EPSG\s*:\s*\d+)(?![\w:]|\.\d|\s*[/|±~])', re.I,
)
_STAT_VALUE = [
    re.compile(rf'对\s*(?P<fields>{_FIELDS})\s*字段\s*(?:计算|统计)', re.I),
    re.compile(rf'(?:统计|\b(?:statistics|stats)\s+(?:for|of|on)|\b(?:sum|mean|average|minimum|maximum)\s+of)'
               rf'\s*(?:同一个\s*|同一\s*)?(?:字段\s*|(?:the\s+)?field\s+)?(?P<fields>{_FIELDS}){_FIELD_END}', re.I),
    re.compile(rf'(?P<fields>{_FIELDS})\s*(?:字段)?\s*(?:的)?(?:基础)?(?:统计|\bstatistics\b|\bstats\b)', re.I),
]
_FIELD_REVISION = re.compile(
    rf'(?:将|把)?(?P<role>融合字段|分组字段|统计字段|数值字段|值字段|'
    rf'dissolve\s+fields?|group(?:ing)?\s+fields?|(?:statistic|value)\s+fields?)'
    rf'\s*(?:改为|改成|改到|换成|换为|调整为|调整到|设为|为|:|：|=|to)\s*(?P<fields>{_FIELDS}){_FIELD_END}', re.I,
)


_PARAMETER_CLAUSE = r'[，;；。!?！？\n]|(?<!\d)\.|\.(?!\d)|,\s*(?:and\s+)?(?=clip\b|dissolve\b|calculate\b|statistics\b|reproject\b)|\bthen\b|然后|再(?=对)|接着|随后|再(?=将|把|全部|融合|缓冲|统计|重投影)|再(?=按)|并(?=统计|计数|计算)'
_NEGATIVE_OPERATION = re.compile(
    r'(?:不|不要|不能|别|勿|禁止|无需|\b(?:no|not|never|without|don[’\']t)\b)[^,，;；。.!?！？]{0,40}'
    r'(?:重投影|融合|统计|缓冲|字段|分组|目标|距离|半径|按|'
    r'\b(?:reproject|dissolve|statistics|stats|buffer|fields?|grouping|target|distance|radius|by)\b)', re.I,
)

def _parameter_text(text: str) -> str:
    """Do not treat literal filenames and display names as instructions."""
    return _DISPLAY_NAME.sub(' ', _FILE_REFERENCE.sub(' ', text or ''))


def _field_names(text: str) -> list[str]:
    parts = re.findall(r'''`[^`]+`|"[^"]+"|'[^']+'|“[^”]+”|‘[^’]+’|[^,、]+''', text)
    names = []
    for part in parts:
        part = part.strip()
        names.extend([part[1:-1]] if part.startswith(('`', '"', "'", '“', '‘'))
                     else re.split(r'\s+and\s+', part, flags=re.I))
    return names


_REFERENCE_CLAUSE = re.compile(
    r'^\s*(?:(?:最后|最终|然后)?只?(?:加载|显示|查看).*(?:结果|产物|图层)|'
    r'(?:不|不要|无需|别)(?:加载|显示|查看)(?:(?:源|原始|输入)?图层|(?:任何)?统计表|计数)'
    r'(?:或(?:(?:源|原始|输入)?图层|(?:任何)?统计表|计数))*(?:到(?:画布|地图))?\s*$|'
    r'(?:总数|要素总数|总要素数)由(?:经过|经)?(?:验证|核验)的?(?:输入|来源|源)?统计事实提供\s*$|'
    r'(?![^,，;；。!?！？]*字段)(?:统计|融合|缓冲)(?:表|结果|产物)(?:的)?(?:输出|保存|写入|为|到)|'
    r'只(?:计划|执行|做)(?:(?:这|上述|以上|前述)(?:[一两二三四\d]+)?(?:步|项|个)?|'
    r'[^字段:：]{0,80}(?:三个步骤|三步|两个步骤|两步|步骤清单))|'
    r'(?:finally\s+)?(?:only\s+)?(?:load|display|view).*(?:result|output)|'
    r'(?:only|just)\s+(?:plan|execute|perform)\s+(?:(?:these|the above)\b|\d+\s+(?:steps|operations)))', re.I,
)

_FEATURE_COUNT = re.compile(
    r'(?:统计|计算|获取|查看|求)\s*(?:要素(?:数量|总数|个数)|总(?:要素)?数)|'
    r'\bcount\s+(?:(?:the|all|selected)\s+)*(?:features?|(?:selected\s+)?layer)\b|'
    r'\b(?:feature|layer)\s+count\b', re.I,
)
_QUOTED_FIELD = re.compile(r'''`[^`\n]+`|"[^"\n]+"|'[^'\n]+'|“[^”\n]+”|‘[^’\n]+’''')
_BUFFER_SEGMENTS = re.compile(
    r'每(?:个)?象限\s*(\d+)\s*段|\bSEGMENTS\s*[:：=]\s*(\d+)(?![\w.]|\s*[/|])|'
    r'(?<![\w.])(\d+)\s+segments?\s+per\s+quadrant\b', re.I,
)
_PROCESSING_ONLY = re.compile(r'只(?:保留|执行|计划)\s*([一两二三四1-4])\s*(?:项|个|步)?\s*Processing\b', re.I)


def _operation_text(text: str) -> str:
    """Mask only operands proven by field syntax, retaining surrounding commands."""
    spans = []
    for expression in [*_STAT_VALUE, _GROUP_FIELDS, _FIELD_REVISION]:
        for match in expression.finditer(text):
            start, end = match.span('fields')
            operand = text[start:end]
            for quoted in _QUOTED_FIELD.finditer(operand):
                spans.append((start + quoted.start(), start + quoted.end()))
            prefix = text[match.start():start]
            suffix = text[end:match.end()] + text[match.end():match.end() + 8]
            if re.search(r'字段|\bfields?\b', prefix, re.I) or re.match(r'\s*(?:字段|fields?\b)', suffix, re.I):
                spans.append((start, end))
    for start, end in sorted(set(spans), reverse=True):
        # Equal-length blanks keep overlapping recognized operand spans stable.
        text = text[:start] + ' ' * (end - start) + text[end:]
    return text


def _parameter_clauses(text: str):
    """Retain explicit sequencing, which distinguishes an option from a new call."""
    start, separate = 0, False
    for match in re.finditer(_PARAMETER_CLAUSE, text, re.I):
        yield text[start:match.start()], separate
        separate = bool(re.search(r'\bthen\b|然后|再|接着|随后|并', match.group(), re.I))
        start = match.end()
    yield text[start:], separate


def _previous_parameters(context, task_id: str, revision: int) -> dict:
    if (isinstance(context, dict) and context.get('task_id') == task_id
            and context.get('revision') in {revision, revision - 1}):
        choices = dict(context.get('choices', {}))
        # Existing checkpoints contained only one value per family. Preserve
        # their proven explicit fact while moving to the ordered representation.
        if 'dissolve' in choices:
            choices['dissolves'] = [choices.pop('dissolve')]
        if 'stat_values' in choices or 'stat_groups' in choices:
            groups = choices.pop('stat_groups', None)
            choices['statistics'] = [{'kind': 'category' if groups else 'basic',
                                      'values': choices.pop('stat_values', None),
                                      'groups': groups[0] if groups and len(groups) == 1 else None}]
        return choices
    return {}


def _parameter_requirements(request: str, advice: str | None, context, task_id: str, revision: int) -> dict:
    """Keep explicit choices in request order, never infer unknown edits as no edit."""
    carry = _previous_parameters(context, task_id, revision)
    choices = {}
    for index, raw in enumerate([request, advice or '']):
        if index == 1:
            choices.update(carry)
        text = _parameter_text(raw)
        instruction_text = ' '.join(_operation_text(part) for part, _ in _parameter_clauses(text)
                                    if not _REFERENCE_CLAUSE.search(part))
        # A step summary can still carry an explicit call-count restriction.
        processing_text = ' '.join(_operation_text(part) for part, _ in _parameter_clauses(text))
        processing_only = list(_PROCESSING_ONLY.finditer(processing_text))
        if processing_only:
            counts = [{'一': 1, '两': 2, '二': 2, '三': 3, '四': 4}.get(match[1], int(match[1]) if match[1].isdigit() else None)
                      for match in processing_only]
            if (len(set(counts)) != 1 or any(re.search(r'(?:取消|不再|不要|不是|无需|不)\s*$', processing_text[:match.start()])
                                             for match in processing_only)
                    or (choices.get('processing_only_count') is not None and choices['processing_only_count'] != counts[0])
                    or re.search(r'(?:同时|另外|还)(?:增加|保留|执行)\s*(?:add_layer|capture_map_canvas|ping)\b', processing_text, re.I)):
                raise ValueError('Processing-only 调用约束有冲突或修改不明确，请重述完整调用要求。')
            choices['processing_only_count'] = counts[0]
        elif (re.search(r'只(?:保留|执行|计划).{0,12}Processing', processing_text, re.I)
              or (index == 1 and choices.get('processing_only_count') is not None and re.search(
                  r'(?:取消|不再|改为|改成|允许|增加).{0,20}(?:Processing|add_layer|capture_map_canvas)', processing_text, re.I))):
            raise ValueError('请明确 Processing-only 调用要求及数量；无法可靠解释的修改不能沿用旧约束。')
        target_matches = list(_TARGET_CRS.finditer(instruction_text))
        operations = [re.sub(r'\s+', '', match.group(1)).upper()
                      for match in target_matches if match.group(1)]
        declarations = [re.sub(r'\s+', '', match.group(2)).upper()
                        for match in target_matches if match.group(2)]
        if operations and all(target in operations for target in declarations):
            targets = operations
        else:
            targets = [re.sub(r'\s+', '', match.group(1) or match.group(2)).upper()
                       for match in target_matches]
        if targets:
            if index == 1 and len(choices.get('targets', [])) > 1 and len(targets) != len(choices['targets']):
                raise ValueError('请按顺序明确每个重投影步骤的新目标 CRS。')
            choices['targets'] = (list(dict.fromkeys(targets)) if not operations and len(set(targets)) == 1
                                  else targets)
        elif index == 1 and re.search(r'目标|坐标系|\b(?:target|CRS|EPSG)\b', instruction_text, re.I):
            raise ValueError('请明确修改后的目标 CRS，例如目标 CRS 改为 EPSG:32651。')
        revisions = list(_FIELD_REVISION.finditer(text))
        if index == 1 and not revisions and re.search(r'(?:字段|\bfields?\b).*(?:改|换|change|set|switch)', text, re.I):
            raise ValueError('请明确修改后的融合、分组或统计字段原名。')
        # A recognized edit consumes its full finite expression. An edit cue
        # left beside a necessary parameter is not evidence of "no change".
        remainder = _TARGET_CRS.sub(' ', _FIELD_REVISION.sub(' ', text))
        remainder = _DISTANCE_REVISION.sub(' ', remainder)
        if index == 1 and re.search(r'改|换|调整|\b(?:change|switch|replace|set)\b', remainder, re.I) and re.search(
            r'融合|统计|字段|\b(?:dissolve|statistics|stats|fields?|EPSG)\b', remainder, re.I,
        ):
            raise ValueError('无法可靠对应必要参数的修改，请明确目标 CRS 或按步骤重述融合、统计字段。')
        active_buffer = bool(index == 1 and choices.get('buffer_distances'))
        statistics, dissolves, buffer_modes, buffer_segments = [], [], [], []
        for raw_clause, separate_step in _parameter_clauses(text):
            if _REFERENCE_CLAUSE.search(raw_clause):
                continue
            if separate_step:
                active_buffer = False
            # Check the original clause before consuming positive edit syntax;
            # "do not change the field" must never become a positive assignment.
            clause = _FIELD_REVISION.sub(' ', raw_clause)
            groups = list(_GROUP_FIELDS.finditer(clause))
            operation_clause = _operation_text(clause)
            distances = {match.span('number') for expression in _DISTANCE_EXPRESSIONS
                         for match in expression.finditer(operation_clause)}
            if distances:
                buffer_modes.extend([None] * len(distances))
                buffer_segments.extend([None] * len(distances))
                active_buffer = True
            segment_matches = list(_BUFFER_SEGMENTS.finditer(operation_clause))
            segment_cue = re.search(r'每(?:个)?象限|\bSEGMENTS\b', operation_clause, re.I)
            if segment_matches:
                values = [int(next(value for value in match.groups() if value is not None))
                          for match in segment_matches]
                if (not active_buffer or len(distances) > 1 or len(set(values)) != 1
                        or any(re.match(r'\s*(?:或|or\b|to\b|[-–~,，])\s*\d+', operation_clause[match.end():], re.I)
                               for match in segment_matches)):
                    raise ValueError('请按执行顺序明确每个缓冲步骤的每象限段数 SEGMENTS。')
                if not buffer_segments:
                    buffer_segments.append(values[0])
                elif buffer_segments[-1] is None:
                    buffer_segments[-1] = values[0]
                elif buffer_segments[-1] != values[0]:
                    raise ValueError('缓冲每象限段数 SEGMENTS 有冲突，请明确一个整数。')
            elif segment_cue:
                raise ValueError('请用明确整数给出缓冲每象限段数，例如每象限12段或 SEGMENTS=12。')
            negative = bool(_NEGATIVE_OPERATION.search(_operation_text(raw_clause)))
            buffer_option = active_buffer and not groups and bool(re.search(r'融合|\bdissolv', operation_clause, re.I))
            if negative and not (buffer_option and not re.search(
                r'(?:不|不要|别|勿|禁止|无需|\b(?:no|not|never|without|don[’\']t)\b)[^,，;；。.!?！？]{0,40}'
                r'(?:重投影|统计|缓冲|\b(?:reproject|statistics|stats|buffer)\b)', operation_clause, re.I,
            )):
                raise ValueError('必要参数包含否定要求，请明确要执行的目标 CRS、融合或统计步骤。')
            ambiguous = (re.search(r'EPSG\s*:\s*\d+\s*(?:或|\bor\b)', operation_clause, re.I)
                         or (re.search(r'融合|统计|\b(?:dissolve|statistics|stats)\b', operation_clause, re.I)
                             and re.search(r'或(?:者)?|\bor\b', operation_clause, re.I)))
            if ambiguous:
                raise ValueError('必要参数包含多个候选，请明确目标 CRS、融合模式或统计字段后重新提交。')
            if buffer_option:
                mode = not negative
                if not buffer_modes:
                    buffer_modes.append(mode)
                elif buffer_modes[-1] is None:
                    buffer_modes[-1] = mode
                elif buffer_modes[-1] != mode:
                    raise ValueError('缓冲融合选项冲突，请明确每个缓冲步骤是否融合。')
            elif re.search(r'融合|\bdissolve\b', operation_clause, re.I):
                dissolves.append([] if _ALL_DISSOLVE.search(operation_clause)
                                 else _field_names(groups[0].group('fields')) if len(groups) == 1 else None)
            count_only = not groups and bool(_FEATURE_COUNT.search(operation_clause))
            if count_only:
                choices['feature_count'] = True
                source = 'statistics' if statistics else 'input'
                if choices.get('feature_count_source', source) != source:
                    raise ValueError('总数涉及多个输入阶段，请明确分别统计哪个来源。')
                choices['feature_count_source'] = source
            stat_matches = [match for expression in _STAT_VALUE for match in expression.finditer(clause)]
            if not count_only and (stat_matches or re.search(r'统计|\bstatistics\b|\bstats\b|\b(?:sum|mean|average|minimum|maximum)\s+of', operation_clause, re.I)):
                values_text = re.sub(r'分类|基础|计算面积并|计算面积后', '', _GROUP_FIELDS.sub(' ', clause))
                matches = [match for expression in _STAT_VALUE for match in expression.finditer(values_text)]
                fields = set()
                for match in matches:
                    literal = match.group('fields')
                    if re.search(r'(?:native|qgis|gdal):$', values_text[:match.start('fields')], re.I):
                        # Qualified algorithm labels in explicit review text
                        # are not another requested statistics field.
                        continue
                    prefix = values_text[match.start():match.start('fields')]
                    suffix = values_text[match.end('fields'):match.end('fields') + 8]
                    marked = bool(re.search(r'字段|\bfields?\b', prefix, re.I)
                                  or re.match(r'\s*(?:字段|fields?\b)', suffix, re.I)
                                  or _QUOTED_FIELD.search(literal))
                    if (literal.lower() in {'calculate', 'basic', 'category'}
                            or (re.search(r'[\u4e00-\u9fff]', literal) and not marked and literal != '面积')):
                        continue
                    fields.add(tuple(_field_names(literal)))
                group_fields = _field_names(groups[0].group('fields')) if len(groups) == 1 else None
                statistics.append({'kind': 'category' if groups or '分类' in clause else 'basic',
                                   'values': list(next(iter(fields))) if len(fields) == 1 else None,
                                   'groups': group_fields})
            if statistics and re.search(r'仅(?:计数|统计数量)|只(?:计数|统计数量)|\bcount\s+only\b|\bonly\s+count\b', clause, re.I):
                statistics[-1]['values'] = []
        if dissolves:
            if index == 1 and len(choices.get('dissolves', [])) > 1 and len(dissolves) != len(choices['dissolves']):
                raise ValueError('请按顺序重述每个融合步骤，不能静默丢弃未提及的步骤。')
            choices['dissolves'] = dissolves
        if statistics:
            if index == 1 and len(choices.get('statistics', [])) > 1 and len(statistics) != len(choices['statistics']):
                raise ValueError('请按顺序重述每个统计步骤，不能静默丢弃未提及的步骤。')
            if index == 1 and any(item['values'] is None and item['groups'] is None for item in statistics):
                raise ValueError('请明确修改后的统计字段或分组字段，不能静默沿用旧参数。')
            if index == 1 and len(statistics) == 1 and len(choices.get('statistics', [])) == 1:
                earlier = choices['statistics'][0]
                current = statistics[0]
                current = {key: current[key] if current[key] is not None else earlier.get(key)
                           for key in ('kind', 'values', 'groups')}
                if current['groups'] is not None:
                    current['kind'] = 'category'
                statistics = [current]
            choices['statistics'] = statistics
        if any(mode is not None for mode in buffer_modes):
            choices['buffer_dissolve'] = buffer_modes
        if any(segments is not None for segments in buffer_segments):
            choices['buffer_segments'] = buffer_segments
        for match in revisions:
            role, names = match.group('role').lower(), _field_names(match.group('fields'))
            if role.startswith(('融合', 'dissolve')):
                if len(choices.get('dissolves', [])) != 1:
                    raise ValueError('请按顺序重述每个融合步骤及字段，不能把修改意见任意分配给多个步骤。')
                choices['dissolves'] = [names]
            else:
                if len(choices.get('statistics', [])) != 1:
                    raise ValueError('请按顺序重述每个统计步骤及字段，不能把修改意见任意分配给多个步骤。')
                item = dict(choices['statistics'][0])
                item['groups' if role.startswith(('分组', 'group')) else 'values'] = names
                if item['groups'] is not None:
                    item['kind'] = 'category'
                choices['statistics'] = [item]
    return {'task_id': task_id, 'revision': revision, 'choices': choices}


def _reviewed_parameters(plan, requirements: dict) -> None:
    """Match necessary choices by call order and count, including repeated families."""
    choices = requirements['choices']
    processing_only_count = choices.get('processing_only_count')
    if processing_only_count is not None and (
        len(plan.tool_calls) != processing_only_count
        or any(call.tool_name != 'execute_processing' for call in plan.tool_calls)
    ):
        raise ValueError('只保留指定数量的 Processing 调用；计划不能夹带加载、截图或其它调用。')
    calls = [call for call in plan.tool_calls if call.tool_name == 'execute_processing']
    families = {
        'targets': [call for call in calls if call.arguments.get('algorithm') == 'native:reprojectlayer'],
        'dissolves': [call for call in calls if call.arguments.get('algorithm') == 'native:dissolve'],
        'statistics': [call for call in calls if call.arguments.get('algorithm') in {
            'native:basicstatisticsforfields', 'qgis:statisticsbycategories'}],
    }
    for family, family_calls in families.items():
        if not family_calls and not choices.get(family):
            continue
        expected = choices.get(family, [])
        if len(family_calls) != len(expected):
            raise ValueError('请按执行顺序明确每个重投影目标 CRS、融合字段或统计字段；计划步骤数量必须与需求一致。')
        for call, desired in zip(family_calls, expected):
            params = call.arguments.get('parameters', {})
            if family == 'targets':
                valid = str(params.get('TARGET_CRS', '')).upper() == desired
            elif family == 'dissolves':
                valid = desired is not None and params.get('FIELD') == desired
            else:
                values = desired['values']
                if values == ['面积'] and any(item.arguments.get('algorithm') == 'qgis:exportaddgeometrycolumns' for item in calls):
                    values = ['area']
                if call.arguments['algorithm'] == 'native:basicstatisticsforfields':
                    valid = desired['kind'] == 'basic' and values == [params.get('FIELD_NAME')]
                else:
                    valid = (desired['kind'] == 'category' and desired['groups'] == params.get('CATEGORIES_FIELD_NAME')
                             and values == ([params['VALUES_FIELD_NAME']] if params.get('VALUES_FIELD_NAME') else []))
            if not valid:
                raise ValueError('计划目标 CRS、融合 FIELD 或统计字段与用户明确给出的有序参数不一致，请补充或修正。')
    if choices.get('feature_count'):
        steps = {step.step_id: step for step in plan.steps}
        statistic_sources = {tuple(steps[call.step_id].input_refs) for call in families['statistics']}
        if choices.get('feature_count_source') == 'statistics':
            if len(statistic_sources) != 1 or len(next(iter(statistic_sources))) != 1:
                raise ValueError('统计涉及多个来源，请明确哪个统计输入的 feature_count 总数。')
            source_refs = next(iter(statistic_sources))
        else:
            if len(plan.required_inputs) != 1:
                raise ValueError('独立计数需要唯一明确输入，请说明要核对哪个输入的 feature_count。')
            source_refs = (plan.required_inputs[0].input_id,)
        # Both paths retain the existing independent source readback. A final
        # loaded product cannot stand in for the original input's count.
        if source_refs not in statistic_sources and not any(
            call.tool_name == 'add_layer' and call.arguments.get('layer_type') == 'vector'
            and tuple(steps[call.step_id].input_refs) == source_refs for call in plan.tool_calls
        ):
            raise ValueError('要素总数需由同一来源的加载或明确字段统计核对 feature_count，不能用其它图层代替。')
    buffers = [call for call in calls if call.arguments.get('algorithm') == 'native:buffer']
    modes = choices.get('buffer_dissolve')
    if modes is not None and (len(modes) != len(buffers) or any(
        desired is not None and call.arguments.get('parameters', {}).get('DISSOLVE') is not desired
        for call, desired in zip(buffers, modes)
    )):
        raise ValueError('计划缓冲 DISSOLVE 与明确要求的融合/不融合选项不一致，请修正。')
    segments = choices.get('buffer_segments')
    if segments is not None and (len(segments) != len(buffers) or any(
        desired is not None and (type(call.arguments.get('parameters', {}).get('SEGMENTS')) is not int
                                 or call.arguments['parameters']['SEGMENTS'] != desired)
        for call, desired in zip(buffers, segments)
    )):
        raise ValueError('计划缓冲 SEGMENTS 与明确要求的每象限段数不一致，请修正。')

def _check_plan_placeholders(plan, uploaded_files) -> None:
    proposal = plan.model_dump(mode='json')
    selected = {str(item['file_id']): item for item in uploaded_files}
    proven_inputs = {}
    def literal_description(value, records):
        if isinstance(value, str):
            literals = {record[key] for record in records for key in ('filename', 'layer_name')}
            for literal in sorted(literals, key=len, reverse=True):
                if literal:
                    value = value.replace(literal, '<registered input>')
        return value

    for item in proposal['required_inputs']:
        record = selected.get(str(item.get('file_id')))
        if record:
            proven_inputs[item['input_id']] = record
            item['description'] = literal_description(item.get('description'), [record])
            for key, registered in (('source', 'filename'), ('layer_name', 'layer_name'), ('path', 'qgis_path')):
                if item.get(key) == record[registered]:
                    item[key] = None
    steps = {step['step_id']: step for step in proposal['steps']}
    proposal['task_summary'] = literal_description(proposal['task_summary'], proven_inputs.values())
    for item in proposal['expected_outputs']:
        item['description'] = literal_description(item.get('description'), proven_inputs.values())
    for step in proposal['steps']:
        records = [proven_inputs[ref] for ref in step.get('input_refs', []) if ref in proven_inputs]
        step['description'] = literal_description(step['description'], records)
    for call in proposal['tool_calls']:
        refs = steps.get(call['step_id'], {}).get('input_refs', [])
        registered_paths = {proven_inputs[ref]['qgis_path'] for ref in refs if ref in proven_inputs}
        if call['tool_name'] == 'add_layer' and len(refs) == 1:
            if call['arguments'].get('path') in registered_paths:
                call['arguments']['path'] = None
            record = proven_inputs.get(refs[0])
            if record and call['arguments'].get('name') in {record['filename'], record['layer_name']}:
                call['arguments']['name'] = None
        elif call['tool_name'] == 'execute_processing':
            parameters = call['arguments'].get('parameters', {})
            if isinstance(parameters, dict):
                for key in ('INPUT', 'INPUT_LAYER', 'OVERLAY'):
                    if parameters.get(key) in registered_paths:
                        parameters[key] = None
    # Only proven, directly referenced input identities are literals. Output,
    # field, unrelated metadata and unmatched execution strings remain strict.
    render_template(proposal, [], {})


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
10. 只允许 ping、get_qgis_info、get_project_info、get_layers、add_layer、get_layer_features、execute_processing、capture_map_canvas、save_project。禁止 execute_code、remove_layer、覆盖输入和任意 Processing 算法；有限算法：{{ supported_algorithms }}
12. execute_processing 的 arguments 固定为 {"algorithm": "算法ID", "parameters": {"参数名": "参数值"}}；使用 parameters，不能写成 params，也不能将算法参数放在 arguments 顶层。Processing 参数、算法可用性以本机只读能力事实为准；能力不可用时不得猜测。缺少 CRS、目标 CRS、缓冲距离或单位，及无法确定米与度的转换时应要求用户补充。步骤依赖使用前一步的 output_id，并在实际调用参数中引用该产物的同一文件名。
13. 每个 native:buffer 的 tool_call 顶层必须显式填写 "distance_unit": "m"，QGIS DISTANCE 的数值对应米。用户未说明距离单位时不得猜测；公里可明确换算成米并供审核。输入若不是米制投影，应先明确重投影到适合距离计算的 CRS。distance_unit 是审核信息，不放入 arguments。
14. 裁剪明确 INPUT 和 OVERLAY 的输入引用；融合明确 FIELD 列表（全部融合用 []）及 SEPARATE_DISJOINT 布尔值。裁剪、融合、面积辅助与统计均使用显式新的 .gpkg OUTPUT，以保留空图层字段类型；basic 指定数值 FIELD_NAME；分类统计指定非空 CATEGORIES_FIELD_NAME 列表，若指定 VALUES_FIELD_NAME 它必须是数值字段，省略时只计类别数量。面积辅助 qgis:exportaddgeometrycolumns 仅 CALC_METHOD=0，需米制投影二维面且 area/perimeter 不冲突；先用辅助结果计算面积，原始同名字段不可推断为平方米。独立的总要素计数任务可通过显式批准的 add_layer 取得总数；数值/分类统计的总数由经验证的输入统计事实提供，无需为计数额外加载。用户明确要求查看图层时按请求加载，未要求查看时不额外加载。
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
{% if skill_examples %}

已提供的方案参考示例（不是执行授权；其示例参数不代表用户的要求或同意）：
{{ skill_examples }}
请结合当前用户需求和文件元数据重新生成唯一的 TaskPlan，使用用户明确提供的参数；
缺少必要参数时不要沿用示例值，计划仍须经过全部校验与人工批准。
{% endif %}
{% if log_summary %}

上一轮执行总结:
{{ log_summary }}
{% endif %}
{% if planning_error %}

上一轮计划未通过服务端检查，以下反馈仅用于修订计划，不能视为审核批准：
{{ planning_error }}
{% endif %}
{% if previous_parameters %}

本任务中已明确的必要参数（包括仍适用的前版意见约束；不是执行批准）:
{{ previous_parameters }}
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
    requirements = None
    try:
        requirements = _parameter_requirements(
            input_query, advise, state.get("parameter_requirements"), task_id, revision,
        )
        skills_enabled = os.getenv("QGIS_AGENT_SKILLS_ENABLED", "true").strip().lower() not in {
            "false", "0", "no", "off",
        }
        examples = (load_skill_registry().matching_examples(
            f"{input_query}\n{advise}" if advise else input_query, uploaded_files,
        ) if skills_enabled else [])
        user_message = USER_MESSAGE_TEMPLATE.render(
            input_query=input_query,
            log_summary=log_summary,
            advise=advise,
            previous_parameters=json.dumps(requirements["choices"], ensure_ascii=False),
            planning_error=state.get("planning_error") if retry_count else None,
            uploaded_files=uploaded_files,
            capabilities=capabilities,
            skill_examples=json.dumps(examples, ensure_ascii=False) if examples else None,
        )
        messages = [
            SystemMessage(content=SYSTEM_PROMPT_TEMPLATE.render(
                task_plan_schema=json.dumps(TaskPlan.model_json_schema(), ensure_ascii=False),
                supported_algorithms='、'.join(sorted(SUPPORTED_PROCESSING_ALGORITHMS)),
            )),
            HumanMessage(content=user_message),
        ]
        response = await llm.ainvoke(messages)
        task_plan = parse_task_plan_json(response.content)
        # The binders replace paths with server-owned values. Check first so a
        # missing placeholder cannot be hidden by that replacement.
        _check_plan_placeholders(task_plan, uploaded_files)
        task_plan = task_plan.model_copy(
            update={
                "task_id": task_id,
                "revision": revision,
                "review_status": "pending_review",
            }
        )
        task_plan = bind_uploaded_files(task_plan, uploaded_files)
        task_plan = bind_task_outputs(task_plan)

        # Only the server can describe which examples were actually provided.
        # These facts do not claim the model used them or authorize execution.
        metadata = {
            "iteration": retry_count + 1,
            "skill_candidates": {
                "enabled": skills_enabled,
                "provided": [
                    {key: item[key] for key in ("skill_id", "version", "family", "review_status", "provenance", "human_review")}
                    for item in examples
                ],
            },
        }
        # Preserve this forbidden legacy field until the validator reports it;
        # discarding it first would hide an invalid proposal from the retry.
        if "tool_calls" in task_plan.metadata:
            metadata["tool_calls"] = task_plan.metadata["tool_calls"]
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
                "parameter_requirements": requirements,
                "retry_count": retry_count + 1,
            }


        distances = _reviewed_buffer_distances(
            task_plan, input_query, advise, requirements['choices'].get('buffer_distances'),
        )
        if distances is not None:
            requirements['choices']['buffer_distances'] = distances
        _reviewed_parameters(task_plan, requirements)
        metadata["parameter_requirements"] = requirements
        await validate_processing_calls(task_plan, inspector=processing_inspector)
        spatial_report = await preflight_spatial_plan(task_plan, inspector=spatial_inspector)
        metadata["spatial_preflight"] = {
            "task_id": task_plan.task_id, "revision": task_plan.revision,
            "review_steps": _review_steps(task_plan, spatial_report),
        }
        task_plan = task_plan.model_copy(update={"metadata": metadata})

        return {
            "task_plan": task_plan,
            "plan_validation": validation_report,
            "planning_error": None,
            "planning_revision": revision,
            "parameter_requirements": requirements,
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
            "parameter_requirements": requirements,
            "retry_count": retry_count + 1,
        }


def _review_steps(task_plan: TaskPlan, report: dict) -> list[dict]:
    """Publish only compact inspector facts matching this plan's spatial calls."""
    calls = [call for call in task_plan.tool_calls
             if call.tool_name == 'execute_processing' or
             (call.tool_name == 'add_layer' and call.arguments.get('layer_type') == 'vector')]
    details = report['facts'].get('review_steps', [])
    if not isinstance(details, list) or len(details) != len(calls):
        raise ValueError('Spatial preflight did not describe every planned spatial call.')
    steps = {step.step_id: step for step in task_plan.steps}
    keys = ('step_id', 'tool', 'algorithm', 'input_crs', 'output_crs', 'input_kind',
            'output_kind', 'geometry', 'field', 'field_type', 'group_fields', 'unit')
    result = []
    for call, item in zip(calls, details):
        if not isinstance(item, dict) or any(key not in item for key in keys):
            raise ValueError('Spatial preflight returned an incomplete review description.')
        sources = item['input_crs']
        algorithm = call.arguments.get('algorithm')
        parameters = call.arguments.get('parameters', {})
        table = algorithm in {'native:basicstatisticsforfields', 'qgis:statisticsbycategories'}
        output_kind = 'layer' if call.tool_name == 'add_layer' else 'table' if table else 'spatial'
        field = (parameters.get('FIELD_NAME') if algorithm == 'native:basicstatisticsforfields'
                 else parameters.get('VALUES_FIELD_NAME') if algorithm == 'qgis:statisticsbycategories'
                 else 'area' if algorithm == 'qgis:exportaddgeometrycolumns' else None)
        groups = (parameters.get('FIELD') if algorithm == 'native:dissolve'
                  else parameters.get('CATEGORIES_FIELD_NAME') if algorithm == 'qgis:statisticsbycategories'
                  else [])
        required_unit = ('m2' if algorithm == 'qgis:exportaddgeometrycolumns'
                         else 'm' if algorithm == 'native:buffer' else None)
        if (type(item['step_id']) is not int or item['step_id'] != call.step_id or item['tool'] != call.tool_name
                or item['algorithm'] != algorithm
                or not isinstance(sources, list)
                or any(not isinstance(source, dict) or not isinstance(source.get('ref'), str)
                       or not isinstance(source.get('crs'), str) or not source['crs'] for source in sources)
                or [source['ref'] for source in sources] != steps[call.step_id].input_refs
                or item['input_kind'] != 'spatial' or item['output_kind'] != output_kind
                or (item['output_crs'] is not None if table else not item['output_crs'])
                or any(item[key] is not None and not isinstance(item[key], str)
                       for key in ('output_crs', 'geometry', 'field', 'field_type', 'unit'))
                or item['unit'] not in {None, 'm', 'm2'}
                or item['field'] != field or item['group_fields'] != groups
                or (required_unit is not None and item['unit'] != required_unit)
                or (algorithm == 'qgis:exportaddgeometrycolumns' and item['field_type'] != 'Real')
                or (item['field'] is not None and not item['field_type'])
                or not isinstance(item['group_fields'], list)
                or any(not isinstance(field, str) or not field for field in item['group_fields'])):
            raise ValueError('Spatial preflight review description does not match the current plan.')
        compact = {key: item[key] for key in keys}
        compact['input_crs'] = [{'ref': source['ref'], 'crs': source['crs']} for source in sources]
        compact['group_fields'] = list(item['group_fields'])
        result.append(compact)
    return result


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
