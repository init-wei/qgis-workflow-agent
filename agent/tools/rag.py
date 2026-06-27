#!/usr/bin/env python3
"""
RAG检索工具模块
提供GDAL和Cookbook的检索功能，PyQGIS通过MCP协议调用
"""

import os
import json
import asyncio
from typing import List, Dict, Any, Optional
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from dotenv import load_dotenv
import logging
from langchain_core.messages import ToolMessage
from agent.tools.mcp_client import get_mcp_client

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/qgis_db")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)


def search_gdal_docs(method_names: List[str]) -> List[Dict[str, Any]]:
    """
    根据方法名搜索GDAL API文档
    
    Args:
        method_names: GDAL方法名列表 (例如: ['osgeo.gdal.Warp', 'osgeo.ogr.Layer.CreateField'])
        
    Returns:
        匹配的GDAL API文档列表
    """
    if not method_names:
        return []
    
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 使用ANY来匹配列表中的任意一个方法名
                cur.execute(
                    """
                    SELECT api_name, description, params, example_code, doc_type as library
                    FROM rag_docs
                    WHERE api_name = ANY(%s)
                    AND doc_type = 'gdal'
                    ORDER BY api_name
                    """,
                    (method_names,)
                )
                docs = cur.fetchall()
                logger.info(f"搜索GDAL文档: 查询{len(method_names)}个API, 找到{len(docs)}个文档")
                return docs
    except Exception as e:
        logger.error(f"搜索GDAL文档失败: {e}")
        return []


