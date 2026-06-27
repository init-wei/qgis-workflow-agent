<template>
  <div class="flex h-screen bg-dark-bg text-dark-text overflow-hidden">
    <!-- 左侧边栏 -->
    <aside class="w-80 flex-shrink-0 bg-dark-bgDark border-r border-dark-border flex flex-col">
      <!-- 头部 -->
      <div class="p-5 border-b border-dark-border flex items-start justify-between gap-3">
        <div>
          <h1 class="text-xl font-semibold text-primary-600">QGIS Agent</h1>
          <p class="text-sm text-dark-textSecondary mt-1">LangGraph Web UI</p>
        </div>
        <el-button
          type="default"
          :icon="Setting"
          circle
          size="small"
          @click="handleOpenSettings"
        />
      </div>

      <!-- QGIS 插件状态 -->
      <div class="mx-4 mt-4 p-3 bg-dark-bgLight rounded-xl border border-dark-border shadow-md">
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-sm font-semibold">QGIS 插件状态</h3>
          <div 
            class="w-2.5 h-2.5 rounded-full"
            :class="{
              'bg-green-500 animate-pulse': qgisStatus === 'running',
              'bg-red-500': qgisStatus === 'error',
              'bg-gray-500': qgisStatus === 'unknown'
            }"
          ></div>
        </div>
        <div class="text-base font-semibold text-primary-600">
          {{ qgisStatusText }}
        </div>
        <div class="text-xs text-dark-textSecondary">
          {{ qgisStatusDetail }}
        </div>
      </div>

      <!-- 新建会话按钮 -->
      <el-button
        type="primary"
        class="mx-4 mt-4 mb-2"
        style="width: calc(100% - 2rem)"
        @click="handleCreateSession"
      >
        + 新建会话
      </el-button>

      <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto px-4 min-h-0">
        <div
          v-for="session in sessions"
          :key="session.thread_id"
          class="session-item mb-2 p-3 bg-dark-bgLight rounded-lg cursor-pointer transition-all hover:bg-dark-border flex items-center justify-between gap-2"
          :class="{
            'bg-primary-600 text-white hover:bg-primary-700': currentThreadId === session.thread_id,
            'hover:bg-dark-border': currentThreadId !== session.thread_id
          }"
          @click="handleSwitchSession(session.thread_id)"
        >
          <div class="flex-1 min-w-0">
            <div class="text-sm font-medium truncate">{{ session.name }}</div>
            <div
              class="text-xs mt-0.5"
              :class="currentThreadId === session.thread_id ? 'text-white/80' : 'text-dark-textSecondary'"
            >{{ formatTime(session.created_at) }}</div>
          </div>
          <el-button
            :type="currentThreadId === session.thread_id ? 'default' : 'danger'"
            :icon="Delete"
            circle
            size="small"
            @click.stop="handleDeleteSession(session.thread_id)"
          />
        </div>
      </div>

      <!-- 文件上传区 -->
      <div class="p-4 border-t border-dark-border flex-shrink-0">
        <div
          class="upload-area border-2 border-dashed border-dark-border rounded-lg p-4 text-center cursor-pointer transition-all hover:border-primary-600 hover:bg-dark-bgLight"
          :class="{ 'border-primary-600 bg-primary-600/10': isDragOver }"
          @click="handleUploadClick"
          @dragover.prevent="handleDragOver"
          @dragleave="handleDragLeave"
          @drop.prevent="handleDrop"
        >
          <div class="text-2xl mb-2">📁</div>
          <div class="text-sm text-dark-textSecondary">点击或拖拽上传文件</div>
          <input
            ref="fileInputRef"
            type="file"
            multiple
            class="hidden"
            @change="handleFileUpload"
          />
        </div>
        <div class="mt-2.5">
          <div
            v-for="(file, index) in uploadedFiles"
            :key="index"
            class="file-item flex items-center justify-between p-2 bg-dark-bgLight rounded mb-1"
          >
            <span class="text-xs flex-1 truncate" :title="file.filename">{{ file.filename }}</span>
            <el-button
              type="danger"
              size="small"
              :icon="Close"
              circle
              @click="handleRemoveFile(index)"
            />
          </div>
        </div>
      </div>
    </aside>

    <!-- 主内容区 -->
    <main class="flex-1 flex flex-col relative min-w-0 px-6 py-4">
      <!-- 截图预览面板 -->
      <div class="fixed bottom-6 right-6 w-[32rem] bg-dark-bgDark border border-dark-border rounded-2xl p-4 shadow-xl z-10">
        <div class="flex items-center justify-between mb-2 text-xs text-dark-textSecondary">
          <span>🖼️ 最新截图</span>
          <span>{{ screenshotTime }}</span>
        </div>
        <div class="preview w-full h-72 bg-dark-bgLight rounded-xl overflow-hidden flex items-center justify-center text-xs text-dark-textSecondary">
          <img
            v-if="screenshotUrl"
            :src="screenshotUrl"
            alt="QGIS截图预览"
            class="w-full h-full object-cover"
          />
          <span v-else>暂无截图</span>
        </div>
      </div>

      <!-- 聊天区域 -->
      <div class="chat-container flex-1 flex flex-col overflow-hidden min-h-0 bg-dark-bgDark border border-dark-border rounded-2xl shadow-xl">
        <div class="chat-messages flex-1 overflow-y-auto p-6 scroll-smooth">
          <!-- 欢迎消息 -->
          <div
            v-if="messages.length === 0"
            class="message flex gap-3 mb-6"
          >
            <div class="avatar w-9 h-9 rounded-full bg-primary-600 flex items-center justify-center font-bold flex-shrink-0">AI</div>
            <div class="content max-w-[70%] p-3 rounded-xl bg-dark-bgLight rounded-tl-none">
              你好！我是 QGIS Agent，专注于地理数据处理。我可以帮你：
              <ul class="list-disc list-inside mt-2 space-y-1">
                <li>规划 QGIS 工作流程</li>
                <li>检索 GDAL/PyQGIS API 文档</li>
                <li>生成并执行 Python 代码</li>
              </ul>
              请告诉我你需要什么帮助？
            </div>
          </div>

          <!-- 消息列表 -->
          <div
            v-for="(msg, index) in messages"
            :key="index"
            class="message mb-6"
            :class="{
              'flex gap-3': msg.role !== 'thinking',
              'flex-col items-center': msg.role === 'thinking'
            }"
          >
            <!-- 思考消息 -->
            <template v-if="msg.role === 'thinking'">
              <div
                class="thinking-message w-full max-w-[85%] mx-auto border-l-4 border-yellow-500/70 bg-yellow-500/5 dark:bg-yellow-500/10 rounded-r-lg p-4"
              >
                <div
                  class="thinking-header flex items-center gap-2 cursor-pointer mb-2 select-none"
                  @click="toggleThinkingCollapse(index)"
                >
                  <span class="text-yellow-600 dark:text-yellow-400 font-semibold text-sm">
                    💭 思考过程
                  </span>
                  <span class="text-yellow-600/70 dark:text-yellow-400/70 text-xs">
                    {{ thinkingCollapsed[index] ? '展开' : '收起' }}
                  </span>
                </div>
                <div
                  class="thinking-content text-sm leading-relaxed whitespace-pre-wrap"
                  :class="{ 'line-clamp-3': thinkingCollapsed[index] }"
                  v-html="formatMessageContent(msg.content)"
                ></div>
              </div>
            </template>

            <!-- 非思考消息 -->
            <template v-else>
              <div
                class="flex gap-3"
                :class="{ 'flex-row-reverse': msg.role === 'user' }"
              >
                <!-- 系统消息不显示头像 -->
                <div
                  v-if="msg.role !== 'system'"
                  class="avatar w-9 h-9 rounded-full flex items-center justify-center font-bold flex-shrink-0"
                  :class="{
                    'bg-primary-600': msg.role === 'assistant',
                    'bg-green-500': msg.role === 'user',
                    'bg-gray-500': msg.role === 'system'
                  }"
                >
                  {{ msg.role === 'user' ? '👤' : msg.role === 'system' ? 'ℹ️' : 'AI' }}
                </div>
                <div
                  class="content bubble p-3 rounded-xl leading-relaxed"
                  :class="{
                    'max-w-[70%] bg-dark-bgLight rounded-tl-none': msg.role === 'assistant',
                    'max-w-[70%] bg-primary-600 rounded-tr-none text-white': msg.role === 'user',
                    'max-w-[80%] bg-dark-bgLight/50 border border-dark-border text-center text-sm system-bubble': msg.role === 'system'
                  }"
                  v-html="formatMessageContent(msg.content)"
                ></div>
              </div>
            </template>
          </div>
        </div>

        <!-- 输入区域 -->
        <div class="chat-input p-5 border-t border-dark-border flex-shrink-0 bg-dark-bgDark/80">
          <div class="input-wrapper flex gap-2.5 bg-dark-bgLight/80 border border-dark-border rounded-xl p-2.5 items-end">
            <el-input
              ref="messageInputRef"
              v-model="inputMessage"
              type="textarea"
              :rows="1"
              :autosize="{ minRows: 1, maxRows: 4 }"
              placeholder="输入你的地理处理需求..."
              :disabled="isExecuting"
              @keydown="handleKeyDown"
              @input="handleInputResize"
              class="flex-1"
            />
            <el-button
              type="primary"
              :icon="Promotion"
              circle
              :disabled="!inputMessage.trim() || isExecuting"
              @click="handleSendMessage"
            />
          </div>
        </div>
      </div>
    </main>

    <!-- 人工审核弹窗 -->
    <el-dialog
      v-model="reviewModalVisible"
      title="⚠️ 人工审核"
      width="600px"
      :close-on-click-modal="false"
    >
      <div v-if="currentDraft">
        <div class="plan-summary bg-dark-bgLight p-4 rounded-lg mb-4">
          <h3 class="font-semibold text-primary-600 mb-2">📋 执行计划</h3>
          <p>{{ currentDraft.task }}</p>
        </div>
        <h4 class="font-semibold mb-2">执行步骤 ({{ currentDraft.steps.length }}):</h4>
        <ul class="steps-list space-y-2">
          <li
            v-for="step in currentDraft.steps"
            :key="step.step_id"
            class="step p-2.5 bg-dark-bg rounded-lg border-l-4 border-primary-600"
          >
            <div class="font-medium">步骤 {{ step.step_id }}: {{ step.description }}</div>
            <div class="text-xs text-dark-textSecondary mt-1">
              <span v-if="step.gdal_api && step.gdal_api.length">GDAL: {{ step.gdal_api.join(', ') }}</span>
              <span v-if="step.pyqgis_api && step.pyqgis_api.length" class="ml-2">PyQGIS: {{ step.pyqgis_api.join(', ') }}</span>
            </div>
          </li>
        </ul>
        <div class="mt-4">
          <label class="block text-sm mb-2">如需修改，请输入修改意见：</label>
          <el-input
            v-model="reviewAdvise"
            type="textarea"
            :rows="3"
            placeholder="例如：步骤2应该先检查文件是否存在..."
          />
        </div>
      </div>
      <template #footer>
        <el-button @click="reviewModalVisible = false">取消</el-button>
        <el-button type="danger" @click="handleReview(false)">拒绝并修改</el-button>
        <el-button type="success" @click="handleReview(true)">批准执行</el-button>
      </template>
    </el-dialog>

    <!-- 执行结果确认弹窗 -->
    <el-dialog
      v-model="resultModalVisible"
      title="📋 确认执行结果"
      width="600px"
      :close-on-click-modal="false"
    >
      <p class="mb-4">执行节点已完成，请确认任务是否成功完成：</p>
      <div class="bg-dark-bgLight p-4 rounded-lg mb-4">
        <strong>执行摘要：</strong>
        <pre class="mt-2 text-sm whitespace-pre-wrap">{{ executionSummary }}</pre>
      </div>
      <div class="mt-4">
        <label class="block text-sm mb-2">若任务未完成，请补充失败原因（可选）：</label>
        <el-input
          v-model="resultFeedback"
          type="textarea"
          :rows="3"
          placeholder="例如：图层未加载成功、字段名不一致、结果与预期不符..."
        />
      </div>
      <p class="text-sm text-dark-textSecondary">
        Reflector 将根据您的确认生成相应的总结和经验归档。
      </p>
      <template #footer>
        <el-button type="danger" @click="handleResultConfirm(false)">❌ 任务失败</el-button>
        <el-button type="success" @click="handleResultConfirm(true)">✅ 任务成功</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, toRefs } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElInput, ElDialog, ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Close, Promotion, Setting } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chat'
