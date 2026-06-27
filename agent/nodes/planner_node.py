#!/usr/bin/env python3
"""
Planner Node - 规划器节点
负责意图识别、Cookbook检索、生成执行计划
"""

from typing import Dict, Any
import asyncio
import logging
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from jinja2 import Template

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm import create_llm

from agent.state import AgentState, Plan, Step
from agent.tools.mcp_client import get_mcp_client
from agent.tools.rag import _extract_mcp_response_data
from agent.tools.database import save_execution_log

load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 定义结构化输出模型
class PlannerOutput(BaseModel):
    """Planner节点的结构化输出"""
    
    task: str = Field(description="任务简述（例如: Align raster and vector layers）")
    steps: list[Step] = Field(description="执行步骤列表")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="元数据，包含iteration和has_example_reference等信息"
    )

def _get_llm(state: AgentState):
    return create_llm(state.get("llm_config")).bind(response_format={"type": "json_object"})

# 定义Query Rewriting模板
QUERY_REWRITE_TEMPLATE = Template("""请将以下信息综合成一个清晰的GIS任务描述，用于检索相似案例。

原始需求: {{ input_query }}
{% if log_summary %}

上轮执行总结: {{ log_summary }}
{% endif %}
{% if advise %}

用户修改意见: {{ advise }}
{% endif %}

请用一句话概括核心任务，包含关键的GIS操作类型（如裁剪、重投影、缓冲区等）。
""")

# 定义System Prompt模板
SYSTEM_PROMPT_TEMPLATE = Template("""你是一个专业的QGIS地理数据处理专家。你的任务是将用户的自然语言需求转化为可执行的QGIS操作步骤。

## 输出格式
请严格按照以下JSON格式输出：

{
  "task": "任务简述（例如: Align raster and vector layers）",
  "steps": [
    {
      "step_id": 1,
      "description": "步骤描述（例如: Load raster layer from file）",
      "gdal_api": ["可能用到的GDAL API名称，如果没有则留空"],
      "pyqgis_api": ["可能用到的PyQGIS API名称，如果没有则留空"]
    }
  ],
  "metadata": {}
}

## API使用与命名规范（重要）
请务必使用**完整的API名称格式**：

GDAL API往往用于数据处理，PyQGIS API往往用于QGIS内部的图层以及可视化操作


## 注意事项
1. 如果有用户修改意见，请结合意见进行调整
2. 如果有上一轮的执行总结，先了解之前内容是否成功执行，若成功则无需重复步骤，如果失败则在本轮中进行修正
3. 步骤不要拆分太细，每个步骤应该是中低复杂度的目标，且涉及文件必须包含路径信息
4. 文件的导入与导出不要使用GDAL或者PYQGIS的代码，只需在步骤中api留空
5. gdal_api和pyqgis_api列出所有可能用到的API名称，尽量精确到方法级别（不需要参数细节），如果该步骤不需要使用某类API，可以留空
6. 参考相似案例可以提高准确性
7. 必须返回有效的JSON格式，不要添加任何额外的解释文字
""")

# 定义User Message模板
USER_MESSAGE_TEMPLATE = Template("""用户需求: {{ input_query }}
{% if log_summary %}

上一轮执行总结:
{{ log_summary }}
{% endif %}
{% if advise %}

用户修改意见:
{{ advise }}
{% endif %}
{% if examples_list %}

相似案例参考（按相似度排序）:
{% for case in examples_list %}
案例 {{ loop.index }} (相似度: {{ "%.2f"|format(case.similarity_score) }}):
- 原始需求: {{ case.user_intent }}
- 参考代码:
```python
{{ case.verified_code }}
```
{% endfor %}
{% endif %}
""")


