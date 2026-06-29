# qgis-workflow-agent 协同开发计划

> 目标读者：Codex / AI 编程助手 / 项目实现者  
> 项目定位：qgis-workflow-agent 是 GIS 智能体运行时与执行闭环项目，负责用户交互、任务状态、API-RAG、工具接入、ExecutionLoop、ExecutionTrace、HITL 与 SkillRegistry。  
> 协同目标：与 GeoTool-LLM 通过 `TaskPlan / ToolCallPlan / RepairAction / ExecutionTrace` 契约对齐，使 qgis-workflow-agent 能从 create_agent 黑箱执行逐步迁移到显式 `ToolCallPlan -> ExecutionLoop -> ExecutionTrace` 执行链。

---

## 1. 项目定位

qgis-workflow-agent 不负责训练模型，也不负责构建大规模 SFT/DPO 数据。它是完整 Agent Runtime，主要负责：

```text
User Query
  -> Planner
  -> API-RAG
  -> ToolCallPlan generation / ToolCallPlanner
  -> ToolPolicy
  -> ExecutionLoop
  -> StepExecutor
  -> Observe / Evaluate / Repair
  -> ExecutionTrace
  -> SkillExtractor / SkillRegistry
```

它与 GeoTool-LLM 的关系：

```text
qgis-workflow-agent 产生真实执行 trace
        ↓
GeoTool-LLM 使用 trace 构造 SFT / DPO 数据
        ↓
GeoTool-LLM 训练 Qwen3-1.7B LoRA
        ↓
qgis-workflow-agent 接入 GeoTool-LLM 作为 ToolCallPlanner
        ↓
执行结果继续产生 trace，反哺训练数据
```

---

## 2. 当前状态判断

当前项目已有较完整的 Agent 工程雏形：

- Planner Node 可生成结构化 `TaskPlan`。
- API-RAG Node 已有基础。
- StateGraph / LangGraph 主链路已成型。
- `ToolCallPlan`、`ToolObservation`、`StepAttempt`、`ExecutionTrace` 等执行契约已有雏形。
- `StepExecutor` 和 `ExecutionLoop` 已有可控执行基础。
- 测试中已覆盖参数错误修复、文件缺失人工审核、QGIS 断连重试、最大尝试次数失败等场景。

主要问题：

> 当前主执行链路仍较依赖 LangChain `create_agent` / ReAct 内部工具调用，具体工具选择和参数生成偏黑箱；`ToolCallPlan` 虽已存在，但还没有完全成为主链路的稳定中间产物。

下一阶段应该将系统从：

```text
TaskPlan
  -> create_agent 内部决定工具调用
  -> 返回结果
```

逐步迁移到：

```text
TaskPlan
  -> ToolCallPlan
  -> ToolPolicy
  -> StepExecutor / ExecutionLoop
  -> ExecutionTrace
```

---

## 3. 下一阶段总目标

下一阶段的目标不是继续扩 UI，也不是扩大 MCP 工具范围，而是：

> 建立 qgis-workflow-agent 与 GeoTool-LLM 之间的 schema adapter，并跑通一个最小显式 ToolCallPlan 执行闭环。

最小闭环：

```text
用户任务
  -> qgis Planner 生成 TaskPlan
  -> adapter 转为 GeoTool-compatible TaskPlan
  -> ToolCallPlanner 生成 ToolCallPlan
  -> ToolPolicy 校验
  -> ExecutionLoop 执行
  -> ExecutionTrace 记录
  -> 导出 GeoTool trace JSONL
```

---

## 4. Q0-1：梳理并冻结当前执行契约

### 4.1 目标

确认 qgis-workflow-agent 当前内部有哪些契约可复用，哪些需要 adapter 对齐 GeoTool-LLM。

### 4.2 重点文件

```text
agent/tools/tool_contracts.py
agent/tools/step_executor.py
agent/tools/execution_loop.py
agent/tools/tool_policy.py
agent/planning/plan_schema.py
agent/planning/legacy_adapter.py
agent/nodes/planner_node.py
agent/nodes/executor_node.py
```

### 4.3 输出文档

新增：

```text
docs/geotool_llm_integration.md
```

文档内容：

