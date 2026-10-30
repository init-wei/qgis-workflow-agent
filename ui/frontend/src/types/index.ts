// 会话类型
export interface Session {
  thread_id: string
  name: string
  created_at: string
}

// 聊天消息类型
export interface Message {
  role: 'user' | 'assistant' | 'system' | 'thinking'
  content: string
  timestamp?: string
  isCollapsible?: boolean  // 是否可折叠（用于思考内容）
}

export type RiskLevel = 'low' | 'medium' | 'high'

export interface TaskPlanInput {
  input_id: string
  description: string
  file_id?: string | null
  layer_name?: string | null
  source?: string | null
  path?: string | null
}

export interface TaskPlanOutput {
  output_id: string
  description: string
  path?: string | null
}

export interface TaskPlanStep {
  step_id: number | string
  description: string
  step_type: string
  candidate_gdal_apis: string[]
  candidate_pyqgis_apis: string[]
  allowed_tools: string[]
  input_refs: string[]
  output_refs: string[]
  risk_level: RiskLevel
  requires_review: boolean
}

export interface TaskPlanToolCall {
  step_id: number | string
  tool_name: string
  arguments: Record<string, unknown>
  risk_level: RiskLevel
  requires_review: boolean
  metadata?: Record<string, unknown>
}

/** The single reviewable plan contract returned by the backend. */
export interface TaskPlan {
  task_id: string
  revision: number
  review_status: 'draft' | 'pending_review' | 'approved' | 'rejected'
  task_summary?: string
  required_inputs: TaskPlanInput[]
  expected_outputs: TaskPlanOutput[]
  steps: TaskPlanStep[]
  tool_calls: TaskPlanToolCall[]
  risk_level: RiskLevel
  metadata: Record<string, unknown>
}

// API 请求类型
export interface ChatRequest {
  message: string
  thread_id: string
  files: Array<string | FileReference>
}

export interface ReviewRequest {
  thread_id: string
  task_id: string
  revision: number
  approved: boolean
  advise?: string
}

export interface FileReference {
  file_id: string
  layer_name?: string
}

export interface ResultConfirmRequest {
  thread_id: string
  is_completed: boolean
  user_feedback?: string
}

export interface SessionCreateRequest {
  session_name?: string
}

// SSE 事件类型
export interface SSEEvent {
  event: string
  data: any
  timestamp: string
}

// QGIS 插件状态
export interface QGISStatus {
  status: 'running' | 'error'
  tools_count: number
  timestamp: string
}

export interface LayerInfo {
  layer_name?: string
  name?: string
  source?: string | null
  crs?: string | null
  geometry?: string | null
  feature_count?: number | null
  fields?: unknown[]
}

// A file is referenced by its server-issued ID; paths stay on the backend.
export interface UploadedFile {
  file_id: string
  filename: string
  size: number
  sha256: string
  layer_info?: LayerInfo | null
  layers?: LayerInfo[]
}

export interface Execution {
  task_id: string
  session_id: string
  plan_revision: number
  status: string
  plan: TaskPlan
  attempts: unknown[]
  artifacts: unknown[]
  confirmed: boolean | null
  summary: string | null
}

export interface ExecutionResponse {
  execution: Execution
}

export interface CurrentStateResponse {
  thread_id: string
  state: {
    task_plan?: TaskPlan | null
    task_plan_draft?: TaskPlan | null
    [key: string]: unknown
  }
  next?: string[]
}

// 会话历史
export interface SessionHistory {
  thread_id: string
  input_query: string
  chat_messages: Message[]
  final_summary?: string
  screenshot_path?: string
  logs: any[]
  log_summary?: string
  history: any[]
}

// 节点执行状态
export interface NodeExecution {
  node: string
  timestamp: string
}

export interface DemoCaseSummary {
  case_id: string
  title: string
  summary: string
  tags: string[]
  trace_id: string
  trace_status: string
  attempts_used: number
  artifact_count: number
  has_repair: boolean
  has_skill_draft: boolean
  has_registered_skill: boolean
}

export interface DemoReplayResult {
  case_id: string
  status: string
  attempts_used: number
  artifact_count: number
  repair_summaries: Record<string, any>[]
  skill_draft?: Record<string, any> | null
  registered_skill?: Record<string, any> | null
  skill_execution_plan?: Record<string, any> | null
  reuse_preview?: Record<string, any> | null
  trace_summary: Record<string, any>
}

export interface DemoCaseDetail {
  case: DemoCaseSummary
  replay: DemoReplayResult
}

export interface DemoCaseListResponse {
  cases: DemoCaseSummary[]
}

export interface DemoCaseTraceResponse {
  case_id: string
  trace: Record<string, any>
}
