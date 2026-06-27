# QGIS Workflow Agent 工程计划书

> 目标：在现有 QGIS-Agent 原型基础上，重构为一个面向 GIS 工作流的可控执行型 Agent 系统。  
> 核心改进方向包括：loop engineering 闭环执行、step-level 可控工具调用、error-driven API-RAG、ExecutionTrace、LLM 生成 Skill Draft、Skill Registry、HITL 审核与工程化测试。

---

## 1. 项目定位

### 1.1 背景

现有 QGIS-Agent 原型已经具备自然语言任务规划、GDAL/PyQGIS API 检索、QGIS MCP 工具调用、人工审核、Web UI 与 Cookbook 归档等基础能力。其主链路大致为：

```text
User Query
  -> Planner
  -> Human Review
  -> API RAG
  -> Executor
  -> Reflector
  -> Cookbook
```

该链路已经能够证明“自然语言驱动 QGIS 自动化”的可行性，但仍存在几个工程化不足：

1. **Executor 偏黑箱**：工具调用主要由 ReAct Agent 自行决定，缺少 step-level 执行契约。
2. **失败恢复不足**：执行失败后缺少结构化观察、错误分类和自动修复机制。
3. **RAG 仍偏文档检索**：没有系统性地把 API 签名、参数 schema 和错误信息纳入闭环。
4. **Cookbook 抽象层级较低**：历史成功案例没有进一步沉淀为可复用技能。
5. **状态契约不够清晰**：AgentState 字段与节点间隐式依赖较多。
6. **测试与质量门禁不足**：缺少稳定的 mock MCP、RAG parser、execution loop 等测试体系。
7. **安全边界不足**：QGIS 代码执行、文件路径访问和高风险操作需要显式约束。

因此，本计划不以“复制原项目”为目标，而是将其改造为一个更可控、可测、可追踪、可复用的 GIS Agent 工程系统。

### 1.2 项目目标

本项目目标是构建：

> 基于 LangGraph、MCP、API-RAG 和 loop engineering 的 QGIS 工作流自动化 Agent，使系统能够将自然语言 GIS 任务转化为结构化计划，通过可控工具调用执行，并基于执行反馈进行观察、评估、修复和经验沉淀。

核心能力包括：

- 自然语言 GIS 任务理解与结构化规划；
- GDAL/PyQGIS API-RAG 与参数校验；
- step-level MCP 工具调用与执行轨迹记录；
- Plan-Execute-Observe-Evaluate-Repair 闭环；
- HITL 计划审核、高风险操作审核与结果确认；
- 成功任务的 Skill Draft 自动生成与注册；
- 可回放 ExecutionTrace 与失败诊断；
- pytest + mock MCP + CI 质量门禁。

### 1.3 非目标

本阶段不追求：

- 完整替代 QGIS 桌面端；
- 支持所有 PyQGIS/GDAL API；
- 完全无人值守执行所有 GIS 任务；
- 大规模多用户生产部署；
- 训练或微调大模型；
- 将自动生成的 skill 无审核地直接投入执行。

本阶段重点是“可靠的 Agent 工程闭环”，而不是“无限泛化的 GIS 通用智能体”。

---

## 2. 核心概念

### 2.1 Loop Engineering

Loop engineering 指的是将 Agent 设计为反馈驱动的闭环系统，而不是一次性 prompt 调用。

传统线性工作流：

```text
Plan -> Retrieve -> Execute -> Reflect
```

改进后的闭环工作流：

```text
Plan
  -> Retrieve
  -> Execute Step
  -> Observe
  -> Evaluate
  -> Repair
  -> Retry / Ask Human / Abort
  -> Reflect
  -> Save Skill
```

在本项目中，loop engineering 主要体现在：

1. **Planning Loop**：计划生成后自动检查完整性、风险与 API 粒度，不合格则重写或进入人工修订。
2. **RAG Loop**：初次检索 API 文档后检查签名、参数和依赖是否完整，缺失则补充检索。
3. **Execution Loop**：每个 step 执行后观察 MCP 返回结果，判断成功/失败，失败则分类修复并重试。
4. **HITL Loop**：人在计划、高风险操作和结果确认阶段提供结构化反馈。
5. **Experience Loop**：成功任务被抽取为 Skill Draft，校验后注册到 Skill Registry，供后续任务复用。

### 2.2 Skill

本项目中的 skill 不是模型参数层面的“学会”，而是系统层面的可复用工作流模板。

Skill 可以理解为：

