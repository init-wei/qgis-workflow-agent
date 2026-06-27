import axios from 'axios'
import type { Session, Message, Plan, UploadedFile, SessionHistory, QGISStatus } from '../types'

// 配置基础URL - 开发环境使用代理，生产环境使用真实地址
const DEFAULT_API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

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
  const response = await api.get('/qgis/status')
  return response
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
export const chatStream = async (message: string, thread_id?: string, files?: string[]): Promise<Response> => {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      message,
      thread_id,
      files: files || []
    })
  })
  return response
}

/**
 * 恢复执行 - 返回 Response 对象以便处理 SSE
 */
export const resumeExecution = async (thread_id: string): Promise<Response> => {
  const response = await fetch(`${API_BASE}/resume/${thread_id}`, {
    method: 'POST'
  })
  return response
}

/**
 * 人工审核
 */
export const reviewPlan = (thread_id: string, approved: boolean, advise?: string): Promise<any> => {
  return api.post('/review', {
    thread_id,
    approved,
    advise
  })
}

/**
 * 确认执行结果
 */
export const confirmResult = (
  thread_id: string,
  is_completed: boolean,
  user_feedback?: string
): Promise<any> => {
  return api.post('/confirm-result', {
    thread_id,
    is_completed,
    user_feedback
  })
}

/**
 * 上传文件
 */
export const uploadFile = async (file: File): Promise<UploadedFile> => {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${DEFAULT_API_BASE}/upload`, {
    method: 'POST',
    body: formData
  })
  return response.json()
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
export const getCurrentState = async (thread_id: string): Promise<any> => {
  return api.get(`/state/${thread_id}`)
}

export default api
