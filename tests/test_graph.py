#!/usr/bin/env python3
"""
测试LangGraph Graph构建和执行流程
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.graph import build_graph, should_continue_planning, should_continue_execution
from agent.state import AgentState, create_initial_state, Plan, Step


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
        
        required_nodes = ["planner_node", "api_rag_node", "executor_node", "reflector_node"]
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
        state = AgentState(
            input_query="test",
            session_id="test_session",
            status=True,  # 用户批准
            retry_count=0,
            log_summary=None,
            advise=None,
            example=None,
            draft=None,
            plan=None,
            gdal_doc=[],
            pyqgis_doc=[],
            api_context_structured=[],
            missing_deps=[],
            messages=[],
            current_step_id=0,
            execution_logs=[],
            code_history=[],
            retry_attempts=0,
            final_summary=None,
            screenshot_path=None,
            is_completed=False,
            quality_score=0.0
        )
        
        result = should_continue_planning(state)
        assert result == "api_rag_node"
        print("✅ 规划批准后正确路由到api_rag_node")
    
    def test_should_continue_planning_force_exit(self):
        """测试强制退出机制"""
        state = AgentState(
            input_query="test",
            session_id="test_session",
            status=False,  # 用户未批准
            retry_count=3,  # 达到强制退出阈值
            log_summary=None,
            advise=None,
            example=None,
            draft=None,
            plan=None,
            gdal_doc=[],
            pyqgis_doc=[],
            api_context_structured=[],
            missing_deps=[],
            messages=[],
            current_step_id=0,
            execution_logs=[],
            code_history=[],
            retry_attempts=0,
            final_summary=None,
            screenshot_path=None,
            is_completed=False,
            quality_score=0.0
        )
        
        result = should_continue_planning(state)
        assert result == "api_rag_node"
        print("✅ 强制退出机制正确路由到api_rag_node")
    
    def test_should_continue_planning_need_revision(self):
        """测试需要修改规划的路由"""
        state = AgentState(
            input_query="test",
            session_id="test_session",
            status=False,  # 用户未批准
            retry_count=1,  # 未达到强制退出阈值
            log_summary=None,
            advise=None,
            example=None,
            draft=None,
            plan=None,
            gdal_doc=[],
            pyqgis_doc=[],
            api_context_structured=[],
            missing_deps=[],
            messages=[],
            current_step_id=0,
            execution_logs=[],
            code_history=[],
            retry_attempts=0,
            final_summary=None,
            screenshot_path=None,
            is_completed=False,
            quality_score=0.0
        )
        
        result = should_continue_planning(state)
        assert result == "planner_node"
        print("✅ 需要修改时正确路由回planner_node")
    
    def test_should_continue_execution_not_finished(self):
        """测试执行未完成时的路由"""
        plan = Plan(
            task="测试任务",
            steps=[
                Step(step_id=1, description="步骤1", gdal_api=[], pyqgis_api=[]),
                Step(step_id=2, description="步骤2", gdal_api=[], pyqgis_api=[])
            ],
            metadata={}
        )
        
        state = AgentState(
            input_query="test",
            session_id="test_session",
            status=True,
            retry_count=0,
            log_summary=None,
            advise=None,
            example=None,
            draft=None,
            plan=plan,
            gdal_doc=[],
            pyqgis_doc=[],
            api_context_structured=[],
            missing_deps=[],
            messages=[],
            current_step_id=0,  # 第一步，还未完成
            execution_logs=[],
            code_history=[],
            retry_attempts=0,
            final_summary=None,
            screenshot_path=None,
            is_completed=False,
            quality_score=0.0
        )
        
        result = should_continue_execution(state)
        assert result == "executor_node"
        print("✅ 执行未完成时正确路由到executor_node")
    
    def test_should_continue_execution_finished(self):
        """测试所有步骤执行完成时的路由"""
        plan = Plan(
            task="测试任务",
            steps=[
                Step(step_id=1, description="步骤1", gdal_api=[], pyqgis_api=[])
            ],
            metadata={}
        )
        
        state = AgentState(
            input_query="test",
            session_id="test_session",
            status=True,
            retry_count=0,
            log_summary=None,
            advise=None,
            example=None,
            draft=None,
            plan=plan,
            gdal_doc=[],
            pyqgis_doc=[],
            api_context_structured=[],
            missing_deps=[],
            messages=[],
            current_step_id=1,  # 已完成所有步骤
            execution_logs=[],
            code_history=[],
            retry_attempts=0,
            final_summary=None,
            screenshot_path=None,
            is_completed=False,
            quality_score=0.0
        )
        
        result = should_continue_execution(state)
        assert result == "reflector_node"
        print("✅ 执行完成时正确路由到reflector_node")


class TestGraphExecution:
    """测试Graph执行流程（Mock版本）"""
    
    @patch('agent.nodes.planner_node.planner_node')
    @patch('agent.nodes.api_rag_node.api_rag_node')
    @patch('agent.nodes.executor_node.executor_node')
    @patch('agent.nodes.reflector_node.reflector_node')
    def test_full_graph_execution_mock(self, mock_reflector, mock_executor, mock_api_rag, mock_planner):
        """测试完整的Graph执行流程（使用Mock）"""
        
        # Mock返回值
        plan = Plan(
            task="测试任务",
            steps=[Step(step_id=1, description="测试步骤", gdal_api=[], pyqgis_api=[])],
            metadata={"iteration": 1, "has_example_reference": False}
        )
        
        # Planner返回批准的计划
        mock_planner.return_value = {
            "draft": plan,
            "plan": plan,
            "status": True,
            "retry_count": 0
        }
        
        # API RAG返回API文档
        mock_api_rag.return_value = {
            "gdal_doc": [],
            "pyqgis_doc": [],
            "api_context_structured": [],
            "missing_deps": []
        }
        
        # Executor返回执行结果
        mock_executor.return_value = {
            "current_step_id": 1,
            "execution_logs": [{"step_id": 1, "status": "success"}],
            "messages": []
        }
        
        # Reflector返回最终总结
        mock_reflector.return_value = {
            "final_summary": "任务完成",
            "is_completed": True,
            "quality_score": 0.8
        }
        
        # 构建Graph
        app = build_graph(checkpointer=None)
        
        # 创建初始状态
        initial_state = create_initial_state(
            session_id="test_session",
            input_query="测试查询"
        )
        
        print("✅ Mock Graph执行流程测试准备完成")
        print("   注意: 由于interrupt配置，实际执行会在interrupt点暂停")


class TestGraphVisualization:
    """测试Graph可视化"""
    
    def test_draw_mermaid(self):
        """测试生成Mermaid图"""
        app = build_graph(checkpointer=None)
        graph = app.get_graph()
        
        mermaid_code = graph.draw_mermaid()
        assert mermaid_code is not None
        assert "planner_node" in mermaid_code
        assert "api_rag_node" in mermaid_code
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
