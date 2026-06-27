#!/usr/bin/env python3
"""
Executor Node测试
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

from agent.state import create_initial_state, Plan, Step, StepContext
from agent.nodes.executor_node import executor_node


async def test_executor_node():
    """测试Executor Node"""
    print("测试Executor Node...")
    print("-" * 60)
    
    # 创建测试状态
    state = create_initial_state(
        session_id="test_executor_001",
        input_query="打印QGIS版本信息"
    )
    
    # 创建简单的测试计划
    plan = Plan(
        task="打印QGIS版本",
        steps=[
            Step(
                step_id=1,
                description="打印QGIS版本信息",
                gdal_api=[],
                pyqgis_api=[]
            )
        ],
        metadata={}
    )
    
    state["plan"] = plan
    state["status"] = True
    state["api_context_structured"] = [
        StepContext(
            step_id=1,
            relevant_docs=[]
        )
    ]
    state["tool_selection"] = None
    
    # 调用Executor Revise Node
    result = await executor_node(state)
    
    print("\nExecutor Revise 结果:")
    messages = result.get("messages", [])
    print(f"- 消息数量: {len(messages)}")
    print(f"- 截图路径: {result.get('screenshot_path')}")

    if messages:
        last_msg = messages[-1]
        content = getattr(last_msg, "content", None)
        if content:
            print(f"- 最后一条消息: {content[:200]}...")

    print("\n✅ Executor Revise Node测试通过")


if __name__ == "__main__":
    print("运行Executor Revise Node测试...")
    print("注意: 需要设置OPENAI_API_KEY环境变量")
    print("注意: 需要QGIS MCP插件和MCP Server运行")
    print("=" * 60)
    
    try:
        asyncio.run(test_executor_node())
        
        print("\n" + "=" * 60)
        print("Executor Node测试完成！")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