import { useSSE, formatMessageContent, getNodeDisplayName, formatTime } from '../composables/useSSE'
import {
  getSessions,
  createSession,
  deleteSession as apiDeleteSession,
  getSessionHistory,
  uploadFile as apiUploadFile,
  reviewPlan,
  confirmResult,
  getQGISStatus,
  getCurrentState
} from '../api'
import type { Plan } from '../types'

const router = useRouter()
const chatStore = useChatStore()

// 组件状态
const inputMessage = ref('')
const reviewModalVisible = ref(false)
const resultModalVisible = ref(false)
const reviewAdvise = ref('')
const resultFeedback = ref('')
const isDragOver = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const screenshotTime = ref('暂无')
const pluginStatusTimer = ref<number | null>(null)
const messageInputRef = ref<any>(null) // Element Plus textarea ref

// 思考内容折叠状态
const thinkingCollapsed = ref<Record<number, boolean>>({})

// Store 引用 - 使用 toRefs 保持响应式
const {
  currentThreadId,
  sessions,
  messages,
  uploadedFiles,
  isExecuting,
  currentDraft,
  currentExecutionResult,
  qgisStatus,
  qgisToolsCount,
  screenshotPath
} = toRefs(chatStore)

// 计算属性
const qgisStatusText = computed(() => {
  const status = qgisStatus.value
  switch (status) {
    case 'running': return '插件已启动'
    case 'error': return '插件未启动'
    default: return '检测中...'
  }
})