- 当前 qgis `TaskPlan` 字段；
- 当前 qgis `ToolCallPlan` 字段；
- 当前 qgis `ExecutionTrace` 字段；
- 与 GeoTool-LLM schema 的差异；
- adapter 映射表；
- 第一阶段不对齐的字段；
- 兼容策略。

### 4.4 验收标准

- 明确哪些类型继续作为 qgis runtime 内部类型；
- 明确哪些字段导出给 GeoTool-LLM；
- 明确 adapter 不直接引入 GeoTool-LLM 源码依赖，先通过 dict / JSON 对齐。

---

## 5. Q0-2：实现 GeoTool Schema Adapter

### 5.1 目标

不要让两个仓库强耦合。qgis-workflow-agent 先实现 adapter，将内部结构转换成 GeoTool-compatible JSON。

### 5.2 文件

```text
agent/adapters/__init__.py
agent/adapters/geotool_schema_adapter.py
tests/test_geotool_schema_adapter.py
```

### 5.3 第一版函数

```python
def qgis_task_plan_to_geotool_dict(task_plan, user_query: str) -> dict:
    ...

def geotool_task_plan_to_qgis_legacy_plan(payload: dict):
    ...

def geotool_tool_call_plan_to_qgis_calls(payload: dict) -> list:
    ...

def qgis_execution_trace_to_geotool_dict(trace, user_query: str) -> dict:
    ...
```

第一阶段不要求导入 GeoTool-LLM 包，只输出符合约定的 dict。

### 5.4 映射建议

qgis TaskPlan：

```text
task_id            -> task_id
task_summary       -> goal
required_inputs    -> required_inputs
expected_outputs   -> expected_outputs
steps              -> steps
risk_level         -> risk_level
```

qgis PlanStep：

```text
step_id                    -> step_id
description                -> description
candidate_gdal_apis        -> metadata.candidate_gdal_apis
candidate_pyqgis_apis      -> metadata.candidate_pyqgis_apis
allowed_tools              -> candidate_tools
input_refs                 -> input_refs
output_refs                -> output_refs
risk_level                 -> risk_level
requires_review            -> requires_review
```

GeoTool ToolCallPlan 到 qgis ToolCallPlan：

```text
call_id          -> metadata.call_id
step_id          -> step_id
tool             -> tool_name
args             -> arguments
risk_level       -> risk_level
requires_review  -> requires_review
depends_on       -> metadata.depends_on
output_ref       -> metadata.output_ref
```

### 5.5 验收标准

- 给定 qgis TaskPlan，可导出 GeoTool-compatible dict；
- 导出的 dict 包含 task_id、user_query、goal、steps；
- step 的 `allowed_tools` 能映射为 `candidate_tools`；
- 给定 GeoTool ToolCallPlan dict，可生成 qgis 内部 ToolCallPlan；
- 测试覆盖缺字段、空 steps、非法 tool payload。

---

## 6. Q0-3：实现最小 ToolCallPlanner

### 6.1 目标

先不要等 GeoTool-LLM 训练完成。先在 qgis-workflow-agent 内实现一个最小 ToolCallPlanner，用规则或 prompt 生成 ToolCallPlan，证明显式执行链可跑通。

### 6.2 文件

```text
agent/tools/tool_call_planner.py
tests/test_tool_call_planner.py
```

### 6.3 输入

```python
class ToolCallPlannerInput:
    task_plan: TaskPlan
    step_id: int | str
    available_tools: list[str]
    api_context: list[dict]
    workspace_state: dict
    previous_observation: dict | None
```

### 6.4 输出

```text
ToolCallPlan 或 list[ToolCallPlan]
```

### 6.5 第一版策略

先实现 rule-based，不接模型：

- 如果 step.allowed_tools 只有一个工具，则使用该工具；
- 如果 step.description 包含“加载图层”，选 `add_layer` 或 `load_vector_layer`；
- 如果 step.description 包含“processing / buffer / clip / intersection”，选 `execute_processing`；
- 如果 step.description 包含“截图”，选 `capture_map_canvas`；
- 从 input_refs / output_refs / metadata 中填入简单参数；
- 参数不足时标记 `requires_review=True` 或返回 planner error。

### 6.6 验收标准

- 给定简单 TaskPlan，可生成 1–3 个 ToolCallPlan；
- 不调用 LLM；
- 输出能通过 qgis ToolPolicy；
- 参数不足时不盲目执行。

