#!/usr/bin/env python3
"""Project task completion facts and release transient workflow state.

The summary is a read-only projection, never an execution or confirmation decision.
"""
import json
import logging
from typing import Any, Dict

from langchain.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent.state import AgentState
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import WRITE_TOOLS

logger = logging.getLogger(__name__)

def _serialize_graph_record(task_plan: Any) -> Any:
    """Serialize a graph-owned TaskPlan or ExecutionTrace for its fact projection."""
    if task_plan is None:
        return None
    if hasattr(task_plan, "model_dump"):
        return task_plan.model_dump()
    if hasattr(task_plan, "dict"):
        return task_plan.dict()
    return task_plan

def _execution_summary(state: AgentState) -> str:
    plan = _serialize_graph_record(state.get("task_plan")) or {}
    trace = _serialize_graph_record(state.get("execution_trace")) or {}
    confirmed = state.get("is_completed")
    confirmation = ('未知' if 'is_completed' not in state else
                    '未确认' if confirmed is None else ('成功' if confirmed else '失败'))
    lines = [f"任务：{plan.get('task_id') or state.get('task_id') or '未知'} · 修订：{plan.get('revision', '未知')}",
             f"用户确认：{confirmation}（与工具完成和空间验证分别记录）"]
    if not trace or trace.get('task_id') != plan.get('task_id'):
        return '\n'.join(lines + ['执行记录：缺失或任务不匹配；不能推断执行成功。', '工具完成：未知'])

    attempts = trace.get('attempts') or []
    completed = sum((a.get('observation') or {}).get('status') == 'success' and
                    ((a.get('observation') or {}).get('outcome') == 'completed' or
                     ((a.get('tool_call') or {}).get('tool_name') not in WRITE_TOOLS and
                      (a.get('observation') or {}).get('outcome') is None)) for a in attempts)
    unknown = any((a.get('observation') or {}).get('outcome') == 'unknown' or
                  (a.get('status') != 'blocked' and
                   (a.get('tool_call') or {}).get('tool_name') in WRITE_TOOLS and
                   (a.get('observation') or {}).get('outcome') is None) for a in attempts)
    artifacts = [artifact for attempt in attempts for artifact in attempt.get('artifacts', [])]
    passed = sum((a.get('spatial_validation') or {}).get('status') == 'passed' for a in artifacts)
    failed = sum((a.get('spatial_validation') or {}).get('status') == 'failed' for a in artifacts)
    missing = len(artifacts) - passed - failed
    if unknown:
        status = '结果未知，需核查；不自动重放'
    elif not attempts or not completed or trace.get('status') != 'success' or failed:
        status = '未完成或失败'
    else:
        status = '执行完成（不替代空间验证或用户确认）'
    lines += [f'执行状态：{status}', f'工具完成：{completed}/{len(attempts)} 个尝试/审核记录中工具返回成功',
              f'空间验证：{passed} 通过，{failed} 失败，{missing} 未提供',
              f"可交付产物：{sum(a.get('deliverable') is True for a in artifacts)}/{len(artifacts)}（按业务记录）"]
    for index, attempt in enumerate(attempts, 1):
        call = attempt.get('tool_call') or {}
        observation = attempt.get('observation') or {}
        outcome_label = ('未发送' if attempt.get('status') == 'blocked' else
                         observation.get('outcome') or
                         ('只读响应' if call.get('tool_name') not in WRITE_TOOLS and observation.get('status') == 'success' else '未知'))
        lines.append(f"尝试/审核记录 {index}：{call.get('tool_name', '未知')} · "
                     f"{attempt.get('status', '未知')} / {outcome_label} · "
                     f"参数：{json.dumps(call.get('arguments') or {}, ensure_ascii=False, sort_keys=True)}")
    for artifact in artifacts:
        lines.append(f"产物：{artifact.get('kind', '未知')} · {artifact.get('path') or artifact.get('layer_id') or '未提供引用'} · "
                     f"空间验证：{(artifact.get('spatial_validation') or {}).get('status', '未提供')} · "
                     f"可交付：{'是' if artifact.get('deliverable') is True else '否' if artifact.get('deliverable') is False else '未知'}")
    return '\n'.join(lines)


async def reflector_node(state: AgentState) -> Dict[str, Any]:
    """Summarize current execution facts without calling a language model."""
    input_query = state.get("input_query")
    prior_log_summary = state.get("log_summary")
    task_plan = state.get("task_plan")
    is_completed = state.get("is_completed")
    final_summary = _execution_summary(state)
    combined_log_summary = (
        f"{prior_log_summary}\n\n{final_summary}" if prior_log_summary else final_summary
    )
    history_entry = {
        "input_query": input_query,
        "task_plan": _serialize_graph_record(task_plan),
        "final_summary": final_summary,
    }
    cleaned_state = {
        # 保留字段
        "final_summary": final_summary,  # 保留当前任务的总结，供前端显示
        "log_summary": combined_log_summary,  # 用于下一轮Planner的上下文
        "screenshot_path": state.get("screenshot_path"),
        "is_completed": is_completed,
        "history": [history_entry],

        # 清理的字段（重置为初始值）
        "input_query": None,
        "task_plan": None,
        "plan_validation": None,
        "planning_error": None,
        "execution_trace": None,
        "api_context_structured": [],
        "gdal_doc": [],
        "pyqgis_doc": [],
        "advise": None,
        "retry_count": 0,
        "missing_deps": [],
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
        "quality_score": 0.0,  # 按文档要求清理
        "user_feedback": None,
    }
    
    logger.info("状态清理完成，节点执行结束")
    
    return cleaned_state