> 从成功 GIS 任务执行轨迹中抽取出的、可检索、可实例化、可校验、可执行的任务模板。

它包含：

- 适用任务类型；
- 输入/输出参数；
- 前置条件；
- MCP 工具调用序列；
- API 依赖；
- 成功判定条件；
- 常见错误类型；
- 修复策略；
- 风险等级；
- 示例任务与来源 trace。

### 2.3 Cookbook 与 Skill 的区别

| 层级 | 含义 | 复用方式 |
|---|---|---|
| Execution Log | 某次执行日志 | 调试 |
| ExecutionTrace | 某次结构化执行轨迹 | 回放、诊断、抽取 skill |
| Cookbook Case | 某次成功案例 | 相似任务参考 |
| Skill Draft | LLM 从成功 trace 中生成的技能草稿 | 待校验 |
| Registered Skill | 通过校验/审核后的技能模板 | 直接参与规划与执行 |

从工程角度看：

```text
Cookbook = 历史案例记忆
Skill = 可执行工作流模板
```

---

## 3. 总体架构设计

### 3.1 架构总览

```text
┌───────────────────────────────────────────────────────────┐
│                         Web UI                             │
│  Chat / File Upload / Review Panel / Trace Viewer / Skills │
└────────────────────────────┬──────────────────────────────┘
                             │ SSE / REST
┌────────────────────────────▼──────────────────────────────┐
│                      FastAPI Backend                        │
│  Session API / Review API / Trace API / Skill API           │
└────────────────────────────┬──────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────┐
│                    LangGraph Agent Graph                    │
│  Planner -> Validator -> Retriever -> ExecutionLoop          │
│       -> Reflector -> SkillExtractor -> SkillRegistry        │
└────────────────────────────┬──────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────┐
│                  Loop Engineering Runtime                   │
│  Plan / Retrieve / Execute / Observe / Evaluate / Repair     │
│  ExecutionTrace / StepResult / RepairAction / HITL Gate      │
└────────────────────────────┬──────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────┐
│                    MCP Tool Adapter Layer                    │
│  Tool Registry / Tool Policy / MCP Response Parser           │
│  Path Guard / Code Guard / Processing Guard                  │
└────────────────────────────┬──────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────┐
│                    QGIS MCP Server + Plugin                  │
│  add_layer / execute_processing / execute_code / screenshot  │
└────────────────────────────┬──────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────┐
│                      Storage Layer                          │
│  PostgreSQL / Checkpoint / RAG Docs / Trace / Skills         │
└───────────────────────────────────────────────────────────┘
```

### 3.2 推荐目录结构

```text
src/gis_agent/
  graph/
    app.py
    state.py
    routing.py
  planning/
    planner.py
    plan_schema.py
    plan_validator.py
  retrieval/
    api_retriever.py
    doc_parser.py
    schema_extractor.py
    error_driven_retriever.py
  execution/
    loop.py
    executor.py
    evaluator.py
    repairer.py
    trace.py
    result.py
  mcp/
    client.py
    adapter.py
    response_parser.py
    tool_registry.py
  policies/
    tool_policy.py
    path_policy.py
    code_policy.py
    risk_policy.py
  skills/
    schema.py
    extractor.py
    validator.py
    registry.py
    retriever.py
  storage/
    database.py
    migrations/
  web/
    api.py
    sse.py
  observability/
    events.py
    trace_view.py

tests/
  unit/
  integration/
  fixtures/
```

---

## 4. 核心数据模型设计

### 4.1 AgentState

```python
class AgentState(TypedDict):
    session_id: str
    user_query: str
    current_phase: str

    plan: Optional[TaskPlan]
    plan_review: Optional[ReviewState]

    retrieval_context: Optional[RetrievalContext]
    execution_trace: Optional[ExecutionTrace]

    final_summary: Optional[str]
    skill_drafts: list[SkillDraft]

    human_feedback: list[HumanFeedback]
    artifacts: list[ArtifactRecord]
```

设计原则：

- 状态字段必须是正式契约；
- 不允许节点临时写入未声明字段；
- 大对象如 trace、docs、artifacts 应有可序列化结构；
- 与 UI 相关的展示状态不直接污染核心执行状态。

### 4.2 TaskPlan

```python
class TaskPlan(BaseModel):
    task_id: str
    task_summary: str
    steps: list[PlanStep]
    risk_level: Literal["low", "medium", "high"]
    required_inputs: list[InputSpec]
    expected_outputs: list[OutputSpec]
    metadata: dict[str, Any]
```

