#!/usr/bin/env python3
"""
API RAG Node - API RAG检索节点
负责GDAL/PyQGIS API文档检索与依赖补充
"""

from typing import Dict, Any, List
import logging
import json
from dotenv import load_dotenv

from agent.llm import create_llm
from langchain_core.messages import SystemMessage, HumanMessage

from agent.state import AgentState, RelevantDoc, StepContext

from agent.tools.database import save_execution_log

import asyncio
from agent.tools.mcp_client import get_mcp_client


load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _get_llm(state: AgentState):
    return create_llm(state.get("llm_config"))



async def api_rag_node(state: AgentState) -> Dict[str, Any]:
    """
    API RAG检索节点 - 检索并结构化API文档
    
    工作流程:
    1. 从plan中提取所有GDAL/PyQGIS API名称
    2. 在数据库中检索对应的API文档
    3. 检查是否有缺失的依赖API
    4. 按步骤分组API文档
    5. 返回结构化的API上下文
    
    Args:
        state: 当前Agent状态
        
    Returns:
        更新后的状态字段
    """
    logger.info("=" * 60)
    logger.info("API RAG Node: 开始检索API文档")
    logger.info("=" * 60)
    
    session_id = state["session_id"]
    llm = _get_llm(state)
    plan = state.get("plan")
    
    if not plan:
        logger.error("未找到执行计划")
        return {
            "gdal_doc": [],
            "pyqgis_doc": [],
            "api_context_structured": [],
            "missing_deps": [],
        }
    
    # 保存日志
    await asyncio.to_thread(
        save_execution_log,
        session_id,
        None,
        "开始检索API文档",
        "info"
    )
    
    # 1. 提取所有API名称
    all_gdal_apis = []
    all_pyqgis_apis = []
    
    for step in plan.steps:
        all_gdal_apis.extend(step.gdal_api)
        all_pyqgis_apis.extend(step.pyqgis_api)
    
    # 去重
    all_gdal_apis = list(set(all_gdal_apis))
    all_pyqgis_apis = list(set(all_pyqgis_apis))
    
    logger.info(f"需要检索的API: GDAL={len(all_gdal_apis)}, PyQGIS={len(all_pyqgis_apis)}")
    
    gdal_docs = []
    pyqgis_docs = []
    found_gdal_apis = set()
    found_pyqgis_apis = set()

    client = await get_mcp_client()
    
    if all_gdal_apis:
        # 先尝试精确搜索
        raw_result = await client.call_tool("search_gdal_api", {"api_names": all_gdal_apis})
        
        # 解析 MCP 返回结果
        if isinstance(raw_result, list) and len(raw_result) > 0:
            # 提取第一个消息的 text 字段
            result_text = raw_result[0].get('text', '{}')
            result_data = json.loads(result_text)
            gdal_docs = result_data.get('results', [])
        else:
            gdal_docs = []
        
        logger.info(f"精确搜索找到GDAL文档: {len(gdal_docs)}个")
        for doc in gdal_docs:
            found_gdal_apis.add(doc["api_name"])
        
    if all_pyqgis_apis:
        raw_result = await client.call_tool("search_pyqgis_api", {"api_names": all_pyqgis_apis})
        
        # 解析 MCP 返回结果
        if isinstance(raw_result, list) and len(raw_result) > 0:
            result_text = raw_result[0].get('text', '{}')
            result_data = json.loads(result_text)
            pyqgis_docs = result_data.get('results', [])
        else:
            pyqgis_docs = []
            
        logger.info(f"找到PyQGIS文档: {len(pyqgis_docs)}个")
        for doc in pyqgis_docs:
            found_pyqgis_apis.add(doc["api_name"])
    
    # 3. 检查缺失的API
    missing_gdal = set(all_gdal_apis) - found_gdal_apis
    missing_pyqgis = set(all_pyqgis_apis) - found_pyqgis_apis
    missing_deps = list(missing_gdal) + list(missing_pyqgis)
    
    if missing_deps:
        logger.warning(f"缺失的API文档: {missing_deps}")
        await asyncio.to_thread(
            save_execution_log,
            session_id,
            None,
            f"缺失API文档: {', '.join(missing_deps)}",
            "warning"
        )
    
    # 4. LLM依赖反思与二次补充（仅对GDAL进行）
    # 将初步检索到的GDAL文档喂给LLM，判断是否还需要其他辅助类
    additional_deps = []
    supplemental_docs = []
    
    if gdal_docs and len(gdal_docs) > 0:
        logger.info("执行LLM依赖分析...")
        
        # 构建API摘要
        api_summary = "\n".join([
            f"- {doc['api_name']}: {doc.get('description', 'N/A')[:100]}..."
            for doc in gdal_docs[:3]  # 只分析前5个，避免token过多
        ])
        
        reflection_prompt = f"""分析以下GDAL API，判断是否还需要其他辅助类或配置类才能正常使用。

已检索到的API:
{api_summary}

常见的辅助类包括:
- osgeo.gdal.WarpOptions (用于osgeo.gdal.Warp)
- osgeo.gdal.TranslateOptions (用于osgeo.gdal.Translate)
- osgeo.gdal.DEMProcessingOptions (用于osgeo.gdal.DEMProcessing)
- osgeo.gdal.GridOptions (用于osgeo.gdal.Grid)

重要：请使用完整的API名称格式（包含osgeo.前缀）。

请仅返回缺失的API名称列表，格式为JSON数组。如果不需要补充，返回空数组[]。

示例输出: ["osgeo.gdal.WarpOptions", "osgeo.gdal.TranslateOptions"]"""

        try:
            response = await llm.ainvoke([HumanMessage(content=reflection_prompt)])
            response_text = response.content.strip()
            
            # 尝试解析JSON
            if "[" in response_text and "]" in response_text:
                start = response_text.find("[")
                end = response_text.rfind("]") + 1
                json_str = response_text[start:end]
                additional_deps = json.loads(json_str)
                
                if additional_deps:
                    logger.info(f"LLM识别出需要补充的依赖: {additional_deps}")
                    
                    # 使用精确搜索查找补充依赖
                    supplemental_docs = await client.call_tool("search_gdal_api", {"method_names": additional_deps})
                    
                    if supplemental_docs:
                        logger.info(f"补充检索到 {len(supplemental_docs)} 个依赖文档")
                        # 将补充文档也添加到gdal_docs中（用于后续可能的使用）
                        gdal_docs.extend(supplemental_docs)
                else:
                    logger.info("LLM判断无需补充依赖")
        except Exception as e:
            logger.warning(f"LLM依赖分析失败: {e}")
    
    # 5. 按步骤分组API文档
    api_context_structured = []
    
    # 准备补充文档列表（用于所有步骤共享）
    supplemental_relevant_docs = []
    if additional_deps:
        logger.info(f"LLM识别出需要补充的依赖: {additional_deps}")
        
        # 使用精确搜索查找补充依赖
        raw_result = await client.call_tool("search_gdal_api", {"api_names": additional_deps})
        
        # 解析结果
        if isinstance(raw_result, list) and len(raw_result) > 0:
            result_text = raw_result[0].get('text', '{}')
            result_data = json.loads(result_text)
            supplemental_docs = result_data.get('results', [])
        else:
            supplemental_docs = []

    if supplemental_docs:
        for doc in supplemental_docs:
            content_parts = [
                f"API名称: {doc['api_name']}",
                f"描述: {doc['description']}",
                f"参数:\n{doc['params']}",
            ]
            if doc.get('example_code'):
                content_parts.append(f"示例代码:\n```python\n{doc['example_code']}\n```")
            
            supplemental_relevant_docs.append(RelevantDoc(
                api_name=doc["api_name"],
                library="GDAL",
                content="\n\n".join(content_parts)
            ))
        logger.info(f"准备 {len(supplemental_relevant_docs)} 个补充文档，将添加到所有步骤")
    
    for step in plan.steps:
        step_docs = []
        
        # 添加该步骤的GDAL文档
        # mcp返回格式：
        """
         {"api_name" ,"description" , "params", "example_code", "library":}
        """
        for api_name in step.gdal_api:
            # 精确匹配（Planner现在输出完整格式，应该能直接匹配）
            matching_docs = [doc for doc in gdal_docs if doc["api_name"] == api_name]
            for doc in matching_docs:
                # 构建完整的文档内容
                content_parts = [
                    f"API名称: {doc['api_name']}",
                    f"描述: {doc['description']}",
                    f"参数:\n{doc['params']}",
                ]
                if doc.get('example_code'):
                    content_parts.append(f"示例代码:\n```python\n{doc['example_code']}\n```")
                
                step_docs.append(RelevantDoc(
                    api_name=doc["api_name"],
                    library="GDAL",
                    content="\n\n".join(content_parts)
                ))
        
        # 添加该步骤的PyQGIS文档
        # mcp返回格式：
        """
         {"api_name" ,"content" , "source_url", "base_class(optional)", "signature", "methods(optional)"}
         class api和method api返回格式不一样，只有api_name和content是共通的
        """
        for api_name in step.pyqgis_api:
            # 精确匹配
            matching_docs = [doc for doc in pyqgis_docs if doc["api_name"] == api_name]
            for doc in matching_docs:
                content_parts = [
                    f"API名称: {doc['api_name']}",
                    f"内容: {doc['content']}",
                ]
                if doc.get('example_code'):
                    content_parts.append(f"示例代码:\n```python\n{doc['example_code']}\n```")
                
                step_docs.append(RelevantDoc(
                    api_name=doc["api_name"],
                    library="PyQGIS",
                    content="\n\n".join(content_parts)
                ))
        
        api_context_structured.append(StepContext(
            step_id=step.step_id,
            relevant_docs=step_docs,
            supplemental_docs=supplemental_relevant_docs  # 所有步骤共享补充文档
        ))
        
        logger.info(f"步骤{step.step_id}: 检索到{len(step_docs)}个API文档")
    
    # 5. 保存日志
    await asyncio.to_thread(
        save_execution_log,
        session_id,
        None,
        f"API文档检索完成: GDAL={len(gdal_docs)}, PyQGIS={len(pyqgis_docs)}",
        "success"
    )
    
    # 返回更新的状态
    return {
        "gdal_doc": gdal_docs,
        "pyqgis_doc": pyqgis_docs,
        "api_context_structured": api_context_structured,
        "missing_deps": missing_deps,
    }