def search_gdal_docs_fuzzy(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    模糊搜索GDAL API文档（使用pg_trgm）
    
    Args:
        query: 搜索关键词
        limit: 返回结果数量限制
        
    Returns:
        匹配的GDAL API文档列表
    """
    if not query:
        return []
    
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 使用pg_trgm的相似度搜索
                cur.execute(
                    """
                    SELECT api_name, description, params, example_code, doc_type as library,
                           similarity(api_name, %s) as sim_score
                    FROM rag_docs
                    WHERE doc_type = 'gdal'
                    AND (api_name ILIKE %s OR description ILIKE %s)
                    ORDER BY similarity(api_name, %s) DESC
                    LIMIT %s
                    """,
                    (query, f'%{query}%', f'%{query}%', query, limit)
                )
                docs = cur.fetchall()
                logger.info(f"模糊搜索GDAL文档: 关键词='{query}', 找到{len(docs)}个文档")
                return docs
    except Exception as e:
        logger.error(f"模糊搜索GDAL文档失败: {e}")
        return []


def search_cookbook(
    user_intent: str,
    similarity_threshold: float = 0.8,  # 提高到0.8以获得更高质量的匹配
    top_k: int = 3
) -> List[Dict[str, Any]]:
    """
    在Cookbook中搜索相似案例
    
    使用BM25+Vector混合检索策略（当前版本使用pg_trgm相似度）
    未来可扩展为真正的混合检索
    
    Args:
        user_intent: 用户意图描述
        similarity_threshold: 相似度阈值 (0.0-1.0)，技术文档建议>0.8
        top_k: 返回top k个结果
        
    Returns:
        相似案例列表（按相似度降序）
    """
    if not user_intent:
        return []
    
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 使用pg_trgm的相似度搜索
                # TODO: 未来可扩展为BM25 + Vector混合检索
                cur.execute(
                    """
                    SELECT id, user_intent, verified_code, tags, 
                           complexity_score, steps, usage_count,
                           similarity(user_intent, %s) as similarity_score
                    FROM cookbook
                    WHERE similarity(user_intent, %s) > %s
                    ORDER BY similarity(user_intent, %s) DESC
                    LIMIT %s
                    """,
                    (user_intent, user_intent, similarity_threshold, user_intent, top_k)
                )
                results = cur.fetchall()
                logger.info(f"搜索Cookbook: 找到{len(results)}个相似案例（阈值>{similarity_threshold}）")
                
                # 如果高阈值没有结果，尝试降低阈值
                if not results and similarity_threshold > 0.3:
                    logger.info(f"高阈值无结果，尝试降低阈值到0.3重新搜索...")
                    cur.execute(
                        """
                        SELECT id, user_intent, verified_code, tags, 
                               complexity_score, steps, usage_count,
                               similarity(user_intent, %s) as similarity_score
                        FROM cookbook
                        WHERE similarity(user_intent, %s) > 0.3
                        ORDER BY similarity(user_intent, %s) DESC
                        LIMIT %s
                        """,
                        (user_intent, user_intent, user_intent, top_k)
                    )
                    results = cur.fetchall()
                    logger.info(f"降低阈值后找到{len(results)}个相似案例")
                
                return results
    except Exception as e:
        logger.error(f"搜索Cookbook失败: {e}")
        return []


def search_pyqgis_docs(method_names: List[str]) -> List[Dict[str, Any]]:
    """
    根据方法名搜索PyQGIS API文档（通过MCP协议调用）
    
    Args:
        method_names: PyQGIS方法名列表 (例如: ['QgsVectorLayer', 'QgsProject', 'QgsVectorLayer.crs'])
        
    Returns:
        匹配的PyQGIS API文档列表
    """
    if not method_names:
        return []
    
    try:
        logger.info(f"通过MCP搜索PyQGIS文档: {method_names}")
        
        # 尝试获取运行中的事件循环
        try:
            loop = asyncio.get_running_loop()
            # 如果有运行中的循环，使用nest_asyncio让我们可以嵌套运行
            import nest_asyncio
            nest_asyncio.apply()
        except RuntimeError:
            # 没有运行中的循环，正常继续
            pass
        
        # 使用asyncio运行异步MCP调用
        return asyncio.run(_search_pyqgis_docs_async(method_names))
    
    except Exception as e:
        logger.error(f"搜索PyQGIS文档失败: {e}")
        import traceback
        traceback.print_exc()
        return []


async def search_pyqgis_docs_async(method_names: List[str]) -> List[Dict[str, Any]]:
    """
    异步搜索PyQGIS文档（通过MCP协议）
    """
    return await _search_pyqgis_docs_async(method_names)


async def _search_pyqgis_docs_async(method_names: List[str]) -> List[Dict[str, Any]]:
    """
    异步搜索PyQGIS文档（通过MCP协议）
    
    Args:
        method_names: PyQGIS方法名列表
        
    Returns:
        匹配的PyQGIS API文档列表
    """
    try:
        mcp_client = await get_mcp_client()
        mcp_result = await mcp_client.call_tool(
            "batch_extract_qgis_methods",
            {"method_names": method_names}
        )

        response_data = _extract_mcp_response_data(mcp_result)
        if not isinstance(response_data, dict):
            logger.error("MCP响应中没有有效内容")
            return []

        extracted_results = response_data.get('results', [])

        logger.info(f"MCP返回 {len(extracted_results)} 个PyQGIS文档")

        # 转换为标准格式
        results = []
        for result in extracted_results:
            # 需要从原始method_names获取对应的api_name
            # 因为batch_extract返回时可能没有明确的method_name字段
            # 我们需要通过class_name或full_method_name推断
            api_name = result.get('class_name') or result.get('full_method_name', '')

            if not api_name:
                logger.warning("无法确定API名称，跳过此结果")
                continue

            # 判断返回的是类还是方法
            if 'class_name' in result:
                # 提取的是类
                # 构建参数说明（从methods列表中提取）
                params_parts = []

                # 基类信息
                if result.get('base_classes'):
                    base_classes = result['base_classes']
                    if isinstance(base_classes, list):
                        params_parts.append(f"基类: {', '.join(base_classes)}")
                    else:
                        params_parts.append(f"基类: {base_classes}")

                # 方法列表概览
                if result.get('methods'):
                    methods_list = result['methods']
                    if isinstance(methods_list, list) and len(methods_list) > 0:
                        params_parts.append(f"\n主要方法 (共{len(methods_list)}个):")
                        # 只显示前10个方法
                        for method in methods_list[:10]:
                            if isinstance(method, dict) and method.get('name'):
                                method_info = f"  - {method['name']}"
                                if method.get('signature'):
                                    method_info += f": {method['signature'][:80]}..."
                                params_parts.append(method_info)
                        if len(methods_list) > 10:
                            params_parts.append(f"  ... 还有 {len(methods_list) - 10} 个方法")

                results.append({
                    "api_name": api_name,
                    "description": result.get('content', '')[:500] + "..." if len(result.get('content', '')) > 500 else result.get('content', ''),
                    "params": "\n".join(params_parts),
                    "example_code": "",
                    "library": "pyqgis"
                })

            elif 'full_method_name' in result:
                # 提取的是方法
                params_parts = []

                if result.get('signature'):
                    params_parts.append(f"方法签名: {result['signature']}")

                # content已经包含了参数、返回值等信息
                if result.get('content'):
                    params_parts.append(result['content'])

                results.append({
                    "api_name": api_name,
                    "description": f"方法: {result.get('full_method_name', api_name)}",
                    "params": "\n\n".join(params_parts),
                    "example_code": "",
                    "library": "pyqgis"
                })
            else:
                logger.warning("未知的返回格式")

        return results
    
    except Exception as e:
        logger.error(f"MCP搜索PyQGIS文档失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def _extract_mcp_response_data(mcp_result: Any) -> Optional[Dict[str, Any]]:
    """
    解析MCP工具返回结果为字典（兼容文本/内容块/ToolMessage/结构化内容）
    """
    if isinstance(mcp_result, ToolMessage):
        artifact = getattr(mcp_result, "artifact", None)
        if isinstance(artifact, dict):
            structured = artifact.get("structured_content")
            if isinstance(structured, dict):
                return structured
        return _parse_content_blocks(getattr(mcp_result, "content", None))

    if isinstance(mcp_result, dict):
        structured = mcp_result.get("structured_content") or mcp_result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        if "content" in mcp_result:
            return _parse_content_blocks(mcp_result.get("content"))
        return mcp_result

    if isinstance(mcp_result, list):
        return _parse_content_blocks(mcp_result)

    if isinstance(mcp_result, str):
        try:
            return json.loads(mcp_result)
        except json.JSONDecodeError:
            logger.error("MCP返回文本无法解析为JSON")
            return None

    logger.error("MCP调用返回格式错误")
    return None


def _parse_content_blocks(content: Any) -> Optional[Dict[str, Any]]:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.error("MCP内容文本无法解析为JSON")
            return None

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                try:
                    return json.loads(item["text"])
                except json.JSONDecodeError:
                    logger.error("MCP内容块文本无法解析为JSON")
                    return None
            if isinstance(item, str):
                try:
                    return json.loads(item)
                except json.JSONDecodeError:
                    logger.error("MCP内容块文本无法解析为JSON")
                    return None

    return None


def search_all_apis_by_keywords(keywords: List[str], limit: int = 20) -> List[Dict[str, Any]]:
    """
    根据关键词搜索所有API文档（GDAL + PyQGIS）
    
    Args:
        keywords: 关键词列表
        limit: 返回结果数量限制
        
    Returns:
        匹配的API文档列表
    """
    if not keywords:
        return []
    
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 构建OR查询条件
                keyword_conditions = " OR ".join(
                    ["api_name ILIKE %s OR description ILIKE %s"] * len(keywords)
                )
                # 扁平化参数列表
                params = []
                for kw in keywords:
                    params.extend([f'%{kw}%', f'%{kw}%'])
                params.append(limit)
                
                cur.execute(
                    f"""
                    SELECT api_name, description, params, example_code, doc_type as library
                    FROM rag_docs
                    WHERE {keyword_conditions}
                    ORDER BY api_name
                    LIMIT %s
                    """,
                    params
                )
                docs = cur.fetchall()
                logger.info(f"关键词搜索API文档: 找到{len(docs)}个文档")
                return docs
    except Exception as e:
        logger.error(f"关键词搜索API文档失败: {e}")
        return []