const qgisStatusDetail = computed(() => {
  const status = qgisStatus.value
  const count = qgisToolsCount.value ?? 0
  if (status === 'running') {
    return `工具数: ${count}`
  }
  return '请检查 QGIS MCP 插件'
})

const executionSummary = computed(() => {
  const result = currentExecutionResult.value
  return result?.execution_summary || '执行完成'
})

const screenshotUrl = computed(() => {
  const path = screenshotPath.value
  if (!path) return ''
  let url = path.replace(/\\/g, '/')
  if (url.includes('shared/screenshots/')) {
    const idx = url.indexOf('shared/screenshots/')
    url = '/' + url.slice(idx)
  }
  return url
})

// SSE Hook
const { isStreaming, startStreaming, resumeStreaming, stopStreaming } = useSSE(
  handleSSEEvent,
  handleSSEComplete,
  handleSSEError
)

// ========== 生命周期 ==========

onMounted(async () => {
  await loadSessions()
  setupDragDrop()
  startPluginStatusMonitor()

  // 自动调整 textarea 高度
  // Element Plus autosize 已处理
})

onUnmounted(() => {
  if (pluginStatusTimer.value) {
    clearInterval(pluginStatusTimer.value)
  }
})

// ========== 会话管理 ==========

async function loadSessions() {
  try {
    const data = await getSessions()
    chatStore.setSessions(data)
  } catch (error) {
    console.error('加载会话失败:', error)
    ElMessage.error('加载会话失败')
  }
}

