#!/usr/bin/env python3
"""
Executor Revise - 使用 create_agent API 的简化版执行器
使用 LangChain 官方推荐的 create_agent 构建执行器，支持手动划定工具范围
"""

from typing import Dict, Any, List, Optional
import logging
import os
from datetime import datetime
from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from agent.llm import create_llm

from agent.state import AgentState, StepContext
from agent.tools.mcp_client import get_mcp_client

load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _get_llm(state: AgentState):
    return create_llm(state.get("llm_config"))


def _filter_tools_by_selection(
    all_tools: List[BaseTool],
    tool_selection: Optional[List[str]]
) -> List[BaseTool]:
    """
    根据用户选择的工具列表筛选工具

    Args:
        all_tools: 所有可用工具
        tool_selection: 工具名称列表，None 表示使用全部工具

    Returns:
        筛选后的工具列表
    """
    if tool_selection is None:
        # 默认使用全部工具
        return all_tools

    if not isinstance(tool_selection, list):
        logger.warning(f"tool_selection 必须是工具名称列表，当前类型: {type(tool_selection)}")
        return all_tools

    tool_map = {tool.name: tool for tool in all_tools}
    selected_names = set(tool_selection)
    return [tool_map[name] for name in selected_names if name in tool_map]


async def _get_available_tools() -> List[BaseTool]:
    """
    从 MCP 客户端获取所有可用工具

    Returns:
        工具列表
    """
    mcp_client = await get_mcp_client()
    tools = await mcp_client.get_tools()
    return tools


def _build_system_prompt(
    task_description: str,
    steps_text: str,
    api_context_parts: List[str],
    tool_catalog: str
) -> str:
    """
    构建系统提示词

    Args:
        task_description: 任务描述（task）
        steps_text: 步骤文本（所有步骤）
        api_context_parts: API 上下文部分
        tool_catalog: 工具目录（可用工具列表）

    Returns:
        系统提示词字符串
    """
    # 简化提示词，专注于让 agent 理解需要调用工具
    prompt = f"""你是一个专业的QGIS开发专家。请通过调用可用的工具来完成以下任务。

## 任务
{task_description}

## 执行步骤
{steps_text}

{f"## API 文档参考\\n{''.join(api_context_parts)}" if api_context_parts else ""}

## 重要说明
- 你必须通过调用工具来完成任务，不要只描述要做什么
- 每完成一个步骤后，继续执行下一个步骤，直到所有步骤完成
- 只有当你确认所有步骤都已实际完成后才停止
"""

    return prompt


async def _create_revise_agent(
    llm,
    tools: List[BaseTool],
    system_prompt: str
):
    """
    使用 create_agent 创建执行器 agent

    Args:
        tools: 可用工具列表
        system_prompt: 系统提示词

    Returns:
        LangChain Agent 实例
    """
    # 创建 agent
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
    )

    logger.info(f"create_agent 返回类型: {type(agent)}")
    logger.info(f"可用工具数量: {len(tools)}")

    return agent


async def _capture_final_screenshot(session_id: str) -> Optional[str]:
    """
    捕获任务完成后的截图（使用时间戳命名）

    Args:
        session_id: 会话ID

    Returns:
        截图文件路径，失败返回 None
    """
    try:
        # 创建截图目录
        screenshot_dir = os.path.join("shared", "screenshots", session_id)
        os.makedirs(screenshot_dir, exist_ok=True)

        # 生成截图文件名（使用时间戳）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_file = os.path.abspath(
            os.path.join(screenshot_dir, f"final_{timestamp}.png")
        )

        # 通过 MCP 获取截图
        mcp_client = await get_mcp_client()
        await mcp_client.call_tool(
            "capture_map_canvas",
            {"path": screenshot_file, "width": 1200, "height": 800}
        )

        # 检查截图是否成功
        if os.path.exists(screenshot_file):
            logger.info(f"最终截图保存成功: {screenshot_file}")
            return screenshot_file
        else:
            logger.warning("最终截图文件未生成")
            return None

    except Exception as e:
        logger.error(f"捕获最终截图失败: {e}")
        return None


async def executor_node(state: AgentState) -> Dict[str, Any]:
    """
    执行器节点（修订版）- 使用 create_agent API

    工作流程:
    1. 从 MCP 获取工具列表，并根据 state 中的 tool_selection 筛选工具
    2. 构建系统提示词（包含 plan 完整信息、步骤和 API 文档）
    3. 使用 create_agent 创建 agent
    4. 调用 agent.ainvoke() 执行任务（ReAct 循环由 agent 内部处理）
    5. 全部执行完后截图
    6. 更新 messages

    Args:
        state: 当前 Agent 状态

        state 中可用的工具选择参数:
            - state.get("tool_selection"): 工具名称列表
                * None: 使用全部工具（默认）
                * ["tool1", "tool2", ...]: 手动指定的工具名称列表

    Returns:
        更新后的状态字段（messages 和 screenshot_path）
    """
    return await _executor_revise_node_async(state)


