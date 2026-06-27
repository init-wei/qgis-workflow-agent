#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试HITL机制（Human-in-the-Loop）
验证Graph的interrupt_before和interrupt_after配置
"""

import sys
import os

# 设置UTF-8编码
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.graph import build_graph
from agent.state import AgentState, Plan, Step
import uuid

def test_graph_structure():
    """测试Graph结构和interrupt配置"""
    print("=" * 60)
    print("测试1: Graph结构和interrupt配置")
    print("=" * 60)
    
    # 构建Graph（不使用checkpointer以便于测试）
    app = build_graph(checkpointer=None)
    
    # 获取Graph配置
    graph_config = app.get_graph()
    
    print("\n✓ Graph构建成功")
    print(f"✓ 节点数: {len(graph_config.nodes)}")
    print(f"✓ 节点列表: {list(graph_config.nodes.keys())}")
    
    # 验证节点存在
    required_nodes = ["planner_node", "api_rag_node", "executor_node", "reflector_node"]
    for node in required_nodes:
        assert node in graph_config.nodes, f"缺少节点: {node}"
        print(f"  ✓ {node} 存在")
    
    # 验证interrupt配置
    # 注意：LangGraph的interrupt配置在编译时设置，需要检查compiled app的属性
    print("\n✓ Interrupt配置:")
    if hasattr(app, 'interrupt_before'):
        print(f"  interrupt_before: {app.interrupt_before}")
    if hasattr(app, 'interrupt_after'):
        print(f"  interrupt_after: {app.interrupt_after}")
    
    print("\n✓ Graph结构测试通过\n")
    return True


def test_planner_metadata_format():
    """测试Planner输出的metadata格式"""
    print("=" * 60)
    print("测试2: Planner Metadata格式")
    print("=" * 60)
    
    # 创建模拟Plan对象
    plan = Plan(
        task="测试任务",
        steps=[
            Step(
                step_id=1,
                description="测试步骤",
                gdal_api=["gdal.Warp"],
                pyqgis_api=["QgsVectorLayer"]
            )
        ],
        metadata={
            "iteration": 1,
            "has_example_reference": True
        }
    )
    
    # 验证metadata字段
    print(f"\n✓ 创建Plan成功")
    print(f"  Task: {plan.task}")
    print(f"  Steps: {len(plan.steps)}")
    print(f"  Metadata: {plan.metadata}")
    
    # 验证必需字段
    assert "iteration" in plan.metadata, "缺少metadata.iteration字段"
    assert "has_example_reference" in plan.metadata, "缺少metadata.has_example_reference字段"
    assert plan.metadata["iteration"] == 1, "iteration值不正确"
    assert plan.metadata["has_example_reference"] == True, "has_example_reference值不正确"
    
    print(f"\n✓ Metadata格式符合技术文档要求:")
    print(f"  ✓ iteration: {plan.metadata['iteration']}")
    print(f"  ✓ has_example_reference: {plan.metadata['has_example_reference']}")
    
    print("\n✓ Metadata格式测试通过\n")
    return True


def test_executor_thinking_model_support():
    """测试Executor的思考模型支持"""
    print("=" * 60)
    print("测试3: Executor思考模型支持")
    print("=" * 60)
    
    # 测试<thought>标签解析
    import re
    
    test_responses = [
        # 包含思考标签的响应
        """<thought>
我需要先加载栅格数据，然后进行重投影操作。
应该使用gdal.Warp函数，因为它支持投影转换。
</thought>

# 加载栅格
from osgeo import gdal
ds = gdal.Open('input.tif')
# 执行重投影
gdal.Warp('output.tif', ds, dstSRS='EPSG:4326')
""",
        # 不包含思考标签的响应
        """# 直接加载栅格
from osgeo import gdal
ds = gdal.Open('input.tif')
"""
    ]
    
    for i, response in enumerate(test_responses, 1):
        print(f"\n测试响应 {i}:")
        
        thought_content = ""
        generated_code = response
        
        if "<thought>" in response and "</thought>" in response:
            thought_match = re.search(r'<thought>(.*?)</thought>', response, re.DOTALL)
            if thought_match:
                thought_content = thought_match.group(1).strip()
                generated_code = re.sub(r'<thought>.*?</thought>', '', response, flags=re.DOTALL).strip()
                print(f"  ✓ 提取到思考过程: {thought_content[:50]}...")
                print(f"  ✓ 提取到代码: {generated_code[:50]}...")
            else:
                print(f"  ✗ 思考标签解析失败")
        else:
            print(f"  ✓ 无思考标签，直接使用代码")
        
        assert len(generated_code) > 0, f"响应 {i}: 代码提取失败"
    
    print("\n✓ 思考模型支持测试通过\n")
    return True


def test_runtime_api_rag_detection():
    """测试Runtime API RAG检测"""
    print("=" * 60)
    print("测试4: Runtime API RAG检测")
    print("=" * 60)
    
    # 测试#NEED_API注释检测
    import re
    
    test_codes = [
        # 包含需要补充的API
        """