### 4.3 PlanStep

```python
class PlanStep(BaseModel):
    step_id: int
    description: str
    step_type: str
    candidate_gdal_apis: list[str]
    candidate_pyqgis_apis: list[str]
    allowed_tools: list[str]
    input_refs: list[str]
    output_refs: list[str]
    requires_review: bool = False
```

### 4.4 ToolCallPlan

```python
class ToolCallPlan(BaseModel):
    step_id: int
    tool_name: str
    arguments: dict[str, Any]
    risk_level: Literal["low", "medium", "high"]
    evidence_docs: list[str]
    expected_result: dict[str, Any]
```

### 4.5 StepAttempt

```python
class StepAttempt(BaseModel):
    attempt_id: str
    step_id: int
    tool_call: ToolCallPlan
    observation: ToolObservation
    evaluation: StepEvaluation
    repair_action: Optional[RepairAction]
    started_at: datetime
    ended_at: datetime
```

### 4.6 ExecutionTrace

```python
class ExecutionTrace(BaseModel):
    trace_id: str
    session_id: str
    user_query: str
    plan_snapshot: TaskPlan
    attempts: list[StepAttempt]
    artifacts: list[ArtifactRecord]
    final_status: Literal["success", "failed", "aborted", "needs_review"]
    summary: Optional[str]
```

ExecutionTrace 是本项目的核心资产。它不仅用于前端展示和失败诊断，也用于后续 Skill Draft 抽取。

---

## 5. Loop Engineering 设计

### 5.1 主循环

```text
for step in plan.steps:
    for attempt in range(max_attempts):
        tool_call = build_tool_call(step, retrieval_context, trace)
        policy_report = validate_tool_call(tool_call)

        if policy_report.requires_human_review:
            pause_for_human_review()

        observation = execute_tool_call(tool_call)
        evaluation = evaluate_observation(step, observation)

        trace.append(step, tool_call, observation, evaluation)

        if evaluation.success:
            break

        repair_action = repairer.propose(step, observation, evaluation, trace)

        if repair_action.requires_more_docs:
            retrieval_context = error_driven_retrieve(observation.error)

        if repair_action.requires_human:
            pause_for_human_feedback()

        apply_repair(repair_action)
    else:
        abort_or_escalate()
```

### 5.2 Observe

Observe 阶段将 MCP 返回结果统一解析为标准结构：

```python
class ToolObservation(BaseModel):
    success: bool
    raw_response: dict[str, Any]
    normalized_output: dict[str, Any]
    artifacts: list[ArtifactRecord]
    error_message: Optional[str]
    error_type: Optional[str]
```

### 5.3 Evaluate

Evaluator 不依赖 LLM 单独判断，而是结合规则与可选 LLM 辅助：

```python
class StepEvaluation(BaseModel):
    success: bool
    confidence: float
    checks: list[CheckResult]
    error_type: Optional[str]
    explanation: str
```

典型检查包括：

- 工具返回 success；
- 输出文件存在；
- QGIS 中新增图层；
- 图层 CRS 符合预期；
- 输出 artifact 可访问；
- 无异常错误信息；
- screenshot 成功生成。

### 5.4 Repair

Repairer 根据错误类型选择修复策略：

| 错误类型 | 修复策略 |
|---|---|
| missing_parameter | 重新检索 Processing 参数 schema |
| invalid_api_name | 使用 API fuzzy search 补充检索 |
| file_not_found | 请求用户重新上传或修正路径 |
| output_exists | 进入 HITL，确认覆盖或改名 |
| layer_not_found | 查询当前 QGIS 图层列表并重绑定 |
| crs_mismatch | 插入重投影步骤或请求确认 |
| qgis_disconnected | 重连 MCP / 提示用户启动 QGIS 插件 |
| unsafe_code | 拒绝执行并请求改写 |

---

## 6. API-RAG 设计

### 6.1 检索目标

API-RAG 不只检索“文档文本”，而是构建可执行上下文：

```text
API symbol
  -> signature
  -> parameters
  -> return type
  -> example code
  -> processing algorithm schema
  -> common errors
```

### 6.2 RAG 数据结构

```python
class ApiDocRecord(BaseModel):
    api_name: str
    library: Literal["gdal", "pyqgis", "processing"]
    signature: Optional[str]
    parameters: list[ParameterSpec]
    returns: Optional[str]
    description: str
    examples: list[str]
    source: str
```

### 6.3 Error-driven RAG

