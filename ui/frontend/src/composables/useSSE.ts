import { ref, onUnmounted } from 'vue'
import { chatStream, resumeExecution } from '../api'
import type { ChatRequest } from '../types'

export type SSEEventHandler = (event: string, data: unknown) => void

interface UseSSEReturn {
  isStreaming: ReturnType<typeof ref<boolean>>
  startStreaming: (request: ChatRequest) => Promise<void>
  resumeStreaming: (threadId: string) => Promise<void>
  stopStreaming: () => void
  error: ReturnType<typeof ref<Error | null>>
}

export function useSSE(
  onEvent: SSEEventHandler,
  onComplete?: () => void,
  onError?: (error: Error) => void
): UseSSEReturn {
  const isStreaming = ref(false)
  const error = ref<Error | null>(null)
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  let controller: AbortController | null = null

  const processSSEStream = async (response: Response) => {
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }
    if (!response.body) {
      throw new Error('SSE 响应没有可读取的数据流')
    }

    reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEventType = 'message'
    const dataLines: string[] = []

    const dispatchEvent = () => {
      if (dataLines.length === 0) {
        currentEventType = 'message'
        return
      }

      try {
        const data: unknown = JSON.parse(dataLines.join('\n'))
        onEvent(currentEventType, data)
        if (currentEventType === 'complete') {
          onComplete?.()
        } else if (currentEventType === 'error') {
          const message = readStringProperty(data, 'message') || '未知错误'
          const streamError = new Error(message)
          error.value = streamError
          onError?.(streamError)
        }
      } catch (cause) {
        if (cause instanceof SyntaxError) {
          console.error('[SSE] 无法解析事件数据')
        } else {
          throw cause
        }
      } finally {
        dataLines.length = 0
        currentEventType = 'message'
      }
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, '')
          if (line === '') {
            dispatchEvent()
          } else if (line.startsWith('event:')) {
            currentEventType = line.slice(6).trim() || 'message'
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).replace(/^ /, ''))
          }
        }
      }

      buffer += decoder.decode()
      if (buffer) {
        for (const rawLine of buffer.split('\n')) {
          const line = rawLine.replace(/\r$/, '')
          if (line.startsWith('event:')) {
            currentEventType = line.slice(6).trim() || 'message'
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).replace(/^ /, ''))
          }
        }
      }
      dispatchEvent()
    } finally {
      isStreaming.value = false
      cleanup()
    }
  }

  const consume = async (startRequest: (signal: AbortSignal) => Promise<Response>) => {
    cleanup()
    isStreaming.value = true
    error.value = null
    controller = new AbortController()

    try {
      const response = await startRequest(controller.signal)
      await processSSEStream(response)
    } catch (cause) {
      if (cause instanceof Error && cause.name === 'AbortError') {
        return
      }
      const streamError = cause instanceof Error ? cause : new Error(String(cause))
      error.value = streamError
      onError?.(streamError)
      cleanup()
    }
  }

  const startStreaming = (request: ChatRequest) => consume(signal => chatStream(request, signal))

  const resumeStreaming = (threadId: string) => consume(signal => resumeExecution(threadId, signal))

  const stopStreaming = () => {
    cleanup()
    isStreaming.value = false
  }

  const cleanup = () => {
    if (controller) {
      controller.abort()
      controller = null
    }
    if (reader) {
      void reader.cancel()
      reader = null
    }
    isStreaming.value = false
  }

  onUnmounted(cleanup)

  return {
    isStreaming,
    startStreaming,
    resumeStreaming,
    stopStreaming,
    error
  }
}

function readStringProperty(value: unknown, key: string): string | undefined {
  if (typeof value !== 'object' || value === null || !(key in value)) return undefined
  const property = (value as Record<string, unknown>)[key]
  return typeof property === 'string' ? property : undefined
}

export function getNodeDisplayName(node: string): string {
  const names: Record<string, string> = {
    planner_node: '📋 规划器',
    human_review_node: '👤 人工审核',
    risk_review_node: '🛡️ 风险审核',
    executor_node: '⚙️ 受控执行',
    reflector_node: '🔍 反思器'
  }
  return names[node] || node
}

export function formatTime(isoString: string): string {
  const date = new Date(isoString)
  const now = new Date()
  const diff = now.getTime() - date.getTime()

  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前'
  return date.toLocaleDateString()
}