async def _executor_revise_node_async(state: AgentState) -> Dict[str, Any]:
    """
    执行器节点的异步实现 - 一次性执行整个 plan
    """
    logger.info("=" * 60)
    logger.info("Executor Revise Node: 开始执行任务")
    logger.info("=" * 60)

    session_id = state["session_id"]
    plan = state.get("plan")
    api_context_structured = state.get("api_context_structured", [])
    messages = state.get("messages", [])
    tool_selection = state.get("tool_selection")  # 工具选择范围
    llm = _get_llm(state)

    if not plan:
        logger.error("未找到执行计划")
        return {"messages": messages}

    logger.info(f"任务描述: {plan.task}")
    logger.info(f"工具选择范围: {tool_selection if tool_selection else '全部工具'}")

    # 1. 构建 API 文档上下文（合并所有步骤的文档）
    api_context_parts = []
    if api_context_structured:
        for step_context in api_context_structured:
            if step_context.relevant_docs:
                for doc in step_context.relevant_docs:
                    api_context_parts.append(f"### {doc.api_name} ({doc.library})\n{doc.content}\n")

    # 2. 获取所有可用工具
    all_tools = await _get_available_tools()
    logger.info(f"从 MCP 获取到 {len(all_tools)} 个可用工具")

    # 3. 根据用户选择筛选工具范围
    selected_tools = _filter_tools_by_selection(all_tools, tool_selection)
    logger.info(f"筛选后的工具数量: {len(selected_tools)}")
    for tool in selected_tools:
        logger.info(f"  - {tool.name}: {tool.description or '无描述'}")

    # 构建工具目录文本
    tool_catalog = "\n".join([
        f"- {tool.name}: {tool.description or '无描述'}"
        for tool in selected_tools
    ])

    # 构建步骤文本（包含所有步骤）
    steps_text = ""
    if plan.steps:
        for step in plan.steps:
            steps_text += f"{step.step_id}. {step.description}\n"
    else:
        steps_text = "（无具体步骤）"

    # 4. 构建系统提示词
    system_prompt = _build_system_prompt(
        plan.task,
        steps_text,
        api_context_parts,
        tool_catalog
    )

    # 构建用户消息，明确要求调用工具
    user_message = f"""请通过调用工具来完成以下QGIS任务: {plan.task}

可用步骤:
{steps_text}

请开始执行：首先调用相应的工具来完成第一个步骤。
"""

    logger.info("使用 create_agent 创建 agent 并执行任务...")

    # 5. 创建 executor (包含 agent)
    executor = await _create_revise_agent(llm, selected_tools, system_prompt)

    logger.info(f"[DEBUG] Executor 类型: {type(executor)}")

    # 6. 使用 astream() 执行任务，让 agent 完整执行所有步骤
    logger.info(f"[DEBUG] 开始调用 executor.astream()...")
    logger.info(f"[DEBUG] 用户消息: {user_message[:200]}")
    final_result = None
    step_count = 0
    async for chunk in executor.astream(
        {"messages": [HumanMessage(content=user_message)]},
        stream_mode="values"
    ):
        step_count += 1
        final_result = chunk
        messages = chunk.get('messages', [])
        logger.info(f"[DEBUG] Agent 步骤 {step_count}: 总共 {len(messages)} 条消息")

        # 打印最后一条消息的详细信息
        if messages:
            last_msg = messages[-1]
            msg_type = type(last_msg).__name__
            content = str(last_msg.content)[:150] if hasattr(last_msg, 'content') and last_msg.content else ''
            logger.info(f"[DEBUG]   最后一条消息类型: {msg_type}")
            logger.info(f"[DEBUG]   内容: {content}")

            # 检查是否有 tool_calls
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                tool_names = [tc.get('name', 'unknown') for tc in last_msg.tool_calls]
                logger.info(f"[DEBUG]   工具调用: {tool_names}")
    logger.info(f"[DEBUG] executor.astream() 完成，共 {step_count} 步")

    # 提取最终结果
    if not final_result:
        logger.error("Agent 未返回任何结果")
        raise RuntimeError("Agent 未返回任何结果")

    agent_messages = final_result.get("messages", [])
    logger.info(f"[DEBUG] final_result 包含的键: {list(final_result.keys()) if isinstance(final_result, dict) else 'not a dict'}")
    logger.info(f"Agent 共返回 {len(agent_messages)} 条消息")

    # 打印所有消息以调试
    for i, msg in enumerate(agent_messages):
        content = msg.content if hasattr(msg, 'content') else str(msg)
        logger.info(f"消息 {i+1}: {content[:200] if len(content) > 200 else content}")

    final_message = agent_messages[-1] if agent_messages else None

    if not final_message:
        logger.error("Agent 未返回任何消息")
        raise RuntimeError("Agent 未返回任何消息")

    final_text = final_message.content if final_message.content else ""
    logger.info(f"Agent 执行完成: {final_text}")

    # 7. 更新 messages
    messages.append(AIMessage(content=f"任务完成: {final_text}"))

    # 8. 全部执行完后截图
    screenshot_path = await _capture_final_screenshot(session_id)
    if screenshot_path:
        logger.info(f"任务截图: {screenshot_path}")

    return {
        "messages": messages,
        "screenshot_path": screenshot_path,
        "is_completed": None  # TODO待HITL实现后改为人工确认,暂时确保运行
    }