当执行失败时，从错误信息中提取：

- API 名称；
- 参数名；
- Processing algorithm；
- 返回类型；
- 文件路径；
- 图层 ID；
- CRS 信息。

然后触发二次检索：

```text
Tool Error
  -> Error Classifier
  -> Query Builder
  -> API/Processing Schema Retriever
  -> Context Update
  -> Repair ToolCallPlan
```

### 6.4 参数校验

ToolCallPlan 执行前必须经过 schema 校验：

```text
tool arguments
  -> required parameter check
  -> type check
  -> allowed value check
  -> path permission check
  -> risk check
```

---

## 7. Tool Policy 与安全边界设计

### 7.1 ToolPolicy

```python
class ToolPolicy(BaseModel):
    tool_name: str
    allowed: bool
    risk_level: Literal["low", "medium", "high"]
    requires_review: bool
    allowed_paths: list[str]
    denied_patterns: list[str]
```

### 7.2 路径安全

原则：

- 所有输入文件必须来自 `workspace/`、`uploads/` 或用户显式授权路径；
- 所有输出文件默认写入 `workspace/outputs/`；
- 覆盖已有文件需要人工确认；
- 禁止访问系统敏感路径。

### 7.3 代码执行安全

对于 `execute_code`，增加 CodeGuard：

- 使用 AST 检查；
- 禁止 `subprocess`、`os.system`、任意网络请求；
- 限制文件删除、递归目录写入；
- 对高风险代码要求人工确认；
- 保留 raw code、review 状态与执行结果。

### 7.4 高风险操作

高风险操作包括：

- 删除图层；
- 覆盖文件；
- 批量处理大量文件；
- 执行任意 PyQGIS 代码；
- 修改或保存 QGIS 项目；
- 访问非 workspace 路径。

高风险操作必须进入 HITL Risk Review。

---

## 8. Skill Draft 自动生成设计

### 8.1 目标

将成功任务从“历史案例”升级为“可复用技能”。

完整链路：

```text
Successful ExecutionTrace
  -> SkillExtractor
  -> SkillDraft
  -> SkillValidator
  -> Human Review
  -> RegisteredSkill
  -> Future Skill Retrieval
```

### 8.2 SkillDraft Schema

```python
class SkillDraft(BaseModel):
    skill_id: str
    name: str
    description: str

    intent_patterns: list[str]
    input_schema: dict[str, InputSpec]
    output_schema: dict[str, OutputSpec]

    preconditions: list[str]
    tool_sequence: list[ToolCallTemplate]
    success_criteria: list[str]
    repair_strategies: list[RepairStrategy]

    risk_level: Literal["low", "medium", "high"]
    source_trace_id: str
    generated_by: Literal["llm", "human", "hybrid"]
    validation_status: Literal["draft", "validated", "rejected", "registered"]
```

### 8.3 LLM 在 Skill 生成中的职责

LLM 适合负责：

- 识别任务类型；
- 归纳任务意图模式；
- 将具体路径、图层名泛化为变量；
- 生成工具序列说明；
- 生成 skill 描述；
- 提取常见修复策略初稿。

LLM 不应单独负责：

- 判断文件是否存在；
- 判断工具参数是否合法；
- 注册 skill 为正式可执行能力；
- 安全策略判断；
- 最终结果验收。

### 8.4 SkillExtractor 流程

```text
Input: Successful ExecutionTrace

1. 提取任务摘要与执行步骤
2. 提取成功工具调用序列
3. 提取输入输出 artifact
4. 识别具体值：路径、图层名、CRS、算法名
5. 将具体值泛化为变量
6. 让 LLM 生成 SkillDraft
7. 用程序校验 schema 完整性
8. 输出 SkillDraft + ValidationReport
```

### 8.5 SkillValidator

SkillValidator 负责：

- 校验 schema 是否完整；
- 校验工具是否存在；
- 校验工具参数模板是否可实例化；
- 校验路径变量是否安全；
- 校验 success criteria 是否可执行；
- 校验 risk_level 是否匹配；
- 可选：用 mock MCP 回放工具序列。

### 8.6 Skill Registry

Skill Registry 存储注册后的技能：

```text
skills
  id
  name
  description
  intent_patterns
  input_schema
  output_schema
  tool_sequence
  success_criteria
  repair_strategies
  risk_level
  source_trace_id
  usage_count
  success_count
  failure_count
  created_at
  updated_at
```

Planner 阶段优先检索 Skill：

