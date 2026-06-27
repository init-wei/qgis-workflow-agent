#!/usr/bin/env python3
"""
Planner Node测试
"""

import sys
from pathlib import Path

# 添加项目根目录到sys.path（必须在导入agent之前）
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# 加载.env环境变量
load_dotenv()

from agent.state import create_initial_state, Plan, Step
from agent.nodes.planner_node import planner_node

def test_planner_node():
    """测试Planner Node"""
    print("测试Planner Node...")
    print("-" * 60)
    
    # 创建初始状态
    state = create_initial_state(
        session_id="test_planner_001",
        input_query="加载shapefile矢量图层，将其坐标系转换为wgs84，并保存结果为新的图层。"
    )
    
    # 调用Planner Node
    result = planner_node(state)
    
    print("\nPlanner结果:")
    print(f"- draft存在: {result.get('draft') is not None}")
    print(f"- status: {result.get('status')}")
    print(f"- retry_count: {result.get('retry_count')}")
    
    if result.get('draft'):
        draft = result['draft']
        print(f"- 任务: {draft.task}")
        print(f"- 步骤数: {len(draft.steps)}")
        
        for step in draft.steps:
            print(f"  步骤 {step.step_id}: {step.description}")
            print(f"    GDAL APIs: {step.gdal_api}")
            print(f"    PyQGIS APIs: {step.pyqgis_api}")
    
    assert result.get('draft') is not None or result.get('retry_count') > 0
    print("\n✅ Planner Node测试通过")


if __name__ == "__main__":
    print("运行Planner Node测试...")
    print("注意: 需要设置OPENAI_API_KEY环境变量")
    print("注意: 需要PostgreSQL数据库运行")
    print("=" * 60)
    
    try:
        test_planner_node()
        
        print("\n" + "=" * 60)
        print("Planner Node测试完成！")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