async function handleCreateSession() {
  try {
    const data = await createSession()
    chatStore.addSession(data)
    await handleSwitchSession(data.thread_id)
    ElMessage.success(`已创建新会话: ${data.name}`)
  } catch (error) {
    console.error('创建会话失败:', error)
    ElMessage.error('创建会话失败')
  }
}

function handleOpenSettings() {
  router.push('/settings')
}

async function handleSwitchSession(threadId: string) {
  // 停止之前的流式连接
  stopStreaming()

  // 重置所有状态
  resetStreamingState()
  chatStore.setExecuting(false)

  // 切换会话
  chatStore.setThreadId(threadId)
  chatStore.clearMessages()
  await loadSessionHistory(threadId)
}

async function loadSessionHistory(threadId: string) {
  try {
    const data = await getSessionHistory(threadId)
    renderHistory(data)
  } catch (error) {
    console.error('加载历史失败:', error)
    ElMessage.error('加载历史失败')
  }
}

function renderHistory(data: any) {
  chatStore.clearMessages()

  console.log('[RenderHistory] 数据:', data)
  console.log('[RenderHistory] history:', data.history)
  console.log('[RenderHistory] chat_messages:', data.chat_messages)

  // 优先使用 history 数组（包含完整的对话轮次）
  if (data.history && data.history.length > 0) {
    console.log('[RenderHistory] 使用 history，共', data.history.length, '轮')
    data.history.forEach((entry: any, index: number) => {
      // 添加轮次标题
      chatStore.addSystemMessage(`📌 第 ${index + 1} 轮对话`)

      // 用户输入
      if (entry.input_query) {
        chatStore.addUserMessage(entry.input_query)
      }

      // 执行计划
      if (entry.plan) {
        const plan = entry.plan
        let planText = `📋 **执行计划**\n\n${plan.task || ''}\n\n**执行步骤**:\n\n`
        if (Array.isArray(plan.steps) && plan.steps.length > 0) {
          plan.steps.forEach((step: any) => {
            planText += `${step.step_id}. ${step.description}\n`
            if (step.gdal_api && step.gdal_api.length) {
              planText += `   - GDAL API: ${step.gdal_api.join(', ')}\n`
            }
            if (step.pyqgis_api && step.pyqgis_api.length) {
              planText += `   - PyQGIS API: ${step.pyqgis_api.join(', ')}\n`
            }
          })
        }
        chatStore.addAssistantMessage(planText)
      }

      // 执行结果
      if (entry.executor_last_message) {
        chatStore.addAssistantMessage(`⚙️ **执行结果**\n\n${entry.executor_last_message}`)
      }

      // 最终总结
      if (entry.final_summary) {
        chatStore.addAssistantMessage(`🎉 任务完成！\n\n${entry.final_summary}`)
      }
    })
  }
  // 如果 history 不存在，使用 chat_messages 数组
  else if (data.chat_messages && data.chat_messages.length > 0) {
    console.log('[RenderHistory] 使用 chat_messages，共', data.chat_messages.length, '条消息')
    data.chat_messages.forEach((msg: any) => {
      if (msg.role === 'user') {
        chatStore.addUserMessage(msg.content)
      } else {
        chatStore.addAssistantMessage(msg.content)
      }
    })
  }
  // 后备方案：处理旧数据格式
  else {
    console.log('[RenderHistory] 使用后备方案（旧数据格式）')

    if (data.input_query) {
      chatStore.addUserMessage(data.input_query)
    }

    if (data.final_summary) {
      chatStore.addAssistantMessage(`🎉 任务完成！\n\n${data.final_summary}`)
    }

    if (data.log_summary) {
      chatStore.addAssistantMessage(`⚙️ **执行结果**\n\n${data.log_summary}`)
    }
  }

  // 显示截图（如果有）
  if (data.screenshot_path) {
    chatStore.setScreenshotPath(data.screenshot_path)
    screenshotTime.value = new Date().toLocaleTimeString()
  }

  // 移除系统消息，让历史记录更干净
  // chatStore.addSystemMessage('✅ 已加载会话历史')
}

