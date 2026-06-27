import { ref, onUnmounted } from 'vue'
import type { Plan } from '../types'

/**
 * SSE 事件类型
 */
export type SSEEventType =
  | 'start'
  | 'node_start'
  | 'node_end'
  | 'thinking'
  | 'output'
  | 'message_delta'
  | 'human_review_required'
  | 'result_review_required'
  | 'complete'
  | 'error'
  | 'paused'

/**
 * SSE 事件回调函数类型
 */
export type SSEEventHandler = (event: string, data: any) => void

/**
 * SSE Hook 返回值
 */
interface UseSSEReturn {
  isStreaming: ReturnType<typeof ref<boolean>>
  startStreaming: (message: string, threadId?: string, files?: string[]) => Promise<void>
  resumeStreaming: (threadId: string) => Promise<void>
  stopStreaming: () => void
  error: ReturnType<typeof ref<Error | null>>
}

/**
 * SSE Composable - 处理服务器推送事件
 */
export function useSSE(
  onEvent: SSEEventHandler,
  onComplete?: () => void,
  onError?: (error: Error) => void
): UseSSEReturn {
  const isStreaming = ref(false)
  const error = ref<Error | null>(null)
  let reader: ReadableStreamDefaultReader | null = null
  let controller: AbortController | null = null

  /**
   * 处理 SSE 流
   */
  const processSSEStream = async (response: Response) => {
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    reader = response.body?.getReader() || null
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEventType = ''

    try {
      while (true) {
        const { done, value } = await reader!.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmedLine = line.trim()
          if (!trimmedLine) continue

          if (trimmedLine.startsWith('event:')) {
            currentEventType = trimmedLine.slice(6).trim()
          } else if (trimmedLine.startsWith('data:')) {
            try {
              const data = JSON.parse(trimmedLine.slice(5).trim())
              console.log('[SSE] 事件:', currentEventType, '数据:', data)
              onEvent(currentEventType, data)

              // 特殊事件处理
              if (currentEventType === 'complete') {
                onComplete?.()
              } else if (currentEventType === 'error') {
                error.value = new Error(data.message || '未知错误')
                onError?.(error.value)
              }
            } catch (e) {
              console.error('[SSE] 解析失败:', trimmedLine, e)
            }
          }
        }
      }
    } finally {
      // ✅ 显式设置流式状态为 false
      isStreaming.value = false
      cleanup()
    }
  }

  /**
   * 开始流式聊天
   */
  const startStreaming = async (message: string, threadId?: string, files?: string[]) => {
    cleanup()
    isStreaming.value = true
    error.value = null
    controller = new AbortController()

    try {
      const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

      const readNumber = (key: string) => {
        const raw = localStorage.getItem(key)
        if (raw === null || raw === '') return undefined
        const num = Number(raw)
        return Number.isFinite(num) ? num : undefined
      }

      const llmConfig = {
        api_base: localStorage.getItem('apiUrl') || undefined,
        api_key: localStorage.getItem('apiKey') || undefined,
        model: localStorage.getItem('model') || undefined,
        temperature: readNumber('temperature'),
        max_tokens: readNumber('maxTokens')
      }

      const hasLlmConfig = Object.values(llmConfig).some((v) => v !== undefined)

      const response = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message,
          thread_id: threadId,
          files: files || [],
          ...(hasLlmConfig ? { llm_config: llmConfig } : {})
        }),
        signal: controller.signal
      })

      await processSSEStream(response)
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        error.value = err
        onError?.(err)
      }
      isStreaming.value = false
    }
  }

  /**
   * 恢复执行
   */
  const resumeStreaming = async (threadId: string) => {
    cleanup()
    isStreaming.value = true
    error.value = null
    controller = new AbortController()

    try {
      const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'
      const response = await fetch(`${API_BASE}/resume/${threadId}`, {
        method: 'POST',
        signal: controller.signal
      })

      await processSSEStream(response)
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        error.value = err
        onError?.(err)
      }
      isStreaming.value = false
    }
  }

  /**
   * 停止流式传输
   */
  const stopStreaming = () => {
    cleanup()
    isStreaming.value = false
  }

  /**
   * 清理资源
   */
  const cleanup = () => {
    if (controller) {
      controller.abort()
      controller = null
    }
    if (reader) {
      reader.cancel()
      reader = null
    }
    // ✅ 确保流式状态被重置
    isStreaming.value = false
  }

  // 组件卸载时清理
  onUnmounted(() => {
    cleanup()
  })

  return {
    isStreaming,
    startStreaming,
    resumeStreaming,
    stopStreaming,
    error
  }
}

/**
 * 格式化消息内容（支持 Markdown 简单格式）
 */
export function formatMessageContent(content: string): string {
  return content
    .replace(/\n/g, '<br>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
}

/**
 * 获取节点显示名称
 */
export function getNodeDisplayName(node: string): string {
  const names: Record<string, string> = {
    'planner_node': '📋 规划器',
    'human_review_node': '👤 人工审核',
    'api_rag_node': '📚 API 检索',
    'executor_node': '⚙️ 执行器',
    'reflector_node': '🔍 反思器'
  }
  return names[node] || node
}

/**
 * 格式化时间
 */
export function formatTime(isoString: string): string {
  const date = new Date(isoString)
  const now = new Date()
  const diff = now.getTime() - date.getTime()

  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前'
  return date.toLocaleDateString()
}
