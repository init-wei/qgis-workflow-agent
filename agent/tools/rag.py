#!/usr/bin/env python3
"""
RAG检索工具模块
提供GDAL文档检索功能，PyQGIS通过MCP协议调用
"""

import json
import asyncio
from typing import List, Dict, Any, Optional
from psycopg.rows import dict_row
import logging
from langchain_core.messages import ToolMessage
from agent.tools.database import _get_pool
from agent.tools.mcp_client import get_mcp_client

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    
    pool = _get_pool()
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
        raise


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
    
    pool = _get_pool()
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
        raise


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
        raise


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
    if not method_names:
        return []

    try:
        mcp_client = await get_mcp_client()
        mcp_result = await mcp_client.call_tool(
            "batch_extract_qgis_methods",
            {"method_names": method_names}
        )

        _raise_on_mcp_error(mcp_result)
        response_data = _extract_mcp_response_data(mcp_result)
        if not isinstance(response_data, dict):
            raise ValueError("MCP响应中没有有效内容")
        _raise_on_mcp_error(response_data)

        extracted_results = response_data.get('results')
        if not isinstance(extracted_results, list):
            raise ValueError("MCP响应中的 results 必须是列表")

        logger.info(f"MCP返回 {len(extracted_results)} 个PyQGIS文档")

        # 转换为标准格式
        results = []
        for result in extracted_results:
            if not isinstance(result, dict):
                raise ValueError("MCP返回的文档必须是对象")
            # 需要从原始method_names获取对应的api_name
            # 因为batch_extract返回时可能没有明确的method_name字段
            # 我们需要通过class_name或full_method_name推断
            class_name = result.get('class_name')
            method_name = result.get('full_method_name')
            if isinstance(class_name, str) and class_name.strip():
                api_name = class_name
            elif isinstance(method_name, str) and method_name.strip():
                api_name = method_name
            else:
                raise ValueError("MCP返回的文档缺少有效API名称")

            # 判断返回的是类还是方法
            if api_name == class_name:
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

            else:
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
        return results
    
    except Exception as e:
        logger.error(f"MCP搜索PyQGIS文档失败: {e}")
        raise


def _raise_on_mcp_error(response: Any) -> None:
    """Reject explicit tool errors, even if their content resembles a result."""
    if isinstance(response, ToolMessage) and response.status == "error":
        raise RuntimeError("MCP工具返回错误")
    if isinstance(response, dict):
        for key in ("isError", "is_error"):
            value = response.get(key)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"MCP响应中的 {key} 必须是布尔值")
    if isinstance(response, dict) and (
        response.get("isError") is True
        or response.get("is_error") is True
        or response.get("status") in ("error", "failed")
        or response.get("error") is not None
    ):
        raise RuntimeError("MCP工具返回错误")


def _extract_mcp_response_data(mcp_result: Any) -> Optional[Dict[str, Any]]:
    """
    解析MCP工具返回结果为字典（兼容文本/内容块/ToolMessage/结构化内容）
    """
    if isinstance(mcp_result, ToolMessage):
        artifact = getattr(mcp_result, "artifact", None)
        if isinstance(artifact, dict):
            _raise_on_mcp_error(artifact)
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
    
    pool = _get_pool()
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
        raise