async function handleDeleteSession(threadId: string) {
  try {
    await ElMessageBox.confirm('确认删除该会话吗？此操作不可恢复。', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    await apiDeleteSession(threadId)
    chatStore.removeSession(threadId)
    ElMessage.success('会话已删除')
  } catch (error: any) {
    if (error !== 'cancel') {
      console.error('删除会话失败:', error)
      ElMessage.error('删除会话失败')
    }
  }
}

// ========== 消息发送 ==========

async function handleSendMessage() {
  const message = inputMessage.value.trim()
  if (!message) return

  // 检查是否正在流式传输
  if (isStreaming.value) {
    console.warn('正在流式传输中，无法发送新消息')
    ElMessage.warning('请等待当前任务完成')
    return
  }

  // 重置流式状态
  resetStreamingState()

  if (!currentThreadId.value) {
    await handleCreateSession()
  }

  chatStore.addUserMessage(message)
  inputMessage.value = ''
  chatStore.setExecuting(true)

  try {
    await startStreaming(message, currentThreadId.value!, uploadedFiles.value.map(f => f.file_path))
  } catch (error) {
    console.error('发送消息失败:', error)
    ElMessage.error('发送消息失败')
    chatStore.setExecuting(false)
  }
}

function handleKeyDown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSendMessage()
  }
}

