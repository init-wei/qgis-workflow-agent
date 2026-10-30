import axios from 'axios'
import type {
  Session,
  Message,
  UploadedFile,
  ChatRequest,
  ReviewRequest,
  ExecutionResponse,
  CurrentStateResponse,
  QGISIdentity,
  SessionHistory,
  QGISStatus,
  DemoCaseDetail,
  DemoCaseListResponse,
  DemoCaseTraceResponse
} from '../types'

// 配置基础URL - 开发环境使用代理，生产环境使用真实地址
const DEFAULT_API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'
const apiUrl = (path: string) => `${DEFAULT_API_BASE.replace(/\/$/, '')}${path}`

// 创建 axios 实例
const api = axios.create({
  baseURL: DEFAULT_API_BASE,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response.data
  },
  (error) => {
    console.error('API Error:', error)
    return Promise.reject(error.response?.data || error.message)
  }
)

// ========== API 接口 ==========

/**
 * 获取 API 信息
 */
export const getApiInfo = () => {
  return api.get('/info')
}

/**
 * 健康检查
 */
export const healthCheck = () => {
  return api.get('/health')
}

/**
 * 获取 QGIS 插件状态
 */
export const getQGISStatus = async (): Promise<QGISStatus> => {
  const response = await axios.get<QGISStatus>(apiUrl('/qgis/status'))
  return response.data
}

/**
 * 创建新会话
 */
export const createSession = (session_name?: string): Promise<Session> => {
  return api.post('/sessions', { session_name })
}

/**
 * 获取会话列表
 */
export const getSessions = (): Promise<Session[]> => {
  return api.get('/sessions')
}

/**
 * 获取会话历史
 */
export const getSessionHistory = (thread_id: string): Promise<SessionHistory> => {
  return api.get(`/sessions/${thread_id}/history`)
}

/**
 * 删除会话
 */
export const deleteSession = (thread_id: string): Promise<any> => {
  return api.delete(`/sessions/${thread_id}`)
}

/**
 * 流式聊天 - 返回 Response 对象以便处理 SSE
 */
export const chatStream = async (request: ChatRequest, signal?: AbortSignal): Promise<Response> => {
  const response = await fetch(apiUrl('/chat/stream'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(request),
    signal
  })
  return response
}

/**
 * 恢复执行 - 返回 Response 对象以便处理 SSE
 */
export const resumeExecution = async (thread_id: string, signal?: AbortSignal): Promise<Response> => {
  const response = await fetch(apiUrl(`/resume/${encodeURIComponent(thread_id)}`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    signal
  })
  return response
}

/**
 * 人工审核
 */
export const reviewPlan = (request: ReviewRequest): Promise<unknown> => {
  return api.post('/review', {
    thread_id: request.thread_id,
    task_id: request.task_id,
    revision: request.revision,
    approved: request.approved,
    advise: request.advise
  })
}

export const getExecution = (task_id: string): Promise<ExecutionResponse> => {
  return api.get(`/executions/${encodeURIComponent(task_id)}`)
}

export const probeReconciliation = (taskId: string): Promise<{ task_id: string; qgis: QGISIdentity }> => {
  return api.get(`/executions/${encodeURIComponent(taskId)}/reconciliation/probe`)
}

export const reconcileExecution = (taskId: string, threadId: string, note: string, qgis: QGISIdentity): Promise<unknown> => {
  return api.post(`/executions/${encodeURIComponent(taskId)}/reconciliation`, {
    thread_id: threadId, checked: true, note, qgis
  })
}

/**
 * 确认执行结果
 */
export const confirmResult = (
  thread_id: string,
  task_id: string,
  revision: number,
  review_id: string,
  is_completed: boolean,
  user_feedback?: string
): Promise<unknown> => {
  return api.post('/confirm-result', {
    thread_id,
    task_id,
    revision,
    review_id,
    is_completed,
    user_feedback
  })
}

export const submitRiskReview = (
  thread_id: string,
  task_id: string,
  revision: number,
  review_id: string,
  status: 'approved' | 'rejected' | 'modified',
  comment?: string,
  modified_arguments?: Record<string, any>,
  reviewer = 'web-ui'
): Promise<any> => {
  return api.post('/risk-review', {
    thread_id,
    task_id,
    revision,
    review_id,
    status,
    comment,
    modified_arguments,
    reviewer
  })
}

/**
 * 上传文件
 */
export const uploadFile = async (file: File): Promise<UploadedFile> => {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(apiUrl('/upload'), {
    method: 'POST',
    body: formData
  })
  if (!response.ok) {
    throw new Error(`文件上传失败（HTTP ${response.status}）`)
  }
  return response.json() as Promise<UploadedFile>
}

/**
 * 获取文件列表
 */
export const getFiles = (): Promise<{ files: UploadedFile[] }> => {
  return api.get('/files')
}

/**
 * 获取当前状态
 */
export const getCurrentState = async (thread_id: string): Promise<CurrentStateResponse> => {
  return api.get(`/state/${encodeURIComponent(thread_id)}`)
}

export const getDemoCases = (): Promise<DemoCaseListResponse> => {
  return api.get('/demo-cases')
}

export const getDemoCase = (case_id: string): Promise<DemoCaseDetail> => {
  return api.get(`/demo-cases/${case_id}`)
}

export const getDemoCaseTrace = (
  case_id: string
): Promise<DemoCaseTraceResponse> => {
  return api.get(`/demo-cases/${case_id}/trace`)
}

export default api
