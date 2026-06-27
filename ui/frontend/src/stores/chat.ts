import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Session, Message, UploadedFile, Plan } from '../types'

export const useChatStore = defineStore('chat', () => {
  // 状态
  const currentThreadId = ref<string | null>(null)
  const sessions = ref<Session[]>([])
  const messages = ref<Message[]>([])
  const uploadedFiles = ref<UploadedFile[]>([])
  const isExecuting = ref(false)
  const currentDraft = ref<Plan | null>(null)
  const currentExecutionResult = ref<any>(null)
  const qgisStatus = ref<'running' | 'error' | 'unknown'>('unknown')
  const qgisToolsCount = ref(0)
  const screenshotPath = ref<string | null>(null)

  // 计算属性
  const hasActiveSession = computed(() => !!currentThreadId.value)
  const currentSession = computed(() => {
    return sessions.value.find(s => s.thread_id === currentThreadId.value)
  })

  // Actions
  function setThreadId(threadId: string) {
    currentThreadId.value = threadId
  }

  function setSessions(newSessions: Session[]) {
    sessions.value = newSessions
  }

  function addSession(session: Session) {
    sessions.value.unshift(session)
  }

  function removeSession(threadId: string) {
    const index = sessions.value.findIndex(s => s.thread_id === threadId)
    if (index !== -1) {
      sessions.value.splice(index, 1)
    }
    if (currentThreadId.value === threadId) {
      currentThreadId.value = null
      clearMessages()
    }
  }

  function clearMessages() {
    messages.value = []
  }

  function addUserMessage(content: string) {
    messages.value.push({
      role: 'user',
      content,
      timestamp: new Date().toISOString()
    })
  }

  function addAssistantMessage(content: string) {
    messages.value.push({
      role: 'assistant',
      content,
      timestamp: new Date().toISOString()
    })
  }

  function addSystemMessage(content: string) {
    messages.value.push({
      role: 'system',
      content,
      timestamp: new Date().toISOString()
    })
  }

  // ========== 思考消息相关方法 ==========

  // 添加思考消息（用于显示模型推理过程）
  function addThinkingMessage(content: string) {
    messages.value.push({
      role: 'thinking',
      content,
      timestamp: new Date().toISOString(),
      isCollapsible: true  // 思考内容可以折叠
    })
  }

  // 追加思考内容（用于流式思考）
  function appendThinkingMessage(content: string) {
    if (!content) return
    
    const lastMessage = messages.value[messages.value.length - 1]
    if (lastMessage?.role === 'thinking' && lastMessage.content === '') {
      // 占位符，直接设置
      lastMessage.content = content
    } else if (lastMessage?.role === 'thinking') {
      // 追加到现有思考消息
      lastMessage.content += content
    } else {
      // 创建新的思考消息
      addThinkingMessage(content)
    }
  }

  // 开始新的思考消息
  function startThinkingMessage() {
    messages.value.push({
      role: 'thinking',
      content: '',
      timestamp: new Date().toISOString(),
      isCollapsible: true
    })
  }

  // ========== 助手消息相关方法 ==========

  // 追加到最后一条助手消息（用于流式输出）
  function appendAssistantMessage(content: string) {
    if (!content) return
    
    const lastMessage = messages.value[messages.value.length - 1]
    if (lastMessage && lastMessage.role === 'assistant' && lastMessage.content === '') {
      // 最后一条是空的助手消息（刚刚创建的占位符），直接设置内容
      lastMessage.content = content
    } else if (lastMessage && lastMessage.role === 'assistant') {
      // 追加到现有助手消息
      lastMessage.content += content
    } else {
      // 最后一条不是助手消息，创建新的
      addAssistantMessage(content)
    }
  }

  // 开始新的流式消息（创建占位符）
  function startStreamingMessage() {
    messages.value.push({
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString()
    })
  }

  function setExecuting(value: boolean) {
    isExecuting.value = value
  }

  function setCurrentDraft(draft: Plan | null) {
    currentDraft.value = draft
  }

  function setExecutionResult(result: any) {
    currentExecutionResult.value = result
  }

  function addUploadedFile(file: UploadedFile) {
    uploadedFiles.value.push(file)
  }

  function removeUploadedFile(index: number) {
    uploadedFiles.value.splice(index, 1)
  }

  function clearUploadedFiles() {
    uploadedFiles.value = []
  }

  function setQGISStatus(status: 'running' | 'error' | 'unknown') {
    qgisStatus.value = status
  }

  function setQGISToolsCount(count: number) {
    qgisToolsCount.value = count
  }

  function setScreenshotPath(path: string | null) {
    screenshotPath.value = path
  }

  return {
    // 状态
    currentThreadId,
    sessions,
    messages,
    uploadedFiles,
    isExecuting,
    currentDraft,
    currentExecutionResult,
    qgisStatus,
    qgisToolsCount,
    screenshotPath,
    // 计算属性
    hasActiveSession,
    currentSession,
    // Actions
    setThreadId,
    setSessions,
    addSession,
    removeSession,
    clearMessages,
    addUserMessage,
    addAssistantMessage,
    appendAssistantMessage,
    startStreamingMessage,
    addSystemMessage,
    addThinkingMessage,
    appendThinkingMessage,
    startThinkingMessage,
    setExecuting,
    setCurrentDraft,
    setExecutionResult,
    addUploadedFile,
    removeUploadedFile,
    clearUploadedFiles,
    setQGISStatus,
    setQGISToolsCount,
    setScreenshotPath
  }
})