// ========== 输入框高度自动调整 ==========

function handleInputResize() {
  // Element Plus 的 autosize 属性已经处理了大部分情况
  // 这里添加额外的稳定性保证
  nextTick(() => {
    if (messageInputRef.value) {
      const textarea = messageInputRef.value.$?.querySelector('textarea')
      if (textarea) {
        // 确保最小高度
        const minHeight = 32 // 单行高度
        const currentHeight = textarea.scrollHeight
        if (currentHeight < minHeight) {
          textarea.style.height = minHeight + 'px'
        }
      }
    }
  })
}

// ========== 思考内容折叠/展开 ==========

function toggleThinkingCollapse(index: number) {
  thinkingCollapsed.value[index] = !thinkingCollapsed.value[index]
}

// ========== SSE 事件处理 ==========

// 跟踪当前是否正在接收流式消息
let isStreamingMessage = false
// 跟踪当前是否正在接收思考内容
let isStreamingThinking = false

// 重置流式状态（在发送新消息前调用）
function resetStreamingState() {
  isStreamingMessage = false
  isStreamingThinking = false
}

function handleSSEEvent(event: string, data: any) {
  switch (event) {
    case 'start':
      chatStore.addSystemMessage('🚀 ' + data.message)
      break
    case 'node_start':
      chatStore.addSystemMessage(`🔄 正在执行: ${getNodeDisplayName(data.node)}`)
      break
    case 'node_end':
      chatStore.addSystemMessage(`✅ 完成: ${getNodeDisplayName(data.node)}`)
      break
    case 'thinking':
      // 处理思考内容（reasoning_content）
      const thinkingContent = data?.content || ''
      if (thinkingContent) {
        if (!isStreamingThinking) {
          // 第一次接收思考内容，创建占位符
          chatStore.startThinkingMessage()
          isStreamingThinking = true
        }
        // 追加思考内容
        chatStore.appendThinkingMessage(thinkingContent)
      }
      break
    case 'output':
      // 处理正式输出（content）
      const outputContent = data?.content || ''
      if (outputContent) {
        if (!isStreamingMessage) {
          // 第一次接收输出，创建占位符
          chatStore.startStreamingMessage()
          isStreamingMessage = true
          isStreamingThinking = false // 思考结束
        }
        // 追加输出内容
        chatStore.appendAssistantMessage(outputContent)
      }
      break
    case 'message_delta':
      // 处理流式消息 - 追加到最后一条助手消息（保留兼容性）
      const delta = data?.content || ''
      if (delta) {
        if (!isStreamingMessage) {
          // 开始新的流式消息
          chatStore.startStreamingMessage()
          isStreamingMessage = true
        }
        // 追加内容
        chatStore.appendAssistantMessage(delta)
      }
      break
    case 'human_review_required':
      isStreamingMessage = false // 结束流式消息
      isStreamingThinking = false
      chatStore.setCurrentDraft(data.draft)
      reviewModalVisible.value = true
      chatStore.setExecuting(false)
      break
    case 'result_review_required':
      isStreamingMessage = false // 结束流式消息
      isStreamingThinking = false
      chatStore.setExecutionResult(data)
      resultFeedback.value = ''
      resultModalVisible.value = true
      chatStore.setExecuting(false)
      refreshScreenshotFromState()
      break
    case 'complete':
      isStreamingMessage = false // 结束流式消息
      isStreamingThinking = false
      const finalSummary = data.summary || ''
      if (finalSummary) {
        const lastAssistant = [...messages.value].reverse().find((msg) => msg.role === 'assistant')
        const lastContent = lastAssistant?.content || ''
        const alreadyStreamed = lastContent.trim() === finalSummary.trim()
          || lastContent.trim().endsWith(finalSummary.trim())

        if (!alreadyStreamed) {
          chatStore.addAssistantMessage(`🎉 任务完成！\n\n${finalSummary}`)
        }
      } else {
        chatStore.addSystemMessage('🎉 任务完成！')
      }
      if (data.screenshot_path) {
        chatStore.setScreenshotPath(data.screenshot_path)
        screenshotTime.value = new Date().toLocaleTimeString()
      }
      chatStore.setExecuting(false)
      break
    case 'error':
      isStreamingMessage = false // 结束流式消息
      isStreamingThinking = false
      chatStore.addAssistantMessage(`❌ 错误: ${data.message}`)
      chatStore.setExecuting(false)
      break
    case 'paused':
      isStreamingMessage = false // 结束流式消息
      isStreamingThinking = false
      chatStore.setExecuting(false)
      chatStore.addSystemMessage(`⏸️ ${data.message}`)
      break
  }
}

