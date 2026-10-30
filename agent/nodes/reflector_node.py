#!/usr/bin/env python3
"""
Reflector Node - 反思与归档节点
负责执行结果总结、价值判断与归档、状态清理
"""

from typing import Dict, Any
import logging
from dotenv import load_dotenv

from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from jinja2 import Template
from agent.llm import create_llm

from agent.state import AgentState
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain.messages import RemoveMessage  
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _get_llm(state: AgentState):
    return create_llm(state.get("llm_config"))


def _serialize_task_plan(task_plan: Any) -> Any:
    """Serialize the authoritative TaskPlan for user-visible history."""
    if task_plan is None:
        return None
    if hasattr(task_plan, "model_dump"):
        return task_plan.model_dump()
    if hasattr(task_plan, "dict"):
        return task_plan.dict()
    return task_plan

def _get_last_executor_message(messages) -> str:
    """
    提取 Executor 的最后一条自然语言总结消息
    规则（基于真实日志）：
    1. 从后往前遍历
    2. 只处理 AIMessage
    3. content 为 str，且不是 JSON 结构
    4. 直接返回第一条命中的
    """
    if not messages:
        return "无任务总结"

    for msg in reversed(messages):

        # 只关心 AIMessage
        if not isinstance(msg, AIMessage):
            continue

        content = msg.content

        # 1️⃣ 最理想情况：content 是字符串（你的最终总结就是这种）
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text

        # 2️⃣ 次优情况：content 是 list（多模态 / 工具返回）
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    parts.append(part)

            text = "\n".join(parts).strip()

            # 排除明显是 JSON 的工具结果
            if text and not text.startswith("{"):
                return text

    return "无任务总结"

async def reflector_node(state: AgentState) -> Dict[str, Any]:
    """
    反思与归档节点 - 总结任务并归档成功案例
    
    工作流程:
    1. 分析执行日志，判断任务是否成功
    2. 生成任务总结
    3. 评估任务价值（质量评分）
    4. 如果值得归档，保存到Cookbook
    5. 生成截图（如果需要）
    6. 清理临时状态
    
    Args:
        state: 当前Agent状态
        
    Returns:
        更新后的状态字段
    """
    logger.info("=" * 60)
    logger.info("Reflector Node: 开始反思与归档")
    logger.info("=" * 60)
    
    llm = _get_llm(state)
    input_query = state["input_query"]
    prior_log_summary = state.get("log_summary")
    task_plan = state.get("task_plan")
    messages = state.get("messages", [])
    
    # 1. 分析执行结果
    total_steps = len(task_plan.steps) if task_plan else 0
    is_completed = state.get("is_completed")
    if is_completed is None:
        is_success = len(messages) > 0
    else:
        is_success = bool(is_completed)

    error_count = 0 if is_success else 1
    
    # 2. 生成任务总结
    logger.info("生成任务总结...")
    
    # 构建执行日志摘要
    log_summary_parts = []

    for msg in messages:
        content = getattr(msg, "content", "") or ""
        if content:
            log_summary_parts.append(f"- {content}")
    
    log_summary_text = "\n".join(log_summary_parts) if log_summary_parts else "无可用日志"
    
    detail_info = ""
    
    # 调用LLM生成总结
    system_prompt_template = Template(
        """你是一个专业的GIS任务分析专家。请分析以下任务执行情况，生成简洁的总结报告。

总结应包含:
1. 任务完成情况（成功/失败）
2. 关键步骤回顾
3. 使用的图层和变量（如果有）
4. 遇到的问题（如果有）
5. 经验教训（如果有）

请用简洁的中文回答，不超过300字。{% if detail_info %}{{ detail_info }}{% endif %}
"""
    )
    system_prompt = system_prompt_template.render(detail_info=detail_info)

    user_message_template = Template(
        """
任务需求: {{ input_query }}

执行计划: {{ plan_task }}

执行日志:
{{ log_summary_text }}

总步骤数: {{ total_steps }}
执行结果: {{ "成功" if is_success else "失败" }}
{% if not is_success %}用户认为的失败原因: {{ user_feedback }}{% endif %}
"""
    )
    user_message = user_message_template.render(
        input_query=input_query,
        plan_task=task_plan.task_summary if task_plan else "无计划",
        log_summary_text=log_summary_text,
        total_steps=total_steps,
        is_success=is_success,
        user_feedback=state.get("user_feedback", "无反馈"),
    )
    
    try:
        reflector_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message)
        ]
        
        response = await llm.ainvoke(reflector_messages)
        final_summary = response.content.strip()
        
        logger.info(f"生成总结: {final_summary[:100]}...")
        
    except Exception as e:
        logger.error(f"生成总结失败: {e}")
        final_summary = f"任务{'成功' if is_success else '失败'} (总步骤: {total_steps})"
    
    # 3. 评估任务价值
    quality_score = 0.0
    
    if is_success:
        # 成功任务基础分 0.6
        quality_score = 0.6
        
        # 根据复杂度加分
        if total_steps >= 5:
            quality_score += 0.2
        elif total_steps >= 3:
            quality_score += 0.1
        
        # 如果有重试但最终成功，加分（说明有纠错能力）
        if error_count > 0:
            quality_score += 0.1
        
        quality_score = min(quality_score, 1.0)
    else:
        # 失败任务
        quality_score = max(0.0, 0.3 - (error_count * 0.1))
    
    logger.info(f"质量评分: {quality_score:.2f}")
    
    # 4. Preserve the user's separate result-confirmation decision.
    logger.info("Execution success=%s; result confirmation=%s", is_success, is_completed)

    # 5. State cleanup
    # 保留最终摘要、截图和历史；清理下一轮不应复用的计划与执行状态。
    logger.info("执行状态清理...")

    combined_log_summary = (
        f"{prior_log_summary}\n\n{final_summary}" if prior_log_summary else final_summary
    )
    history_entry = {
        "input_query": input_query,
        "task_plan": _serialize_task_plan(task_plan),
        "executor_last_message": _get_last_executor_message(messages),
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