```text
User Query
  -> Skill Retriever
  -> matched skill?
        yes -> instantiate skill plan
        no  -> normal planning + RAG
```

---

## 9. HITL 设计

### 9.1 HITL 类型

| 类型 | 触发点 | 用户操作 |
|---|---|---|
| Plan Review | Planner 生成计划后 | 批准 / 修改 / 拒绝 |
| Risk Review | 高风险工具调用前 | 批准 / 修改参数 / 拒绝 |
| Result Review | 执行完成后 | 成功 / 失败 / 补充反馈 |
| Skill Review | SkillDraft 生成后 | 注册 / 修改 / 拒绝 |

### 9.2 结构化反馈

```python
class HumanFeedback(BaseModel):
    feedback_id: str
    feedback_type: Literal[
        "plan_review",
        "risk_review",
        "result_review",
        "skill_review"
    ]
    target_id: str
    approved: bool
    message: Optional[str]
    structured_edits: dict[str, Any]
    created_at: datetime
```

结构化反馈会进入 ExecutionTrace，并可作为后续 SkillExtractor 的输入。

---

## 10. Web UI 与可观测性设计

### 10.1 前端视图

推荐增加四类视图：

1. **Plan Review Panel**
   - 显示结构化计划；
   - 标记风险步骤；
   - 支持用户编辑步骤。

2. **Execution Trace Viewer**
   - 展示每个 step；
   - 展示工具调用参数；
   - 展示 MCP 返回结果；
   - 展示错误分类与修复动作。

3. **RAG Evidence Panel**
   - 展示每一步使用的 API 文档；
   - 显示参数 schema；
   - 显示二次检索原因。

4. **Skill Registry Panel**
   - 展示已注册 skill；
   - 展示 skill draft；
   - 支持审核、禁用、删除、查看来源 trace。

### 10.2 SSE 事件

推荐事件类型：

```text
session_started
plan_generated
plan_validation_failed
human_review_required
retrieval_started
retrieval_completed
tool_call_planned
tool_call_policy_blocked
tool_call_started
tool_call_completed
step_evaluated
repair_started
repair_completed
trace_updated
skill_draft_generated
skill_review_required
task_completed
task_failed
```

---

## 11. 数据库设计

### 11.1 新增表

推荐新增：

```text
execution_traces
step_attempts
tool_observations
human_feedback
skill_drafts
registered_skills
skill_usage_logs
```

### 11.2 execution_traces

```sql
CREATE TABLE execution_traces (
    trace_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_query TEXT NOT NULL,
    plan_snapshot JSONB NOT NULL,
    final_status TEXT NOT NULL,
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 11.3 step_attempts

```sql
CREATE TABLE step_attempts (
    attempt_id TEXT PRIMARY KEY,
    trace_id TEXT REFERENCES execution_traces(trace_id),
    step_id INT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_arguments JSONB NOT NULL,
    observation JSONB,
    evaluation JSONB,
    repair_action JSONB,
    started_at TIMESTAMP,
    ended_at TIMESTAMP
);
```

### 11.4 registered_skills

```sql
CREATE TABLE registered_skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    intent_patterns JSONB NOT NULL,
    input_schema JSONB NOT NULL,
    output_schema JSONB,
    tool_sequence JSONB NOT NULL,
    success_criteria JSONB NOT NULL,
    repair_strategies JSONB,
    risk_level TEXT NOT NULL,
    source_trace_id TEXT,
    usage_count INT DEFAULT 0,
    success_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 12. 测试与质量门禁

### 12.1 单元测试

必须覆盖：

- Plan schema 解析；
- PlanValidator；
- MCP response parser；
- ToolPolicy；
- PathPolicy；
- CodePolicy；
- ApiDoc parser；
- error classifier；
- StepEvaluator；
- Repairer；
- SkillExtractor；
- SkillValidator。

### 12.2 集成测试

必须覆盖：

1. 规划 -> 审核 -> RAG -> mock MCP 执行 -> trace 生成；
2. 工具调用参数错误 -> error-driven RAG -> repair -> retry；
3. 高风险操作 -> HITL 阻断；
4. 成功 trace -> skill draft -> validation report；
5. skill 命中 -> 实例化计划 -> 执行。

### 12.3 Mock MCP Server

建立 mock MCP 工具：

```text
add_layer
execute_processing
execute_code
capture_map_canvas
search_gdal_api
search_pyqgis_api
```

支持：

- success response；
- parameter error；
- file not found；
- layer not found；
- QGIS disconnected；
- output exists。