function handleSSEComplete() {
  // 流处理完成
}

function handleSSEError(error: Error) {
  console.error('SSE 错误:', error)
  chatStore.setExecuting(false)
}

// ========== 人工审核 ==========

async function handleReview(approved: boolean) {
  if (!currentThreadId.value) return

  try {
    await reviewPlan(currentThreadId.value, approved, approved ? null : reviewAdvise.value)

    if (approved) {
      // 显示计划内容
      if (currentDraft.value) {
        let planContent = `📋 **执行计划**\n\n${currentDraft.value.task}\n\n**执行步骤** (${currentDraft.value.steps.length}):\n\n`
        currentDraft.value.steps.forEach((step) => {
          planContent += `${step.step_id}. ${step.description}\n`
          if (step.gdal_api && step.gdal_api.length) {
            planContent += `   - GDAL API: ${step.gdal_api.join(', ')}\n`
          }
          if (step.pyqgis_api && step.pyqgis_api.length) {
            planContent += `   - PyQGIS API: ${step.pyqgis_api.join(', ')}\n`
          }
        })
        chatStore.addAssistantMessage(planContent)
      }

      reviewModalVisible.value = false
      chatStore.addSystemMessage('✅ 已批准，继续执行')
      chatStore.setExecuting(true)

      // 重置流式状态，准备接收新的流式消息
      resetStreamingState()

      // ✅ 使用正确的函数名
      await resumeStreaming(currentThreadId.value)
    } else {
      reviewModalVisible.value = false
      chatStore.addSystemMessage('📝 已提交修改意见')

      // 使用修改意见重新规划（不需要调用 resume）
      await handleSendMessage()
    }
  } catch (error) {
    console.error('审核失败:', error)
    ElMessage.error('审核失败')
    // 审核失败时，恢复执行状态
    chatStore.setExecuting(false)
  }
}

// ========== 执行结果确认 ==========

async function handleResultConfirm(isCompleted: boolean) {
  if (!currentThreadId.value) return

  try {
    const feedback = isCompleted ? undefined : resultFeedback.value.trim() || undefined
    await confirmResult(currentThreadId.value, isCompleted, feedback)
    resultModalVisible.value = false
    chatStore.addSystemMessage(isCompleted ? '✅ 确认任务成功，继续反思' : '❌ 确认任务失败，继续反思')
    chatStore.setExecuting(true)

    resultFeedback.value = ''

    // 重置流式状态
    resetStreamingState()

    // ✅ 使用正确的函数名
    await resumeStreaming(currentThreadId.value)
  } catch (error) {
    console.error('结果确认失败:', error)
    ElMessage.error('结果确认失败')
    chatStore.setExecuting(false)
  }
}

// ========== 文件上传 ==========

function setupDragDrop() {
  // 处理在模板中
}

function handleDragOver() {
  isDragOver.value = true
}