---

## 7. Q0-4：接入 ExecutionLoop 路径

### 7.1 目标

证明 qgis-workflow-agent 可以不走 `create_agent` 黑箱工具调用，而是显式执行 ToolCallPlan。

### 7.2 文件

```text
agent/nodes/tool_call_execution_node.py
tests/test_tool_call_execution_node.py
tests/test_tool_call_execution_loop_integration.py
```

### 7.3 流程

```text
TaskPlan
  -> ToolCallPlanner
  -> ToolCallPlan
  -> ToolPolicy
  -> ExecutionLoop.run()
  -> ExecutionTrace
```

### 7.4 第一版支持工具

不要一开始覆盖全部 MCP 工具。先支持 2–3 个：

```text
add_layer / load_vector_layer
execute_processing
capture_map_canvas
```

### 7.5 验收标准

- 一个简单“加载图层并截图”任务可以构造 ToolCallPlan；
- ToolCallPlan 通过 policy；
- ExecutionLoop 执行；
- ExecutionTrace 记录 attempt；
- 测试使用 mock MCP，不依赖真实 QGIS。

---

## 8. Q0-5：导出 GeoTool Trace Seed

### 8.1 目标

让 qgis-workflow-agent 产生 GeoTool-LLM 可用的训练数据种子。

### 8.2 文件

```text
agent/tools/export_geotool_trace.py
tests/test_export_geotool_trace.py
exports/geotool_traces/
```

### 8.3 输出格式

```json
{
  "trace_id": "trace-001",
  "task_id": "task-001",
  "user_query": "加载一个矢量图层并截图",
  "task_plan": {},
  "tool_call_plan": {},
  "observations": [],
  "repair_actions": [],
  "final_status": "success",
  "metadata": {
    "source_project": "qgis-workflow-agent",
    "export_version": "v0"
  }
}
```

### 8.4 第一批 trace

至少导出：

```text
3 条 success trace
2 条 failed / repaired trace
```

建议案例：

1. 加载矢量图层成功；
2. 执行 buffer 成功；
3. 截图成功；
4. 参数缺失 -> repair -> 成功；
5. 文件缺失 -> needs_review。

### 8.5 验收标准

- trace 可写入 JSONL；
- 每条 trace 包含 user_query；
- 包含 tool_call_plan；
- 包含 observations；
- failed trace 包含 error_type；
- repaired trace 包含 repair_action 或 repair metadata。

---

## 9. Q0-6：保留 create_agent 路径作为 baseline

### 9.1 目标

不要立刻删除原 executor。保留 `create_agent` 路径作为 baseline，对比显式 ToolCallPlan 路径。

### 9.2 推荐策略

新增配置：

```toml
[agent.execution]
mode = "react_agent"  # or "tool_call_plan"
```

支持：

```text
react_agent：原 create_agent 路线
tool_call_plan：显式 ToolCallPlan -> ExecutionLoop 路线
```

### 9.3 对比指标

后续记录：

```text
task_success
tool_call_count
retry_count
parameter_error_count
needs_review_count
execution_time
trace_completeness
```

### 9.4 验收标准

- 两条执行模式可切换；
- 默认仍可使用原模式；
- 新模式可在 mock 测试中通过；
- README 或 docs 说明两种模式差异。

---

## 10. Q0-7：测试与质量门禁

### 10.1 目标命令

确保新增内容能被现有测试体系覆盖。

```powershell
uv run pytest -q
uv run ruff check .
```

如果项目已有 mypy 配置，可加入：

```powershell
uv run mypy agent
```

### 10.2 新增测试

```text
tests/test_geotool_schema_adapter.py
tests/test_tool_call_planner.py
tests/test_tool_call_execution_node.py
tests/test_export_geotool_trace.py
```

### 10.3 验收标准

- adapter 测试通过；
- ToolCallPlanner rule-based 输出稳定；
- mock MCP execution loop 通过；
- trace export JSONL 可读回；
- 不破坏原 graph / planner / executor 测试。

---

## 11. 推荐开发阶段

### 阶段 1：契约对齐，不接模型

目标：

```text
qgis TaskPlan -> GeoTool-compatible TaskPlan
GeoTool ToolCallPlan -> qgis ToolCallPlan
qgis ExecutionTrace -> GeoTool ExecutionTrace
```

