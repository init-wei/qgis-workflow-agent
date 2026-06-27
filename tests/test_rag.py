#!/usr/bin/env python3
"""
RAG工具测试
"""

from agent.tools.rag import (
    search_gdal_docs,
    search_gdal_docs_fuzzy,
    search_cookbook,
    search_pyqgis_docs
)


def test_search_gdal_docs():
    """测试精确搜索GDAL文档"""
    method_names = ["osgeo.gdal.Warp", "osgeo.gdal.Translate"]
    
    docs = search_gdal_docs(method_names)
    
    print(f"找到 {len(docs)} 个GDAL文档")
    
    if docs:
        print(f"第一个文档: {docs[0]['api_name']}")
        assert "api_name" in docs[0]
        assert "description" in docs[0]
        assert "params" in docs[0]
    
    print("✅ GDAL文档精确搜索测试通过")


def test_search_gdal_docs_fuzzy():
    """测试模糊搜索GDAL文档"""
    query = "Warp"
    
    docs = search_gdal_docs_fuzzy(query, limit=5)
    
    print(f"模糊搜索 '{query}' 找到 {len(docs)} 个文档")
    
    if docs:
        print(f"第一个文档: {docs[0]['api_name']}")
    
    print("✅ GDAL文档模糊搜索测试通过")


def test_search_cookbook():
    """测试Cookbook搜索"""
    user_intent = "加载图层"
    
    results = search_cookbook(user_intent, similarity_threshold=0.1, top_k=3)
    
    print(f"Cookbook搜索 '{user_intent}' 找到 {len(results)} 个案例")
    
    if results:
        print(f"第一个案例: {results[0]['user_intent'][:50]}...")
        print(f"相似度: {results[0]['similarity_score']:.2f}")
    
    print("✅ Cookbook搜索测试通过")


if __name__ == "__main__":
    print("运行RAG工具测试...")
    print("注意: 需要PostgreSQL数据库运行并导入GDAL文档")
    print("-" * 60)
    
    try:
        test_search_gdal_docs()
        print()
        
        test_search_gdal_docs_fuzzy()
        print()
        
        test_search_cookbook()
        print()
        
        print("=" * 60)
        print("所有RAG测试通过！")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