### 12.4 CI

使用 GitHub Actions：

```text
ruff check
black --check
mypy
pytest
coverage report
```

最低质量门槛：

- 单元测试通过；
- 核心模块覆盖率 > 75%；
- 无高风险 lint 错误；
- PR 必须通过 mock integration tests。

---

## 13. 开发计划分解

### Phase 0：项目重构与边界声明

目标：建立自己的工程结构，明确与原型项目的关系。

任务：

1. 新建项目结构 `src/gis_agent/`；
2. 整理 README，声明项目基于 QGIS-Agent 思路进行二次设计；
3. 抽离配置系统；
4. 建立基础 pyproject；
5. 配置 ruff、black、mypy、pytest；
6. 添加 GitHub Actions；
7. 建立 mock MCP fixture。

交付物：

- 新目录结构；
- 基础 CI；
- 项目说明文档；
- 初始测试框架。

验收标准：

- `pytest` 可运行；
- CI 可通过；
- 不再直接依赖原型项目的目录结构。

---

### Phase 1：状态契约与计划模型

目标：建立清晰的 TaskPlan / AgentState / PlanStep 契约。

任务：

1. 定义 `TaskPlan`、`PlanStep`、`InputSpec`、`OutputSpec`；
2. 重构 Planner 输出为严格 JSON schema；
3. 实现 `PlanValidator`；
4. 支持 plan risk level；
5. 支持 plan review 状态；
6. 添加 plan parser 和 validator 测试。

交付物：

- `planning/plan_schema.py`
- `planning/planner.py`
- `planning/plan_validator.py`
- `tests/unit/test_plan_validator.py`

验收标准：

- LLM 输出不合法时可被 parser 捕获；
- 缺路径、缺输出、API 粒度过粗的计划会被 validator 标记；
- 高风险步骤会被标记 `requires_review=True`。

---

### Phase 2：MCP Adapter 与 Tool Policy

目标：统一工具调用接口，建立工具安全边界。

任务：

1. 实现 `MCPClientAdapter`；
2. 实现统一 MCP response parser；
3. 定义 ToolRegistry；
4. 实现 ToolPolicy；
5. 实现 PathPolicy；
6. 实现 CodePolicy；
7. 实现 RiskPolicy；
8. 添加 mock MCP 测试。

交付物：

- `mcp/adapter.py`
- `mcp/response_parser.py`
- `mcp/tool_registry.py`
- `policies/tool_policy.py`
- `policies/path_policy.py`
- `policies/code_policy.py`

验收标准：

- 所有 MCP 返回都能归一化为 `ToolObservation`；
- 非 workspace 输出路径被阻断；
- 高风险工具调用会要求 review；
- 危险代码被 CodePolicy 拒绝。

---

### Phase 3：ExecutionTrace 与 Step-level Executor

目标：替代黑箱 Executor，建立 step-level 可控执行。

任务：

1. 定义 `ToolCallPlan`；
2. 定义 `StepAttempt`；
3. 定义 `ExecutionTrace`；
4. 实现 StepExecutor；
5. 实现 trace append / serialize；
6. 实现 artifact record；
7. 添加 trace viewer API 初稿。

交付物：

- `execution/trace.py`
- `execution/result.py`
- `execution/executor.py`
- `tests/unit/test_execution_trace.py`
- `tests/integration/test_step_executor_mock_mcp.py`

验收标准：

- 每次工具调用都生成 StepAttempt；
- trace 可序列化保存；
- 失败与成功步骤均可回放；
- Executor 不再完全依赖黑箱 ReAct Agent。

---

### Phase 4：Plan-Execute-Observe-Evaluate-Repair Loop

目标：实现核心 loop engineering 执行闭环。

任务：

1. 实现 `ExecutionLoop`；
2. 实现 `StepEvaluator`；
3. 实现 `ErrorClassifier`；
4. 实现 `Repairer`；
5. 实现 max_attempts；
6. 实现失败后 repair action；
7. 实现 retry / abort / ask human 逻辑；
8. 添加错误恢复集成测试。

交付物：

- `execution/loop.py`
- `execution/evaluator.py`
- `execution/repairer.py`
- `execution/error_classifier.py`
- `tests/integration/test_execution_loop_repair.py`

验收标准：

- 参数错误可触发 repair；
- 输出文件已存在可触发 HITL；
- QGIS 断连可触发重连或提示；
- 重试次数达到上限后任务进入 failed/needs_review。

---

