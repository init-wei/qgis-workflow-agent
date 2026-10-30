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

from agent.planning.plan_schema import PlanValidationReport, TaskPlan
from agent.tools.tool_contracts import ExecutionTrace, HumanFeedback


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
    session_name: Optional[str]
    session_created_at: Optional[str]
    task_id: str
    uploaded_files: List[Dict[str, Any]]
    llm_config: Optional[Dict[str, Any]]  # 非敏感运行选项；密钥与入口仅由服务端环境决定
    
    # ========== Planner Node 字段 ==========
    log_summary: Optional[str]        # 之前轮次的任务执行总结
    advise: Optional[str]             # 用户在审核阶段提出的修改意见
    task_plan: Optional[TaskPlan]        # 唯一权威计划及当前审核版本
    plan_validation: Optional[PlanValidationReport]  # TaskPlan验证报告
    planning_error: Optional[str]
    planning_revision: int            # 修订目标；生成失败时仍保留身份
    last_plan_decision: Optional[Dict[str, Any]]
    last_result_decision: Optional[Dict[str, Any]]
    last_risk_decision: Optional[Dict[str, Any]]
    retry_count: int                  # 人工审查/修改的循环次数
    
    # ========== API RAG Node 字段 ==========
    gdal_doc: List[Dict[str, Any]]   # 检索到的GDAL API文档
    pyqgis_doc: List[Dict[str, Any]] # 检索到的PyQGIS API文档
    api_context_structured: List[StepContext]  # 按步骤分段的结构化API文档
    missing_deps: List[str]           # LLM反思发现的缺失依赖API
    
    # ========== Executor Node 字段 ==========
    messages: Annotated[list, add_messages]  # 包含AI思考、代码输出及执行结果
    screenshot_path: Optional[str]    # 最新生成的截图文件路径
    execution_trace: Optional[ExecutionTrace]  # 受控执行轨迹
    pending_risk_review: Optional[HumanFeedback]  # 等待人工审核的高风险工具调用
    risk_review_feedback: Optional[HumanFeedback]  # 人工审核反馈，用于恢复执行
    execution_cursor: Optional[int]  # 下一个待执行 ToolCallPlan 的下标

    
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
def create_initial_state(
    session_id: str,
    input_query: str,
    llm_config: Optional[Dict[str, Any]] = None,
    task_id: str | None = None,
    uploaded_files: List[Dict[str, Any]] | None = None,
    session_name: str | None = None,
    session_created_at: str | None = None,
) -> AgentState:
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
        session_name=session_name,
        session_created_at=session_created_at,
        task_id=task_id or str(uuid.uuid4()),
        uploaded_files=uploaded_files or [],
        
        # LLM配置
        llm_config=llm_config,

        # Planner字段
        log_summary=None,
        advise=None,
        task_plan=None,
        plan_validation=None,
        planning_error=None,
        planning_revision=1,
        last_plan_decision=None,
        last_result_decision=None,
        last_risk_decision=None,
        retry_count=0,
        
        # API RAG字段
        gdal_doc=[],
        pyqgis_doc=[],
        api_context_structured=[],
        missing_deps=[],
        
        # Executor字段
        messages=[],
        screenshot_path=None,
        execution_trace=None,
        pending_risk_review=None,
        risk_review_feedback=None,
        execution_cursor=None,

        
        # Reflector字段
        final_summary=None,
        is_completed=None,
        quality_score=0.0,

        # History字段
        history=[],
    )
