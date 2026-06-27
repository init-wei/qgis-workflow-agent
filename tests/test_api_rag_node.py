#!/usr/bin/env python3
"""
API RAG Node测试
"""

# 添加项目根目录到sys.path（必须在导入agent之前）
import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


from agent.state import create_initial_state, Plan, Step
from agent.nodes.api_rag_node import api_rag_node





def test_api_rag_node():
    """测试API RAG Node"""
    print("测试API RAG Node...")
    print("-" * 60)
    
    # 创建测试状态
    state = create_initial_state(
        session_id="test_api_rag_001",
        input_query="使用GDAL进行影像重投影"
    )
    
    # 创建测试计划（使用完整的API名称格式，包含GDAL和PyQGIS）
    plan = Plan(
        task="影像重投影和图层加载",
        steps=[
            Step(
                step_id=1,
                description="打开影像文件",
                gdal_api=["osgeo.ogr.Open"],  # GDAL完整格式
                pyqgis_api=[]
            ),
            Step(
                step_id=2,
                description="执行重投影",
                gdal_api=["osgeo.gdal.Warp"],  # GDAL完整格式
                pyqgis_api=[]
            ),
            Step(
                step_id=3,
                description="加载矢量图层到QGIS",
                gdal_api=[],
                pyqgis_api=["QgsVectorLayer", "QgsProject","QgsGridFileWriter.setNoDataValue"]  # PyQGIS只用类名或者类+方法
            )
        ],
        metadata={}
    )
    
    state["plan"] = plan
    state["status"] = True
    
    # 调用API RAG Node
    result = api_rag_node(state)
    
    print("\nAPI RAG结果:")
    print(f"- GDAL文档数: {len(result.get('gdal_doc', []))}")
    print(f"- PyQGIS文档数: {len(result.get('pyqgis_doc', []))}")
    print(f"- 结构化上下文数: {len(result.get('api_context_structured', []))}")
    print(f"- 缺失依赖: {result.get('missing_deps', [])}")
    
    # 打印所有GDAL文档
    print("\n" + "=" * 60)
    print("所有搜索到的GDAL文档:")
    print("=" * 60)
    for i, doc in enumerate(result.get('gdal_doc', []), 1):
        print(f"\n{i}. API名称: {doc['api_name']}")
        print(f"   库: {doc.get('library', 'GDAL')}")
        print(f"   描述: {doc.get('description', 'N/A')[:100]}...")
        if doc.get('params'):
            print(f"   参数预览: {doc['params'][:80]}...")
    
    # 打印所有PyQGIS文档
    if result.get('pyqgis_doc'):
        print("\n" + "=" * 60)
        print("所有搜索到的PyQGIS文档:")
        print("=" * 60)
        for i, doc in enumerate(result.get('pyqgis_doc', []), 1):
            print(f"\n{i}. API名称: {doc['api_name']}")
            print(f"   库: {doc.get('library', 'PyQGIS')}")
            print(f"   内容: {doc.get('content', 'N/A')[:100]}...")
    
    # 打印步骤分组详情
    print("\n" + "=" * 60)
    print("步骤分组详情:")
    print("=" * 60)
    for ctx in result.get('api_context_structured', []):
        print(f"\n步骤 {ctx.step_id}:")
        print(f"  文档数: {len(ctx.relevant_docs)}")
        for doc in ctx.relevant_docs:
            print(f"  - {doc.api_name} ({doc.library})")

    print("补充api",result.get('api_context_structured')[0].supplemental_docs)
    print("\n✅ API RAG Node测试通过")


if __name__ == "__main__":
    print("运行API RAG Node测试...")
    print("注意: 需要PostgreSQL数据库运行并导入GDAL文档")
    print("=" * 60)
    
    try:
        test_api_rag_node()
        
        print("\n" + "=" * 60)
        print("API RAG Node测试完成！")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