function handleDragLeave() {
  isDragOver.value = false
}

function handleDrop(event: DragEvent) {
  isDragOver.value = false
  const files = event.dataTransfer?.files
  if (files) {
    Array.from(files).forEach(uploadSingleFile)
  }
}

function handleUploadClick() {
  fileInputRef.value?.click()
}

function handleFileUpload(event: Event) {
  const files = (event.target as HTMLInputElement).files
  if (files) {
    Array.from(files).forEach(uploadSingleFile)
  }
}

async function uploadSingleFile(file: File) {
  try {
    const data = await apiUploadFile(file)
    chatStore.addUploadedFile(data)
  } catch (error) {
    console.error('文件上传失败:', error)
    ElMessage.error('文件上传失败')
  }
}

function handleRemoveFile(index: number) {
  chatStore.removeUploadedFile(index)
}

// ========== QGIS 插件状态监控 ==========

async function refreshPluginStatus() {
  try {
    const data = await getQGISStatus()
    chatStore.setQGISStatus(data.status || 'running')
    chatStore.setQGISToolsCount(data.tools_count || 0)
  } catch (error) {
    chatStore.setQGISStatus('error')
  }
}

function startPluginStatusMonitor() {
  refreshPluginStatus()
  pluginStatusTimer.value = window.setInterval(refreshPluginStatus, 5000)
}

// ========== 截图预览 ==========

async function refreshScreenshotFromState() {
  if (!currentThreadId.value) return

  try {
    const data = await getCurrentState(currentThreadId.value)
    const path = data?.state?.screenshot_path
    if (path) {
      chatStore.setScreenshotPath(path)
      screenshotTime.value = new Date().toLocaleTimeString()
    }
  } catch (error) {
    console.error('获取截图状态失败:', error)
  }
}
</script>

<style scoped>
/* 自定义样式补充 */
.chat-messages {
  scroll-behavior: smooth;
}

/* 确保聊天区域宽度固定 */
.chat-container {
  width: clamp(480px, 78vw, 1280px);
  max-width: 100%;
  margin: 0 auto;
  min-width: 0; /* 允许flex子元素收缩 */
}

/* 消息之间的间距 */
.message {
  transition: all 0.2s ease;
  width: 100%;
}

.message:last-child {
  margin-bottom: 0 !important;
}

/* 消息内容样式 - 固定宽度 */
.message .content {
  white-space: pre-wrap;
  word-wrap: break-word;
  overflow-wrap: break-word;
  flex-shrink: 0; /* 防止内容被压缩 */
}

/* 思考消息样式 */
.thinking-message {
  transition: all 0.3s ease;
}

.thinking-header {
  transition: all 0.2s ease;
}

.thinking-header:hover {
  opacity: 0.8;
}

.thinking-content {
  color: var(--tw-colors-dark-text);
  opacity: 0.9;
}

.thinking-content.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 统一用户/AI 气泡宽度 */
.message .bubble {
  width: min(70%, 720px);
}

/* 系统消息保持自适应宽度 */
.message .system-bubble {
  width: auto;
  max-width: 80%;
}

/* 系统消息居中且更明显 */
.message:has(.content:where([class*="border"])) {
  display: flex;
  justify-content: center;
}

/* 头像样式 */
.message .avatar {
  flex-shrink: 0;
}

/* 确保会话列表项高度一致 */
.session-item {
  min-height: 60px;
}

/* 确保上传区域稳定 */
.upload-area {
  min-height: 80px;
}

/* 输入框固定最小高度，防止布局跳动 */
.input-wrapper {
  min-height: 48px;
}

/* 修复 Element Plus 组件样式 */
:deep(.el-button) {
  transition: all 0.2s;
}

:deep(.el-textarea__inner) {
  background-color: transparent;
  border: none;
  box-shadow: none;
  padding: 0;
  resize: none;
  line-height: 1.5;
  min-height: 32px !important;
  max-height: 128px !important;
}

:deep(.el-textarea__inner:focus) {
  box-shadow: none;
}

/* 确保 textarea 容器高度稳定 */
:deep(.el-textarea) {
  min-height: 32px;
}
</style>
