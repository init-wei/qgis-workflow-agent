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


async def risk_review_node(state: AgentState) -> dict:
    """Interrupt placeholder for high-risk tool call review."""

    return {}


def should_continue_planning(
    state: AgentState,
) -> Literal["human_review_node", "planner_node", "executor_node", "planning_failed"]:
    """Route a valid TaskPlan to review or execution; stop on planning exhaustion."""
    task_plan = state.get("task_plan")
    review_status = (
        task_plan.get("review_status")
        if isinstance(task_plan, dict)
        else getattr(task_plan, "review_status", None)
    )
    validation = state.get("plan_validation")
    is_valid = (
        validation.get("is_valid", False)
        if isinstance(validation, dict)
        else getattr(validation, "is_valid", False)
    )

    if task_plan and review_status == "approved" and is_valid:
        return "executor_node"
    if task_plan and review_status == "pending_review" and is_valid:
        return "human_review_node"
    if state.get("retry_count", 0) >= 2:
        return "planning_failed"
    return "planner_node"


def should_continue_after_review(state: AgentState) -> Literal["executor_node", "planner_node"]:
    """Execute only an approved valid plan; otherwise return to planning."""
    task_plan = state.get("task_plan")
    review_status = (
        task_plan.get("review_status")
        if isinstance(task_plan, dict)
        else getattr(task_plan, "review_status", None)
    )
    validation = state.get("plan_validation")
    is_valid = (
        validation.get("is_valid", False)
        if isinstance(validation, dict)
        else getattr(validation, "is_valid", False)
    )
    if task_plan and review_status == "approved" and is_valid:
        return "executor_node"
    return "planner_node"


def should_continue_after_executor(state: AgentState) -> Literal["risk_review_node", "reflector_node"]:
    """Route high-risk pauses to risk review; otherwise continue to reflection."""

    if state.get("pending_risk_review") is not None:
        return "risk_review_node"
    return "reflector_node"


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
    3. Risk Review Node -> Executor Node when high-risk approval is needed
    4. Reflector Node -> END
    
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
    workflow.add_node("risk_review_node", risk_review_node)
    workflow.add_node("reflector_node", reflector_node)
    
    # 设置入口点
    workflow.set_entry_point("planner_node")
    
    # 添加边连接
    # Planner -> 条件判断 -> 人工审核 或继续规划 或直接执行
    workflow.add_conditional_edges(
        "planner_node",
        should_continue_planning,
        {
            "human_review_node": "human_review_node",  # 生成待审 TaskPlan 后进入审核
            "planner_node": "planner_node",  # 继续规划（有修改意见时）
            "executor_node": "executor_node",  # 已批准的有效 TaskPlan
            "planning_failed": END,
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
    
    # Executor -> Risk Review 或 Reflector
    workflow.add_conditional_edges(
        "executor_node",
        should_continue_after_executor,
        {
            "risk_review_node": "risk_review_node",
            "reflector_node": "reflector_node",
        },
    )

    # Risk Review -> Executor（审批后恢复）
    workflow.add_edge("risk_review_node", "executor_node")
    
    # Reflector -> END（任务完成）
    workflow.add_edge("reflector_node", END)
    
    # 编译Graph
    # 注意：interrupt功能需要checkpointer支持
    if checkpointer:
        compile_kwargs = {"checkpointer": checkpointer}
        # 默认在human_review_node之前中断（如果未指定）
        if interrupt_before is None:
            interrupt_before = ["human_review_node", "risk_review_node"]
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
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL must point to this project's PostgreSQL database.")
    
    pool = AsyncConnectionPool(
        conninfo=db_url,
        max_size=10,
        open=False,
        kwargs={"autocommit": True},
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