### Phase 5：API-RAG 与 Error-driven Retrieval

目标：将 RAG 从文档检索升级为参数精度支持系统。

任务：

1. 定义 `ApiDocRecord`；
2. 实现 GDAL/PyQGIS doc parser；
3. 实现参数 schema extractor；
4. 实现 RetrievalContext；
5. 实现 step-level docs grouping；
6. 实现 error-driven retriever；
7. 对 ToolCallPlan 做参数 schema 校验；
8. 添加 RAG 相关测试。

交付物：

- `retrieval/api_retriever.py`
- `retrieval/doc_parser.py`
- `retrieval/schema_extractor.py`
- `retrieval/error_driven_retriever.py`
- `tests/unit/test_api_doc_parser.py`
- `tests/integration/test_error_driven_rag.py`

验收标准：

- 每个 step 有局部 API context；
- 缺少辅助类时能补充检索；
- 执行错误能生成二次检索 query；
- 参数 schema 可用于 ToolCallPlan 校验。

---

### Phase 6：HITL Review System

目标：将人工介入从简单批准扩展为结构化反馈闭环。

任务：

1. 定义 HumanFeedback schema；
2. 实现 plan review API；
3. 实现 risk review API；
4. 实现 result review API；
5. 将 feedback 写入 trace；
6. 前端展示 review panel；
7. 添加 review 恢复测试。

交付物：

- `graph/review.py`
- `web/review_api.py`
- `tests/integration/test_hitl_review.py`

验收标准：

- plan 可被批准、修改、拒绝；
- 高风险工具调用必须暂停；
- 用户反馈可结构化保存；
- 恢复执行后 trace 保留 review 信息。

---

### Phase 7：Skill Draft 自动生成

目标：从成功 ExecutionTrace 中生成可审核 SkillDraft。

任务：

1. 定义 SkillDraft schema；
2. 实现 SkillExtractor；
3. 编写 LLM skill extraction prompt；
4. 泛化路径、图层名、输出文件；
5. 提取工具调用序列；
6. 提取 success criteria；
7. 提取 repair strategies；
8. 生成 SkillDraft；
9. 添加 SkillExtractor 测试。

交付物：

- `skills/schema.py`
- `skills/extractor.py`
- `prompts/skill_extraction.md`
- `tests/unit/test_skill_extractor.py`

验收标准：

- 成功 trace 可生成 skill draft；
- 具体路径被泛化为变量；
- tool_sequence 可复用；
- draft 不会自动注册为正式 skill。

---

### Phase 8：Skill Validation 与 Registry

目标：将 SkillDraft 校验后注册为可复用 skill。

任务：

1. 实现 SkillValidator；
2. 实现 SkillRegistry；
3. 实现 skill review；
4. 实现 skill retrieval；
5. Planner 优先检索 skill；
6. skill 命中后实例化 TaskPlan；
7. 记录 skill usage；
8. 添加 skill reuse 集成测试。

交付物：

- `skills/validator.py`
- `skills/registry.py`
- `skills/retriever.py`
- `tests/integration/test_skill_registry.py`
- `tests/integration/test_skill_reuse.py`

验收标准：

- 不合格 skill draft 被拒绝；
- 注册 skill 可被查询；
- 用户新任务可命中 skill；
- skill 能实例化为计划和工具调用模板；
- skill 使用结果会更新统计信息。

---

### Phase 9：Web Trace Viewer 与 Skill Panel

目标：增强可观测性和演示效果。

任务：

1. Trace API；
2. Trace timeline 前端；
3. RAG evidence panel；
4. Skill draft review panel；
5. Skill registry panel；
6. Screenshot/artifact 展示；
7. SSE 事件完善。

交付物：

- `web/trace_api.py`
- `web/skill_api.py`
- 前端 Trace Viewer
- 前端 Skill Panel

验收标准：

- 用户可看到每一步工具调用；
- 用户可查看 RAG 证据；
- 用户可审核 SkillDraft；
- 用户可查看 skill 来源 trace。

---

### Phase 10：Benchmark 与项目包装

目标：形成可展示、可量化、可写入简历的成果。

任务：

1. 准备 5-10 个 GIS 自动化任务；
2. 对比 baseline 与改进系统；
3. 指标设计：
   - 任务成功率；
   - 平均重试次数；
   - API 参数错误率；
   - 人工介入次数；
   - skill 命中后执行时间；
4. 录制 demo；
5. 编写最终 README；
6. 编写项目架构图与面试讲解稿。

