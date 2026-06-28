#!/usr/bin/env python3
"""
LangGraph 构建模块
定义Agent的状态机、节点连接和执行流程
"""

import os
import asyncio
from typing import Optional, Literal
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from agent.state import AgentState
from agent.nodes.planner_node import planner_node
from agent.nodes.executor_node import executor_node
from agent.nodes.reflector_node import reflector_node
try:
    from agent.observability import configure_langsmith
except ModuleNotFoundError:
    def configure_langsmith() -> None:
        """Optional LangSmith hook; local development works without it."""
        return None

load_dotenv()


async def human_review_node(state: AgentState) -> dict:
    """
    人工审核节点
    这是一个中断点，等待外部输入审核结果
    
    注意: 此节点会立即返回当前状态，实际的审核在中断后通过update_state完成
    
    Args:
        state: 当前状态
        
    Returns:
        空字典（保持状态不变）
    """
    # 此节点仅作为人工审核的占位符
    # 实际的审核逻辑在外部通过app.update_state()完成
    return {}


def should_continue_planning(state: AgentState) -> Literal["human_review_node", "planner_node", "executor_node"]:
    """
    判断规划是否完成，是否需要人工审核或继续规划
    
    逻辑:
    - 如果plan已存在且已批准(status=True)，直接进入受控执行
    - 如果达到重试上限(retry_count>=3)，强制进入受控执行
    - 如果已有draft且未审核(status=False)，进入人工审核节点
    - 其他情况继续规划
    
    Args:
        state: 当前状态
        
    Returns:
        下一个节点名称
    """
    # 优先检查：如果plan已批准，直接执行
    if state.get("plan") and state.get("status", False):
        return "executor_node"
    
    # 检查重试上限（强制退出）
    if state.get("retry_count", 0) >= 5:
        return "executor_node"
    
    # 如果生成了draft但未审核，进入人工审核
    if state.get("draft") and not state.get("status", False):
        return "human_review_node"
    
    # 其他情况继续规划（首次规划或有修改意见）
    return "planner_node"


def should_continue_after_review(state: AgentState) -> Literal["executor_node", "planner_node"]:
    """
    人工审核后的路由决策
    
    逻辑:
    - 如果已批准(status=True)，进入API RAG
    - 如果有修改意见(advise)或未批准，返回planner重新规划
    - 如果达到重试上限，强制进入API RAG
    
    Args:
        state: 当前状态
        
    Returns:
        下一个节点名称
    """
    # 优先检查：是否批准
    if state.get("status", False):
        return "executor_node"
    
    # 检查重试上限（强制退出）
    if state.get("retry_count", 0) >= 3:
        return "executor_node"
    
    # 有修改意见或未批准，返回planner
    return "planner_node"


def build_graph(
    checkpointer: Optional[AsyncPostgresSaver] = None,
    interrupt_before: Optional[list] = None,
    interrupt_after: Optional[list] = None
) -> StateGraph:
    """
    构建LangGraph状态图
    
    工作流程:
    1. Planner Node -> (条件判断) -> 人工审核 / 受控执行 / 继续修改
    2. Executor Node -> TaskPlan 编译为 ToolCallPlan 并通过 ExecutionLoop 执行
    3. Reflector Node -> END
    
    HITL机制（需要checkpointer）:
    - interrupt_before: 在指定节点之前中断（例如["human_review_node"]用于计划审核）
    - interrupt_after: 在指定节点之后中断（例如["reflector_node"]用于结果确认）
    
    Args:
        checkpointer: PostgreSQL checkpointer，用于状态持久化和HITL
        interrupt_before: 在这些节点之前中断
        interrupt_after: 在这些节点之后中断
        
    Returns:
        编译后的StateGraph
    """
    # 创建状态图
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("planner_node", planner_node)
    workflow.add_node("human_review_node", human_review_node)  # 新增：人工审核节点
    workflow.add_node("executor_node", executor_node)
    workflow.add_node("reflector_node", reflector_node)
    
    # 设置入口点
    workflow.set_entry_point("planner_node")
    
    # 添加边连接
    # Planner -> 条件判断 -> 人工审核 或继续规划 或直接执行
    workflow.add_conditional_edges(
        "planner_node",
        should_continue_planning,
        {
            "human_review_node": "human_review_node",  # 生成draft后进入审核
            "planner_node": "planner_node",  # 继续规划（有修改意见时）
            "executor_node": "executor_node"  # plan已批准，直接执行
        }
    )
    
    # 人工审核 -> 条件判断 -> 受控执行或返回Planner
    workflow.add_conditional_edges(
        "human_review_node",
        should_continue_after_review,
        {
            "executor_node": "executor_node",  # 审核通过，继续执行
            "planner_node": "planner_node"  # 需要修改，重新规划
        }
    )
    
    # Executor -> Reflector（执行结束直接进入Reflector）
    workflow.add_edge("executor_node", "reflector_node")
    
    # Reflector -> END（任务完成）
    workflow.add_edge("reflector_node", END)
    
    # 编译Graph
    # 注意：interrupt功能需要checkpointer支持
    if checkpointer:
        compile_kwargs = {"checkpointer": checkpointer}
        # 默认在human_review_node之前中断（如果未指定）
        if interrupt_before is None:
            interrupt_before = ["human_review_node"]
        if interrupt_before:
            compile_kwargs["interrupt_before"] = interrupt_before
        # 默认在executor_node之后中断（如果未指定）
        if interrupt_after is None:
            interrupt_after = ["executor_node"]
        if interrupt_after:
            compile_kwargs["interrupt_after"] = interrupt_after
        app = workflow.compile(**compile_kwargs)
    else:
        app = workflow.compile()
    
    return app


async def create_checkpointer() -> AsyncPostgresSaver:
    """
    创建PostgreSQL checkpointer用于状态持久化
    
    Returns:
        PostgresSaver实例
    """
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/qgis_db")
    
    pool = AsyncConnectionPool(
        conninfo=db_url,
        max_size=10,
        open=False
    )
    await pool.open()
    
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # 创建必要的表结构
    return checkpointer


async def get_graph(
    with_checkpointer: bool = True,
    interrupt_before: list = None,
    interrupt_after: list = None,
    reset_thread_id: Optional[str] = None,
) -> StateGraph:
    """
    获取配置好的Graph实例
    
    Args:
        with_checkpointer: 是否启用状态持久化
        interrupt_before: 在这些节点之前中断
        interrupt_after: 在这些节点之后中断
        
    Returns:
        编译后的StateGraph
    """
    configure_langsmith()
    if with_checkpointer:
        checkpointer = await create_checkpointer()
        if reset_thread_id:
            await checkpointer.adelete_thread(reset_thread_id)
        return build_graph(checkpointer, interrupt_before=interrupt_before, interrupt_after=interrupt_after)
    else:
        return build_graph(interrupt_before=interrupt_before, interrupt_after=interrupt_after)


if __name__ == "__main__":
    # 测试Graph构建
    print("Building LangGraph...")
    app = asyncio.run(get_graph(with_checkpointer=False))
    print("Graph built successfully!")
    print("\nGraph structure:")
    print(app.get_graph().draw_mermaid())