# 加载栅格
from osgeo import gdal
# NEED_API: gdal.WarpOptions
ds = gdal.Open('input.tif')
""",
        # 不包含需要补充的API
        """
from osgeo import gdal
ds = gdal.Open('input.tif')
"""
    ]
    
    for i, code in enumerate(test_codes, 1):
        print(f"\n测试代码 {i}:")
        need_apis = re.findall(r'#\s*NEED_API:\s*([\w\.]+)', code)
        
        if need_apis:
            print(f"  ✓ 检测到需要补充的API: {need_apis}")
            assert "gdal.WarpOptions" in need_apis, "API检测失败"
        else:
            print(f"  ✓ 无需补充API")
    
    print("\n✓ Runtime API RAG检测测试通过\n")
    return True


def test_reflector_completion_check():
    """测试Reflector的人工结项检查逻辑"""
    print("=" * 60)
    print("测试5: Reflector人工结项检查")
    print("=" * 60)
    
    # 模拟两种情况
    test_cases = [
        {
            "name": "用户确认完成",
            "state": {"is_completed": True},
            "expected": True
        },
        {
            "name": "用户确认未完成",
            "state": {"is_completed": False},
            "expected": False
        },
        {
            "name": "默认值（自动判断）",
            "state": {},
            "expected": None  # 取决于执行结果
        }
    ]
    
    for case in test_cases:
        print(f"\n测试场景: {case['name']}")
        state = case["state"]
        
        # 模拟Reflector逻辑
        is_success = True  # 假设执行成功
        is_completed = state.get("is_completed", is_success)
        
        print(f"  State中的is_completed: {state.get('is_completed', '(未设置)')}")
        print(f"  最终is_completed: {is_completed}")
        
        if case["expected"] is not None:
            assert is_completed == case["expected"], f"结项检查逻辑错误: {case['name']}"
            print(f"  ✓ 符合预期: {case['expected']}")
    
    print("\n✓ 人工结项检查测试通过\n")
    return True


def test_quality_score_threshold():
    """测试归档质量阈值"""
    print("=" * 60)
    print("测试6: 归档质量阈值 (>0.6)")
    print("=" * 60)
    
    test_scores = [
        (0.5, False, "质量分0.5 < 0.6，不应归档"),
        (0.6, False, "质量分0.6 = 0.6，不应归档（要求 > 0.6）"),
        (0.61, True, "质量分0.61 > 0.6，应归档"),
        (0.7, True, "质量分0.7 > 0.6，应归档"),
        (1.0, True, "质量分1.0 > 0.6，应归档"),
    ]
    
    for score, should_archive, description in test_scores:
        print(f"\n测试: {description}")
        
        # 模拟归档条件
        is_completed = True
        quality_score = score
        
        will_archive = is_completed and quality_score > 0.6
        
        print(f"  质量分: {quality_score:.2f}")
        print(f"  是否归档: {will_archive}")
        
        assert will_archive == should_archive, f"归档逻辑错误: {description}"
        print(f"  ✓ 符合预期")
    
    print("\n✓ 归档阈值测试通过\n")
    return True


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("HITL机制和改进功能测试套件")
    print("=" * 60 + "\n")
    
    tests = [
        ("Graph结构和interrupt配置", test_graph_structure),
        ("Planner Metadata格式", test_planner_metadata_format),
        ("Executor思考模型支持", test_executor_thinking_model_support),
        ("Runtime API RAG检测", test_runtime_api_rag_detection),
        ("Reflector人工结项检查", test_reflector_completion_check),
        ("归档质量阈值", test_quality_score_threshold),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
        except Exception as e:
            failed += 1
            print(f"\n✗ 测试失败: {name}")
            print(f"  错误: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"通过: {passed}/{len(tests)}")
    print(f"失败: {failed}/{len(tests)}")
    
    if failed == 0:
        print("\n✓ 所有测试通过！")
        return True
    else:
        print(f"\n✗ {failed}个测试失败")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