async def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    规划器节点 - 生成执行计划
    
    工作流程:
    1. 如果plan已存在且status=True，直接返回（已批准，不再规划）
    2. 查询重写（综合input_query、log_summary、advise）
    3. 如果是首次规划，检索Cookbook中的相似案例
    4. 调用LLM生成执行计划
    5. 返回draft计划，等待人工审核
    6. 强制退出机制（retry_count >= 3时强制执行）
    
    Args:
        state: 当前Agent状态
        
    Returns:
        更新后的状态字段
    """
    logger.info("=" * 60)
    logger.info("Planner Node: 开始生成执行计划")
    logger.info("=" * 60)
    
    session_id = state["session_id"]
    llm = _get_llm(state)
    input_query = state["input_query"]
    advise = state.get("advise")
    log_summary = state.get("log_summary")
    retry_count = state.get("retry_count", 0)
    plan = state.get("plan")
    draft = state.get("draft")
    status = state.get("status", False)
    
    # 情况1: 如果计划已批准且plan已存在，直接返回（不再重新规划）
    if plan and status:
        logger.info("计划已批准且plan已存在，跳过规划步骤")
        return {
            "plan": plan,
            "status": True
        }
    
    # 情况2: 如果已批准但plan不存在（只有draft），将draft提升为plan
    if status and draft and not plan:
        logger.info("将已批准的draft提升为plan")
        return {
            "draft": draft,
            "plan": draft,  # 提升为plan
            "status": True
        }
    
    # 保存日志
    await asyncio.to_thread(
        save_execution_log,
        session_id,
        None,
        f"开始规划任务: {input_query}",
        "info"
    )
    
    # 强制退出机制：如果重试次数过多，强制执行
    force_execute = retry_count >= 3
    if force_execute:
        logger.warning(f"已达到修改上限（retry_count={retry_count}），将强制执行当前方案")
    
    # 1. 查询重写（Query Rewriting）
    # 综合input_query、log_summary和advise，生成用于检索Cookbook的关键词
    search_query = input_query
    
    if advise or log_summary:
        logger.info("执行查询重写...")
        rewrite_prompt = QUERY_REWRITE_TEMPLATE.render(
            input_query=input_query,
            log_summary=log_summary,
            advise=advise
        )

        try:
            response = await llm.ainvoke([HumanMessage(content=rewrite_prompt)])
            search_query = response.content.strip()
            logger.info(f"重写后的查询: {search_query}")
        except Exception as e:
            logger.warning(f"查询重写失败，使用原始查询: {e}")
            search_query = input_query
    
    # 2. 检索相似案例（仅在首次规划时）
    example = None
    examples_list = []  # 存储Top-3案例
    has_example_reference = False
    
    if retry_count == 0 and not advise:
        logger.info("通过MCP检索Cookbook相似案例...")
        try:
            mcp_client = await get_mcp_client()
            mcp_result = await mcp_client.call_tool(
                "search_qgis_cookbook",
                {
                    "user_intent": search_query,
                    "similarity_threshold": 0.3,
                    "top_k": 3
                }
            )
            response_data = _extract_mcp_response_data(mcp_result) or {}
            cookbook_results = response_data.get("results", [])
        except Exception as e:
            logger.warning(f"MCP检索Cookbook失败: {e}")
            cookbook_results = []

        if cookbook_results:
            # 使用Top-3案例
            examples_list = cookbook_results
            example = cookbook_results[0]  # 主要参考最相似的案例（保留此字段用于State兼容）
            has_example_reference = True
            logger.info(f"找到{len(cookbook_results)}个相似案例，Top-1: {example['user_intent'][:50]}... (相似度: {example.get('similarity_score', 0.0):.2f})")
        else:
            logger.info("未找到相似案例")
    
    # 3. 构建Prompt（使用Jinja2模板）
    system_prompt = SYSTEM_PROMPT_TEMPLATE.render()
    
    user_message = USER_MESSAGE_TEMPLATE.render(
        input_query=input_query,
        log_summary=log_summary,
        advise=advise,
        examples_list=examples_list[:3] if examples_list else []
    )
    
    # 4. 调用LLM生成计划（使用JSON模式）
    logger.info("调用LLM生成执行计划（JSON模式）...")
    
    try:
        import json
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message)
        ]
        
        # 调用LLM获取JSON响应
        response = await llm.ainvoke(messages)
        response_text = response.content
        
        logger.info(f"LLM响应: {response_text[:200]}...")
        
        # 提取JSON内容
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            json_str = response_text[json_start:json_end].strip()
        elif "```" in response_text:
            json_start = response_text.find("```") + 3
            json_end = response_text.find("```", json_start)
            json_str = response_text[json_start:json_end].strip()
        else:
            json_str = response_text.strip()
        
        # 解析JSON
        plan_dict = json.loads(json_str)
        
        # 转换为PlannerOutput对象（用于验证）
        planner_output = PlannerOutput(
            task=plan_dict["task"],
            steps=[Step(**step) for step in plan_dict["steps"]],
            metadata=plan_dict.get("metadata", {})
        )
        logger.info(f"JSON解析成功: {planner_output.task}")
        
        # 确保metadata包含必需字段
        metadata = planner_output.metadata.copy()
        metadata["iteration"] = retry_count + 1
        metadata["has_example_reference"] = has_example_reference
        
        # 转换为Plan对象
        plan = Plan(
            task=planner_output.task,
            steps=planner_output.steps,
            metadata=metadata
        )
        
        logger.info(f"生成计划成功: {plan.task}, 共{len(plan.steps)}个步骤")
        
        # 保存日志
        await asyncio.to_thread(
            save_execution_log,
            session_id,
            None,
            f"生成执行计划: {plan.task}",
            "success"
        )
        
        # 强制执行逻辑：如果已达到修改上限，直接将draft转为plan并标记为已批准
        if force_execute:
            logger.warning("强制执行模式：跳过人工审核，直接执行计划")
            return {
                "draft": plan,
                "plan": plan,  # 直接设置为最终计划
                "example": example,
                "retry_count": retry_count + 1,
                "status": True,  # 标记为已批准
            }
        
        # 正常流程：返回draft等待人工审核
        return {
            "draft": plan,
            "example": example,
            "retry_count": retry_count + 1,
            "status": False,  # 等待人工审核
        }
        
    except Exception as e:
        logger.error(f"生成计划失败: {e}")
        await asyncio.to_thread(
            save_execution_log,
            session_id,
            None,
            f"生成计划失败: {str(e)}",
            "error",
            error_detail=str(e)
        )
        
        # 返回错误状态
        return {
            "draft": None,
            "status": False,
            "retry_count": retry_count + 1,
        }
