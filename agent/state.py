#!/usr/bin/env python3
"""
LangGraph State定义
包含所有节点共享的状态字段
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated
import operator
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
import uuid


class Step(BaseModel):
    """执行步骤"""
    
    step_id: int = Field(description="步骤ID")
    description: str = Field(description="步骤描述")
    gdal_api: List[str] = Field(default_factory=list, description="涉及的GDAL API")
    pyqgis_api: List[str] = Field(default_factory=list, description="涉及的PyQGIS API")


class Plan(BaseModel):
    """执行计划"""
    
    task: str = Field(description="任务简述")
    steps: List[Step] = Field(default_factory=list, description="执行步骤列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


class RelevantDoc(BaseModel):
    """相关API文档"""
    
    api_name: str = Field(description="API名称")
    library: str = Field(description="库类型: GDAL or PyQGIS")
    content: str = Field(description="API文档完整内容")


class StepContext(BaseModel):
    """步骤上下文（按步骤分组的API文档）"""
    
    step_id: int = Field(description="步骤ID")
    relevant_docs: List[RelevantDoc] = Field(default_factory=list, description="相关文档列表")
    supplemental_docs: List[RelevantDoc] = Field(default_factory=list, description="补充的辅助API文档（如WarpOptions）")


class AgentState(TypedDict):
    """
    LangGraph State定义
    所有节点共享的状态字段
    """
    
    # ========== 基础输入 ==========
    input_query: str  # 用户的原始地理处理需求
    session_id: str   # 会话ID（用于数据隔离）
    llm_config: Optional[Dict[str, Any]]  # LLM配置（可由前端覆盖）
    
    # ========== Planner Node 字段 ==========
    log_summary: Optional[str]        # 之前轮次的任务执行总结
    advise: Optional[str]             # 用户在审核阶段提出的修改意见
    example: Optional[Dict[str, Any]] # 从Cookbook检索到的相似案例
    draft: Optional[Plan]             # 模型当前生成的草稿方案
    plan: Optional[Plan]              # 最终确认执行的方案
    status: bool                      # 方案审核状态（False=待审核，True=已批准）
    retry_count: int                  # 人工审查/修改的循环次数
    
    # ========== API RAG Node 字段 ==========
    gdal_doc: List[Dict[str, Any]]   # 检索到的GDAL API文档
    pyqgis_doc: List[Dict[str, Any]] # 检索到的PyQGIS API文档
    api_context_structured: List[StepContext]  # 按步骤分段的结构化API文档
    missing_deps: List[str]           # LLM反思发现的缺失依赖API
    
    # ========== Executor Node 字段 ==========
    messages: Annotated[list, add_messages]  # 包含AI思考、代码输出及执行结果
    screenshot_path: Optional[str]    # 最新生成的截图文件路径

    
    # ========== Reflector Node 字段 ==========
    final_summary: Optional[str]      # 任务完成后的总结
    log_summary: Optional[str]        # 之前轮次的任务执行总结
    screenshot_path: Optional[str]    # 最新生成的截图文件路径
    is_completed: bool                # 用户人工认定的任务成功完成状态
    quality_score: float              # LLM对本次任务的价值评分
    user_feedback: Optional[str]      # 用户对任务失败的反馈意见

    # ========== History 字段 ==========
    history: Annotated[List[Dict[str, Any]], operator.add]  # 任务历史记录（追加式更新）


# State初始化函数
def create_initial_state(session_id: str, input_query: str, llm_config: Optional[Dict[str, Any]] = None) -> AgentState:
    """
    创建初始状态
    
    Args:
        session_id: 会话ID
        input_query: 用户输入的查询
        
    Returns:
        初始化的AgentState
    """
    return AgentState(
        # 基础输入
        input_query=input_query,
        session_id=session_id if session_id else str(uuid.uuid4()),
        
        # LLM配置
        llm_config=llm_config,

        # Planner字段
        log_summary=None,
        advise=None,
        example=None,
        draft=None,
        plan=None,
        status=False,
        retry_count=0,
        
        # API RAG字段
        gdal_doc=[],
        pyqgis_doc=[],
        api_context_structured=[],
        missing_deps=[],
        
        # Executor字段
        messages=[],
        screenshot_path=None,

        
        # Reflector字段
        final_summary=None,
        is_completed=None,
        quality_score=0.0,

        # History字段
        history=[],
    )