交付：

```text
agent/adapters/geotool_schema_adapter.py
docs/geotool_llm_integration.md
tests/test_geotool_schema_adapter.py
```

### 阶段 2：最小 ToolCallPlan 执行

目标：

```text
TaskPlan -> ToolCallPlan -> ExecutionLoop -> ExecutionTrace
```

交付：

```text
agent/tools/tool_call_planner.py
agent/nodes/tool_call_execution_node.py
tests/test_tool_call_execution_loop_integration.py
```

### 阶段 3：Trace 反哺 GeoTool-LLM

目标：

```text
ExecutionTrace -> geotool_trace_seed.jsonl
```

交付：

```text
agent/tools/export_geotool_trace.py
exports/geotool_traces/seed.jsonl
```

### 阶段 4：接入 GeoTool-LLM endpoint

目标：

```text
ToolCallPlanner 支持 HTTP / local model backend
```

先 mock，后真实接入。

交付：

```text
agent/tools/geotool_llm_client.py
agent/tools/tool_call_planner.py
```

### 阶段 5：对比实验

目标：

```text
create_agent route vs ToolCallPlan route
```

指标：

```text
任务成功率
工具调用成功率
参数错误率
平均重试次数
trace 完整度
人工介入次数
```

---

## 12. 当前不要做

下一阶段不要优先做：

- 扩展大量 QGIS MCP 工具；
- 完整 Web UI；
- 复杂 Chainlit / Streamlit 页面；
- 替换所有 executor；
- 强行依赖 GeoTool-LLM 仓库；
- 直接接入未训练模型；
- 删除 create_agent 路线；
- 做完整 RAG 重构。

原因：

> 当前核心问题是显式 ToolCallPlan 执行链尚未转正，应先让系统能消费和导出 GeoTool-compatible 结构化数据。

---

## 13. Codex 首轮任务清单

请 Codex 按顺序实现：

1. 新增 `docs/geotool_llm_integration.md`，记录 qgis schema 与 GeoTool schema 映射。
2. 新增 `agent/adapters/geotool_schema_adapter.py`。
3. 为 adapter 添加测试。
4. 新增 `agent/tools/tool_call_planner.py`，实现 rule-based MVP。
5. 为 ToolCallPlanner 添加测试。
6. 新增 `agent/nodes/tool_call_execution_node.py` 或先实现可测试 helper。
7. 用 mock MCP 跑通 `ToolCallPlan -> ExecutionLoop`。
8. 新增 `agent/tools/export_geotool_trace.py`。
9. 添加 3 条 success trace 与 2 条 failed/repaired trace 的导出测试。
10. 添加执行模式配置：
    ```text
    react_agent
    tool_call_plan
    ```
11. 确保：
    ```powershell
    uv run pytest -q
    uv run ruff check .
    ```
    通过。

---

## 14. 完成后的阶段成果表述

完成 Q0 后，可以这样描述：

```text
qgis-workflow-agent 完成了与 GeoTool-LLM 的契约对齐和显式工具调用执行链改造：新增 GeoTool schema adapter，将内部 TaskPlan、ToolCallPlan 与 ExecutionTrace 转换为 GeoTool-compatible JSON；实现 rule-based ToolCallPlanner MVP，并通过 ToolCallPlan -> ToolPolicy -> ExecutionLoop 的路径跑通最小 QGIS 工具执行闭环；支持导出成功与失败 trace，为 GeoTool-LLM 后续 SFT/DPO 数据构建提供真实执行样本。
```

---

## 15. 与 GeoTool-LLM 的协同验收

两个项目下一阶段共同验收如下：

```text
GeoTool-LLM:
  schemas + registry seed + validators + tests 通过

qgis-workflow-agent:
  adapter + rule-based ToolCallPlanner + ExecutionLoop integration + trace export 通过

共同闭环:
  qgis user task
    -> qgis TaskPlan
    -> GeoTool-compatible TaskPlan
    -> ToolCallPlan
    -> ExecutionLoop
    -> GeoTool-compatible ExecutionTrace JSONL
    -> GeoTool-LLM 可读取为 seed trace
```

一旦这个闭环稳定，再进入 GeoTool-LLM 的 SFT 数据生成和 Qwen3-1.7B LoRA 训练。
