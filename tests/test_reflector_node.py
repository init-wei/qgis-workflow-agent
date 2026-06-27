#!/usr/bin/env python3
"""
Reflector Node测试
"""

# 添加项目根目录到sys.path（必须在导入agent之前）
import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


import asyncio
from dotenv import load_dotenv

# 加载.env环境变量
load_dotenv()

from agent.state import create_initial_state, Plan, Step
from langchain_core.messages import AIMessage
from agent.nodes.reflector_node import reflector_node


async def test_reflector_node():
    """测试Reflector Node"""
    print("测试Reflector Node...")
    print("-" * 60)
    
    # 创建测试状态
    state = create_initial_state(
        session_id="test_reflector_001",
        input_query="加载矢量图层测试"
    )
    
    # 创建测试计划
    plan = Plan(
        task="加载矢量图层",
        steps=[
            Step(step_id=1, description="加载shapefile", gdal_api=[], pyqgis_api=[]),
            Step(step_id=2, description="显示图层", gdal_api=[], pyqgis_api=[])
        ],
        metadata={"estimated_complexity": "low"}
    )
    
    state["plan"] = plan
    state["messages"] = [
        AIMessage(content="步骤1: 使用QgsRasterLayer加载数据源 D:/data/sample.tif，图层命名为 sample_raster"),
        AIMessage(content="步骤2: 将图层添加到QgsProject并刷新地图画布，检查坐标系为 EPSG:4326"),
        AIMessage(content="结果: 图层加载成功，可视化正常，未出现错误")
    ]
    state["is_completed"] = True
    
    # 调用Reflector Node
    result = await reflector_node(state)
    
    print("\nReflector结果:")
    print(f"- 最终总结: {result.get('final_summary')[:100] if result.get('final_summary') else None}...")
    print(f"- 质量评分: {result.get('quality_score')}")
    print(f"- 截图路径: {result.get('screenshot_path')}")
    print(f"- 任务完成: {result.get('is_completed')}")
    
    # 检查状态是否清理
    print(f"\n状态清理检查:")
    print(f"- draft: {result.get('draft')}")
    print(f"- plan: {result.get('plan')}")
    print(f"- execution_logs: {len(result.get('execution_logs', []))}")
    print(f"- messages: {len(result.get('messages', []))}")
    
    print("\n✅ Reflector Node测试通过")


if __name__ == "__main__":
    print("运行Reflector Node测试...")
    print("注意: 需要设置OPENAI_API_KEY环境变量")
    print("注意: 需要QGIS MCP插件和MCP Server运行（用于截图）")
    print("=" * 60)
    
    try:
        asyncio.run(test_reflector_node())
        
        print("\n" + "=" * 60)
        print("Reflector Node测试完成！")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