交付物：

- Benchmark report；
- Demo cases；
- README；
- Architecture docs；
- Resume bullets。

验收标准：

- 至少 3 个端到端 demo；
- 至少 1 个失败修复 demo；
- 至少 1 个 skill 生成与复用 demo；
- benchmark 可复现。

---

## 14. 推荐开发顺序

最小可行路线：

```text
Phase 0
  -> Phase 1
  -> Phase 2
  -> Phase 3
  -> Phase 4
  -> Phase 7
  -> Phase 8
  -> Phase 10
```

也就是说，优先实现：

1. 工程结构；
2. 状态和计划契约；
3. MCP Adapter 和 ToolPolicy；
4. ExecutionTrace；
5. ExecutionLoop；
6. SkillDraft；
7. SkillRegistry；
8. Demo 和包装。

RAG 深度优化、Web 完整可视化可以作为后续增强。

---

## 15. 简历包装建议

### 项目名称

```text
QGIS Workflow Agent：基于 LangGraph + MCP + Loop Engineering 的 GIS 自动化系统
```

### 简历描述

```text
基于 LangGraph、MCP 和 API-RAG 构建面向 QGIS 的自然语言 GIS 自动化系统，将用户地理处理需求拆解为结构化执行计划，并通过 Plan-Execute-Observe-Evaluate-Repair 闭环实现可控工具调用、错误修复、人工审核和经验沉淀。
```

### 简历 Bullet

- 基于 loop engineering 思路重构 QGIS Agent 执行链路，设计 Plan-Execute-Observe-Evaluate-Repair 闭环，对 GIS 工具调用结果进行结构化观察、错误分类和自动修复。
- 将黑箱 ReAct 执行器重构为 step-level 可控执行模型，设计 `ToolCallPlan / StepAttempt / StepResult / ExecutionTrace`，提升任务执行的可解释性与失败定位能力。
- 构建 error-driven API-RAG 机制，根据 GDAL/PyQGIS 执行错误自动触发补充检索、参数 schema 校验和工具调用修正。
- 设计 MCP 工具调用安全策略，对文件路径、任意 PyQGIS 代码执行和高风险 QGIS 操作进行权限控制与 HITL 审核。
- 实现基于 ExecutionTrace 的 Skill Extraction 机制，由 LLM 生成 SkillDraft，并经 schema 校验和人工审核后注册到 Skill Registry，供后续相似 GIS 任务复用。
- 建立 pytest + mock MCP Server 测试体系，覆盖计划校验、MCP 返回解析、执行闭环、错误修复、SkillDraft 生成与 skill 复用流程。

---

## 16. 风险与应对

| 风险 | 说明 | 应对 |
|---|---|---|
| LLM 输出不稳定 | planner 或 skill draft 可能不合法 | 严格 schema parser + retry + fallback |
| QGIS 环境难以自动测试 | QGIS desktop 依赖重 | mock MCP + 小规模真实集成测试 |
| skill 泛化错误 | 把具体案例错误泛化 | draft 不自动注册，必须校验/审核 |
| 代码执行风险 | execute_code 权限过大 | AST CodeGuard + HITL |
| RAG 质量不足 | 文档不完整导致参数错误 | error-driven retrieval + schema extraction |
| 项目范围膨胀 | Agent 系统很容易越做越大 | 以 ExecutionTrace + ExecutionLoop + SkillDraft 为主线 |

---

## 17. 最终目标形态

完成后，系统应具备以下端到端能力：

```text
用户：请对上传的 DEM 做坡度分析，并把结果加载到 QGIS 中截图。

1. Planner 生成结构化计划
2. PlanValidator 检查输入、输出和风险
3. SkillRetriever 查找是否已有 slope_analysis skill
4. 若命中 skill，则实例化工具调用模板
5. 若未命中，则走 API-RAG 生成 ToolCallPlan
6. ExecutionLoop 执行 add_layer / execute_processing / add_layer / screenshot
7. Observe 解析 MCP 返回结果
8. Evaluate 检查输出文件、图层和截图
9. 若失败，Repairer 根据错误触发补充检索或重试
10. 成功后生成 ExecutionTrace
11. Reflector 总结任务
12. SkillExtractor 从成功 trace 生成 SkillDraft
13. 用户审核后注册 skill
14. 未来相似任务优先复用该 skill
```

这使项目从“QGIS Agent 原型”升级为：

> 一个可控、可测、可追踪、可积累经验的 GIS 工作流 Agent 工程系统。
