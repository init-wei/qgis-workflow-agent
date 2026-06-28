#!/usr/bin/env python3
"""
测试LangGraph Graph构建和执行流程
"""

import sys
import os
import pytest

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.graph import (
    build_graph,
    should_continue_after_executor,
    should_continue_planning,
)
from agent.state import AgentState, create_initial_state, Plan, Step
from agent.tools.tool_contracts import HumanFeedback, ToolCallPlan


def make_state(**overrides) -> AgentState:
    """Create a minimal current AgentState for graph routing tests."""
    state = create_initial_state(
        session_id="test_session",
        input_query="test",
    )
    state.update(overrides)
    return state


class TestGraphConstruction:
    """测试Graph构建"""
    
    def test_build_graph_without_checkpointer(self):
        """测试不带checkpointer的Graph构建"""
        app = build_graph(checkpointer=None)
        assert app is not None
        print("✅ Graph构建成功（无checkpointer）")
    
    def test_graph_has_all_nodes(self):
        """测试Graph包含所有必需的节点"""
        app = build_graph(checkpointer=None)
        graph = app.get_graph()
        
        # 获取所有节点
        nodes = [node.id for node in graph.nodes.values()]
        
        required_nodes = [
            "planner_node",
            "human_review_node",
            "executor_node",
            "risk_review_node",
            "reflector_node",
        ]
        for node in required_nodes:
            assert node in nodes, f"缺少节点: {node}"
        
        print(f"✅ Graph包含所有必需节点: {required_nodes}")
    
    def test_graph_entry_point(self):
        """测试Graph的入口点"""
        app = build_graph(checkpointer=None)
        graph = app.get_graph()
        
        # 检查入口点
        # LangGraph的入口点通常是第一个执行的节点
        assert "__start__" in [node.id for node in graph.nodes.values()]
        print("✅ Graph入口点配置正确")


class TestConditionalEdges:
    """测试条件边逻辑"""
    
    def test_should_continue_planning_approved(self):
        """测试规划批准后的路由"""
        state = make_state(status=True, plan=Plan(task="测试任务", steps=[], metadata={}))
        
        result = should_continue_planning(state)
        assert result == "executor_node"
        print("✅ 规划批准后正确路由到executor_node")
    
    def test_should_continue_planning_force_exit(self):
        """测试强制退出机制"""
        state = make_state(status=False, retry_count=5)
        
        result = should_continue_planning(state)
        assert result == "executor_node"
        print("✅ 强制退出机制正确路由到executor_node")
    
    def test_should_continue_planning_need_revision(self):
        """测试需要修改规划的路由"""
        state = make_state(status=False, retry_count=1)
        
        result = should_continue_planning(state)
        assert result == "planner_node"
        print("✅ 需要修改时正确路由回planner_node")

    def test_should_continue_after_executor_routes_pending_risk_review(self):
        """测试执行器遇到风险审核时进入风险审核节点"""
        state = make_state(
            pending_risk_review=HumanFeedback(
                review_id="review-1",
                status="pending",
                tool_call=ToolCallPlan(
                    step_id=1,
                    tool_name="execute_code",
                    arguments={"code": "print('x')"},
                ),
            )
        )

        result = should_continue_after_executor(state)

        assert result == "risk_review_node"

    def test_should_continue_after_executor_routes_to_reflector_without_risk_review(self):
        """测试无风险审核时进入反思节点"""
        state = make_state(pending_risk_review=None)

        result = should_continue_after_executor(state)

        assert result == "reflector_node"


class TestGraphVisualization:
    """测试Graph可视化"""
    
    def test_draw_mermaid(self):
        """测试生成Mermaid图"""
        app = build_graph(checkpointer=None)
        graph = app.get_graph()
        
        mermaid_code = graph.draw_mermaid()
        assert mermaid_code is not None
        assert "planner_node" in mermaid_code
        assert "api_rag_node" not in mermaid_code
        assert "human_review_node" in mermaid_code
        assert "risk_review_node" in mermaid_code
        assert "executor_node" in mermaid_code
        assert "reflector_node" in mermaid_code
        
        print("✅ Mermaid图生成成功")
        print("\n" + "="*60)
        print("Graph结构:")
        print("="*60)
        print(mermaid_code)


if __name__ == "__main__":
    print("\n" + "="*60)
    print("开始测试LangGraph构建")
    print("="*60 + "\n")
    
    # 运行测试
    pytest.main([__file__, "-v", "-s"])
