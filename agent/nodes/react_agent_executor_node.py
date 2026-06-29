#!/usr/bin/env python3
"""Legacy LangChain create_agent executor baseline.

This node is kept as an opt-in comparison path. The default runtime remains the
controlled `ToolCallPlan -> ExecutionLoop` path.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from agent.llm import create_llm
from agent.state import AgentState
from agent.tools.mcp_client import get_mcp_client

logger = logging.getLogger(__name__)


async def react_agent_executor_node(state: AgentState) -> dict[str, Any]:
    """Execute the approved legacy Plan through LangChain create_agent."""

    plan = state.get("plan")
    if plan is None:
        return {
            "messages": [AIMessage(content="ReAct baseline 未开始: 没有已批准的 Plan。")],
            "is_completed": False,
        }

    mcp_client = await get_mcp_client()
    all_tools = await mcp_client.get_tools()
    selected_tools = _filter_tools_by_selection(all_tools, state.get("tool_selection"))
    steps_text = "\n".join(
        f"{step.step_id}. {step.description}" for step in plan.steps
    ) or "（无具体步骤）"
    system_prompt = _build_system_prompt(plan.task, steps_text)
    agent = create_agent(
        model=create_llm(state.get("llm_config")),
        tools=selected_tools,
        system_prompt=system_prompt,
    )

    final_result = None
    async for chunk in agent.astream(
        {
            "messages": [
                HumanMessage(
                    content=(
                        f"请通过调用工具完成以下 QGIS 任务: {plan.task}\n\n"
                        f"步骤:\n{steps_text}"
                    )
                )
            ]
        },
        stream_mode="values",
    ):
        final_result = chunk

    agent_messages = final_result.get("messages", []) if final_result else []
    final_message = agent_messages[-1] if agent_messages else None
    final_text = final_message.content if final_message else "Agent 未返回最终消息。"
    screenshot_path = await _capture_final_screenshot(state["session_id"])
    return {
        "messages": [AIMessage(content=f"ReAct baseline 执行完成: {final_text}")],
        "screenshot_path": screenshot_path,
        "is_completed": None,
    }


def _filter_tools_by_selection(all_tools: list[Any], tool_selection: list[str] | None):
    if not tool_selection:
        return all_tools
    tool_map = {tool.name: tool for tool in all_tools if getattr(tool, "name", None)}
    return [tool_map[name] for name in tool_selection if name in tool_map]


def _build_system_prompt(task_description: str, steps_text: str) -> str:
    return f"""你是一个专业的 QGIS 自动化助手。请通过调用可用工具完成任务。

## 任务
{task_description}

## 执行步骤
{steps_text}

要求：
- 必须实际调用工具，不要只描述做法。
- 按步骤执行，直到任务完成或明确失败。
"""


async def _capture_final_screenshot(session_id: str) -> str | None:
    try:
        screenshot_dir = os.path.join("shared", "screenshots", session_id)
        os.makedirs(screenshot_dir, exist_ok=True)
        screenshot_file = os.path.abspath(
            os.path.join(
                screenshot_dir,
                f"final_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
            )
        )
        mcp_client = await get_mcp_client()
        await mcp_client.call_tool(
            "capture_map_canvas",
            {"path": screenshot_file, "width": 1200, "height": 800},
        )
        return screenshot_file if os.path.exists(screenshot_file) else None
    except Exception as exc:
        logger.warning("ReAct baseline screenshot capture failed: %s", exc)
        return None
