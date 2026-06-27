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

// 计划步骤
export interface PlanStep {
  step_id: string
  description: string
  gdal_api?: string[]
  pyqgis_api?: string[]
}

// 计划（执行计划）
export interface Plan {
  task: string
  steps: PlanStep[]
  metadata?: Record<string, any>
}

// API 请求类型
export interface ChatRequest {
  message: string
  thread_id?: string
  files?: string[]
  llm_config?: LLMConfig
}

export interface LLMConfig {
  api_base?: string
  api_key?: string
  model?: string
  temperature?: number
  max_tokens?: number
}

export interface ReviewRequest {
  thread_id: string
  approved: boolean
  advise?: string
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

// 上传文件
export interface UploadedFile {
  filename: string
  file_path: string
  size: number
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
