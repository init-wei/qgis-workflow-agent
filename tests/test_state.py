#!/usr/bin/env python3
"""
State模块测试
"""

from agent.state import AgentState, create_initial_state, Plan, Step


def test_create_initial_state():
    """测试创建初始状态"""
    session_id = "test_session_123"
    input_query = "加载矢量图层并进行缓冲区分析"
    
    state = create_initial_state(session_id, input_query)
    
    assert state["session_id"] == session_id
    assert state["input_query"] == input_query
    assert state["retry_count"] == 0
    assert state["current_step_id"] == 0
    assert len(state["execution_logs"]) == 0
    assert state["status"] is False


def test_step_model():
    """测试Step模型"""
    step = Step(
        step_id=1,
        description="加载矢量图层",
        gdal_api=["osgeo.ogr.Open"],
        pyqgis_api=["QgsVectorLayer"]
    )
    
    assert step.step_id == 1
    assert step.description == "加载矢量图层"
    assert len(step.gdal_api) == 1
    assert len(step.pyqgis_api) == 1


def test_plan_model():
    """测试Plan模型"""
    steps = [
        Step(step_id=1, description="步骤1", gdal_api=[], pyqgis_api=[]),
        Step(step_id=2, description="步骤2", gdal_api=[], pyqgis_api=[])
    ]
    
    plan = Plan(
        task="测试任务",
        steps=steps,
        metadata={"complexity": "medium"}
    )
    
    assert plan.task == "测试任务"
    assert len(plan.steps) == 2
    assert plan.metadata["complexity"] == "medium"


if __name__ == "__main__":
    print("运行State模块测试...")
    test_create_initial_state()
    print("✅ test_create_initial_state 通过")
    
    test_step_model()
    print("✅ test_step_model 通过")
    
    test_plan_model()
    print("✅ test_plan_model 通过")
    
    print("\n所有测试通过！")
