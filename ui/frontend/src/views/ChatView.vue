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
          <div class="text-xs text-dark-textSecondary mt-1">
            {{ uploadedFiles.length }} 个可用 · 已选 {{ selectedFileIds.length }} 个
          </div>
          <input
            ref="fileInputRef"
            type="file"
            multiple
            class="hidden"
            @change="handleFileUpload"
          />
        </div>
        <div class="mt-2.5 max-h-[32vh] overflow-y-auto space-y-2">
          <div
            v-for="file in uploadedFiles"
            :key="file.file_id"
            class="file-item p-2 bg-dark-bgLight rounded border border-dark-border"
          >
            <label class="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                class="mt-0.5 accent-primary-600"
                :checked="selectedFileIds.includes(file.file_id)"
                @change="handleFileSelection(file, $event)"
              />
              <span class="min-w-0 flex-1">
                <span class="block text-xs font-medium truncate" :title="file.filename">{{ file.filename }}</span>
                <span class="block text-[10px] text-dark-textSecondary mt-0.5">
                  {{ formatBytes(file.size) }} · SHA-256 {{ file.sha256 }}
                </span>
                <span class="block text-[10px] text-dark-textSecondary break-all" :title="file.file_id">
                  file_id: {{ file.file_id }}
                </span>
              </span>
            </label>

            <label v-if="file.layers && file.layers.length > 1" class="block mt-2 text-[11px]">
              <span class="block text-dark-textSecondary mb-1">GeoPackage 图层</span>
              <select
                v-model="selectedLayerByFileId[file.file_id]"
                class="w-full rounded border border-dark-border bg-dark-bg px-2 py-1 text-xs"
              >
                <option disabled value="">请选择图层</option>
                <option v-for="(layer, layerIndex) in file.layers" :key="layerIndex" :value="getLayerName(layer)">
                  {{ getLayerName(layer) || `图层 ${layerIndex + 1}` }}
                </option>
              </select>
            </label>

            <div v-for="(layer, layerIndex) in getFileLayerInfos(file)" :key="layerIndex" class="mt-2 border-t border-dark-border pt-2 text-[10px] leading-relaxed break-all">
              <div v-if="getLayerName(layer)" class="font-medium text-primary-600">{{ getLayerName(layer) }}</div>
              <div><span class="text-dark-textSecondary">来源：</span>{{ layer.source || '未提供' }}</div>
              <div><span class="text-dark-textSecondary">CRS：</span>{{ layer.crs || '未提供' }}</div>
              <div><span class="text-dark-textSecondary">几何：</span>{{ layer.geometry || '未提供' }}</div>
              <div><span class="text-dark-textSecondary">要素数：</span>{{ layer.feature_count ?? '未提供' }}</div>
              <div class="break-words">
                <span class="text-dark-textSecondary">字段：</span>{{ formatFields(layer.fields) }}
              </div>
            </div>
            <div v-if="getFileLayerInfos(file).length === 0" class="mt-2 border-t border-dark-border pt-2 text-[10px] leading-relaxed text-dark-textSecondary">
              来源、CRS、几何、要素数和字段：未提供图层元信息
            </div>
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
                <li>查看上传的矢量图层信息</li>
                <li>生成并审核 GIS 操作计划</li>
                <li>记录执行结果并由您确认</li>
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
                >{{ msg.content }}</div>
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
                  v-if="msg.role === 'user' || msg.role === 'assistant'"
                  class="avatar w-9 h-9 rounded-full flex items-center justify-center font-bold flex-shrink-0"
                  :class="{
                    'bg-primary-600': msg.role === 'assistant',
                    'bg-green-500': msg.role === 'user'
                  }"
                >
                  {{ msg.role === 'user' ? '👤' : 'AI' }}
                </div>
                <div
                  class="content bubble p-3 rounded-xl leading-relaxed"
                  :class="{
                    'max-w-[70%] bg-dark-bgLight rounded-tl-none': msg.role === 'assistant',
                    'max-w-[70%] bg-primary-600 rounded-tr-none text-white': msg.role === 'user',
                    'max-w-[80%] bg-dark-bgLight/50 border border-dark-border text-center text-sm system-bubble': msg.role === 'system'
                  }"
                >{{ msg.content }}</div>
              </div>
            </template>
          </div>
        </div>

        <!-- 输入区域 -->
        <div class="chat-input p-5 border-t border-dark-border flex-shrink-0 bg-dark-bgDark/80">
          <div v-if="executionSkillCandidates.length" class="mb-3 p-3 border border-dark-border rounded-lg text-sm" aria-label="任务业务记录中的参考示例">
            <p>业务计划记录中的参考条目；每次生成的计划仍需批准。</p>
            <p class="break-all">记录任务：{{ executionCandidateRecord?.taskId ?? '未记录' }} · 修订：{{ executionCandidateRecord?.revisionLabel ?? '未记录' }} · 状态：{{ executionCandidateRecord?.status ?? '未记录' }}</p>
            <p v-if="executionCandidateRecord && !executionCandidateRecord.isCurrent" class="text-amber-300">
              以下是业务记录中的修订，不代表当前方案<span v-if="executionCandidateRecord.currentRevision">（当前方案 r{{ executionCandidateRecord.currentRevision }}）</span>。
            </p>
            <p v-for="candidate in executionSkillCandidates" :key="`${candidate.id}-${candidate.version}`" class="mt-1 break-all text-dark-textSecondary">
              {{ candidate.id }} · 记录版本 v{{ candidate.version }}
              <span v-for="source in candidate.sources" :key="source.taskId">
                · 记录的来源引用：<a v-if="source.url" :href="source.url" target="_blank" rel="noopener noreferrer" class="underline">{{ source.taskId }}</a><span v-else>{{ source.taskId }}</span><span v-if="source.qgisVersion">（QGIS {{ source.qgisVersion }}）</span>
              </span>
              <span v-if="candidate.catalogUrl"> · <a :href="candidate.catalogUrl" target="_blank" rel="noopener noreferrer" class="underline">查看目录当前版本与审阅记录</a></span>
            </p>
          </div>
          <div v-if="executionStatus === 'success' || executionStatus === 'failed'" class="mb-3 max-h-[40vh] overflow-y-auto p-3 border border-dark-border rounded-lg text-sm">
            <p>任务状态：{{ executionStatus === 'success' ? '执行结束' : '执行失败' }}</p>
            <p v-if="executionStatus === 'failed'" class="whitespace-pre-wrap">失败原因：{{ executionSummary }}</p>
            <p>工具执行：{{ toolCompletionSummary }}</p>
            <p>文件完整性：{{ fileEvidenceSummary }}</p>
            <p>空间验证：{{ spatialEvidenceSummary }}</p>
            <p>用户确认：{{ userConfirmationSummary }}</p>
            <div v-for="(detail, index) in spatialArtifactDetails" :key="`${detail.kind}-${detail.path}-${index}`" class="mt-2 break-all">
              <p>{{ detail.kind === 'layer' ? '加载图层来源' : detail.kind === 'path' ? '文件产物' : '结果引用' }}：{{ detail.path }} · {{ detail.status === 'passed' ? '空间验证通过' : '空间验证未通过' }} · {{ detail.deliverable ? '可交付' : '不可交付，仅供诊断' }}</p>
              <p v-for="error in detail.errors" :key="error">{{ error }}</p>
              <details v-if="detail.checks.length || detail.facts">
                <summary>{{ detail.deliverable ? '查看空间验证依据' : '查看诊断依据（不代表可交付结果）' }}</summary>
                <pre class="whitespace-pre-wrap break-all">{{ formatJson({ checks: detail.checks, facts: detail.facts }) }}</pre>
              </details>
            </div>
            <section v-if="statisticsResults.length" class="mt-3 space-y-3" aria-label="统计结果">
              <div v-for="(stats, index) in statisticsResults" :key="`${stats.kind}-${stats.path}-${index}`" class="rounded-lg border border-dark-border bg-dark-bgLight p-3">
                <h4 class="font-semibold">统计结果</h4>
                <p class="mt-1 break-all text-xs text-dark-textSecondary">
                  {{ stats.sourceKind === 'layer' ? '已加载图层来源' : '统计表' }}：{{ stats.path }}<span v-if="stats.layerId"> · 图层 ID：{{ stats.layerId }}</span>
                </p>
                <dl class="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 md:grid-cols-3">
                  <div v-if="stats.kind !== 'feature_count'"><dt class="text-dark-textSecondary">来源字段</dt><dd class="break-all">{{ stats.field ?? (stats.kind === 'categorical' ? '无（仅分类计数）' : '未提供') }}</dd></div>
                  <div v-if="stats.groupFields.length"><dt class="text-dark-textSecondary">分类字段</dt><dd class="break-all">{{ stats.groupFields.join('、') }}</dd></div>
                  <div><dt class="text-dark-textSecondary">总要素数</dt><dd>{{ formatStatisticNumber(stats.featureCount) }}</dd></div>
                  <template v-if="hasNumericStatistics(stats)">
                    <div><dt class="text-dark-textSecondary">单位</dt><dd>{{ formatStatisticUnit(stats.unit) }}</dd></div>
                    <div><dt class="text-dark-textSecondary">有效数值数</dt><dd>{{ formatStatisticNumber(stats.validValueCount) }}</dd></div>
                    <div><dt class="text-dark-textSecondary">空值数</dt><dd>{{ formatStatisticNumber(stats.nullValueCount) }}</dd></div>
                    <div><dt class="text-dark-textSecondary">求和</dt><dd>{{ formatStatisticSum(stats) }}</dd></div>
                    <div><dt class="text-dark-textSecondary">最小值</dt><dd>{{ formatStatisticExtremum(stats.min, stats.noValidValues, stats.unit) }}</dd></div>
                    <div><dt class="text-dark-textSecondary">最大值</dt><dd>{{ formatStatisticExtremum(stats.max, stats.noValidValues, stats.unit) }}</dd></div>
                  </template>
                </dl>
                <p v-if="stats.empty" class="mt-2 text-dark-textSecondary">源图层为空（0 个要素）。</p>
                <p v-else-if="hasNumericStatistics(stats) && stats.noValidValues" class="mt-2 text-dark-textSecondary">输入含要素，但该字段无有效数值。</p>
                <div v-if="stats.kind === 'categorical'" class="mt-3">
                  <h5 class="font-medium">分类结果</h5>
                  <div v-if="stats.groups.length" class="overflow-x-auto">
                    <table class="mt-1 w-full text-left text-xs">
                      <thead><tr class="border-b border-dark-border">
                        <th scope="col" class="p-1">类别</th><th scope="col" class="p-1">总要素数</th>
                        <template v-if="hasNumericStatistics(stats)">
                          <th scope="col" class="p-1">有效数值数</th><th scope="col" class="p-1">空值数</th><th scope="col" class="p-1">求和</th><th scope="col" class="p-1">最小值</th><th scope="col" class="p-1">最大值</th>
                        </template>
                      </tr></thead>
                      <tbody><tr v-for="(group, groupIndex) in stats.groups" :key="groupIndex" class="border-b border-dark-border">
                        <td class="p-1 break-all">{{ formatStatisticGroup(group.group) }}</td><td class="p-1">{{ formatStatisticNumber(group.featureCount) }}</td>
                        <template v-if="hasNumericStatistics(stats)">
                          <td class="p-1">{{ formatStatisticNumber(group.validValueCount) }}</td><td class="p-1">{{ formatStatisticNumber(group.nullValueCount) }}</td><td class="p-1">{{ formatGroupSum(group, stats.unit) }}</td><td class="p-1">{{ formatStatisticExtremum(group.min, group.validValueCount === 0, stats.unit) }}</td><td class="p-1">{{ formatStatisticExtremum(group.max, group.validValueCount === 0, stats.unit) }}</td>
                        </template>
                      </tr></tbody>
                    </table>
                  </div>
                  <p v-else class="mt-1 text-dark-textSecondary">统计表没有类别行。</p>
                  <div v-if="stats.omittedGroups.length" class="mt-2">
                    <p>以下类别没有有效数值，未出现在 QGIS 统计表中；来源仍包含这些要素：</p>
                    <ul class="mt-1 list-inside list-disc">
                      <li v-for="(group, groupIndex) in stats.omittedGroups" :key="groupIndex" class="break-all">{{ formatStatisticGroup(group.group) }} · 总要素数 {{ formatStatisticNumber(group.featureCount) }} · 有效数值数 {{ formatStatisticNumber(group.validValueCount) }} · 空值数 {{ formatStatisticNumber(group.nullValueCount) }}</li>
                    </ul>
                  </div>
                </div>
              </div>
            </section>
          </div>
          <div v-if="executionStatus === 'needs_attention'" class="mb-3 p-3 border border-yellow-500 rounded-lg text-sm">
            <p>上次操作结果未知。请在实际 QGIS 中检查工程、图层和产物，确认该操作已结束或原进程已停止；核查不会重放旧操作。</p>
            <el-button class="mt-2" @click="handleProbeReconciliation">读取当前 QGIS 身份</el-button>
            <div v-if="reconciliationProbe" class="mt-2">
              <pre class="whitespace-pre-wrap break-all">{{ formatJson(reconciliationProbe) }}</pre>
              <el-input v-model="reconciliationNote" type="textarea" :rows="2" placeholder="记录检查了哪个工程、图层或文件，以及观察到的情况" />
              <label class="block mt-2"><input v-model="reconciliationChecked" type="checkbox" /> 我已检查实际 QGIS 和相关产物，并确认旧操作不再运行</label>
              <el-button class="mt-2" :disabled="!reconciliationChecked || !reconciliationNote.trim()" @click="handleReconciliation">记录核查结果</el-button>
            </div>
          </div>
          <p v-if="executionStatus === 'reconciled'" class="mb-3 text-sm">未知结果已记录人工核查；请创建新任务继续，旧操作不会重放。</p>
          <el-button v-if="isStreaming" class="mb-2" @click="handleStopReading">停止读取</el-button>
          <el-button v-if="currentThreadId" class="mb-2" :disabled="snapshotLoading" @click="refreshCurrentState">刷新当前状态</el-button>
          <el-button v-if="currentPause" class="mb-2" @click="showCurrentPause">继续处理待审核内容</el-button>
          <el-button v-if="currentContinuation" class="mb-2" :disabled="isExecuting" @click="handleContinueTask">继续已决定的任务</el-button>
          <p v-if="currentRunActive && !isStreaming" class="mb-2 text-sm">后台任务仍在运行；可稍后刷新状态。</p>
          <div class="input-wrapper flex gap-2.5 bg-dark-bgLight/80 border border-dark-border rounded-xl p-2.5 items-end">
            <el-input
              v-model="inputMessage"
              type="textarea"
              :rows="1"
              :autosize="{ minRows: 1, maxRows: 4 }"
              placeholder="输入你的地理处理需求..."
              :disabled="isExecuting || snapshotLoading || !canStartTask"
              @keydown="handleKeyDown"
              class="flex-1"
            />
            <el-button
              type="primary"
              :icon="Promotion"
              circle
              :disabled="!inputMessage.trim() || isExecuting || snapshotLoading || !canStartTask"
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
      :close-on-press-escape="false"
      :show-close="false"
    >
      <div v-if="currentDraft">
        <div class="plan-summary bg-dark-bgLight p-4 rounded-lg mb-4">
          <h3 class="font-semibold text-primary-600 mb-2">📋 审核 TaskPlan</h3>
          <p>{{ currentDraft.task_summary || '待审核的 GIS 任务计划' }}</p>
          <div class="text-xs text-dark-textSecondary mt-2 break-all">
            task_id: {{ currentDraft.task_id }} · revision: {{ currentDraft.revision }} · 状态: {{ currentDraft.review_status }}
          </div>
        </div>
        <div v-if="planSkillCandidates.length" class="mb-4 text-sm" aria-label="当前方案的参考示例">
          <p>计划记录中的参考条目，仍需批准当前计划。</p>
          <p v-for="candidate in planSkillCandidates" :key="`${candidate.id}-${candidate.version}`" class="mt-1 break-all text-dark-textSecondary">
            {{ candidate.id }} · 记录版本 v{{ candidate.version }}
            <span v-for="source in candidate.sources" :key="source.taskId">
              · 记录的来源引用：<a v-if="source.url" :href="source.url" target="_blank" rel="noopener noreferrer" class="underline">{{ source.taskId }}</a><span v-else>{{ source.taskId }}</span><span v-if="source.qgisVersion">（QGIS {{ source.qgisVersion }}）</span>
            </span>
            <span v-if="candidate.catalogUrl"> · <a :href="candidate.catalogUrl" target="_blank" rel="noopener noreferrer" class="underline">查看目录当前版本与审阅记录</a></span>
          </p>
        </div>
        <h4 class="font-semibold mb-2">必需输入</h4>
        <div v-if="currentDraft.required_inputs.length" class="space-y-2 mb-4">
          <div
            v-for="input in currentDraft.required_inputs"
            :key="input.input_id"
            class="p-2.5 bg-dark-bg rounded-lg border border-dark-border text-sm"
          >
            <div class="font-medium">{{ input.input_id }} · {{ input.description }}</div>
            <div class="text-xs text-dark-textSecondary mt-1 break-all">
              <span v-if="input.file_id">文件 ID: {{ input.file_id }}</span>
              <span v-if="input.layer_name"> · 图层: {{ input.layer_name }}</span>
              <span v-if="input.source"> · 来源: {{ input.source }}</span>
              <span v-if="input.path"> · 路径: {{ input.path }}</span>
            </div>
          </div>
        </div>
        <p v-else class="text-sm text-dark-textSecondary mb-4">无额外必需输入。</p>

        <h4 class="font-semibold mb-2">预期产物</h4>
        <div v-if="currentDraft.expected_outputs.length" class="space-y-2 mb-4">
          <div
            v-for="output in currentDraft.expected_outputs"
            :key="output.output_id"
            class="p-2.5 bg-dark-bg rounded-lg border border-dark-border text-sm"
          >
            <div class="font-medium">{{ output.output_id }} · {{ output.description }}</div>
            <div v-if="output.path" class="text-xs text-dark-textSecondary mt-1 break-all">{{ output.path }}</div>
          </div>
        </div>
        <p v-else class="text-sm text-dark-textSecondary mb-4">计划未声明预期产物。</p>

        <h4 class="font-semibold mb-2">执行步骤 ({{ currentDraft.steps.length }})</h4>
        <ul class="steps-list space-y-2 mb-4">
          <li
            v-for="step in currentDraft.steps"
            :key="step.step_id"
            class="step p-2.5 bg-dark-bg rounded-lg border-l-4 border-primary-600"
          >
            <div class="font-medium">步骤 {{ step.step_id }} · {{ step.description }}</div>
            <div class="text-xs text-dark-textSecondary mt-1 flex flex-wrap gap-x-3 gap-y-1">
              <span v-if="step.step_type">类型: {{ step.step_type }}</span>
              <span v-if="step.risk_level">风险: {{ step.risk_level }}</span>
              <span v-if="step.requires_review !== undefined">额外审核: {{ step.requires_review ? '需要' : '不需要' }}</span>
              <span v-if="step.input_refs?.length">输入引用: {{ step.input_refs.join(', ') }}</span>
              <span v-if="step.output_refs?.length">输出引用: {{ step.output_refs.join(', ') }}</span>
            </div>
          </li>
        </ul>

        <section v-if="planPreflightSteps.length" class="mb-4" aria-label="执行前数据条件">
          <h4 class="font-semibold mb-1">执行前数据条件</h4>
          <p class="text-xs text-dark-textSecondary mb-2">根据所选输入和前序步骤预检；执行时会再次核验。</p>
          <div class="space-y-2 max-h-52 overflow-y-auto">
            <div v-for="(step, index) in planPreflightSteps" :key="`${step.stepId}-${index}`" class="p-2.5 bg-dark-bg rounded-lg border border-dark-border text-sm">
              <div class="font-medium break-all">步骤 {{ step.stepId }} · {{ step.algorithm || step.tool || '工具调用' }}</div>
              <dl class="mt-1 space-y-1 text-xs">
                <div><dt class="inline text-dark-textSecondary">输入 CRS：</dt>
                  <dd v-if="step.inputCrs.length" class="inline"><span v-for="(input, inputIndex) in step.inputCrs" :key="inputIndex" class="break-all">{{ inputIndex ? '；' : '' }}{{ input.ref }}：{{ input.crs ?? '未指定' }}</span></dd>
                  <dd v-else class="inline">未指定</dd>
                </div>
                <div><dt class="inline text-dark-textSecondary">输出 CRS：</dt><dd class="inline">{{ step.outputCrs ?? (step.outputKind === 'table' ? '无（非空间表）' : '未指定') }}</dd></div>
                <div v-if="step.geometry"><dt class="inline text-dark-textSecondary">几何类型：</dt><dd class="inline">{{ step.geometry }}</dd></div>
                <div v-if="step.field !== null"><dt class="inline text-dark-textSecondary">字段：</dt><dd class="inline break-all">{{ step.field }}（{{ step.fieldType ?? '类型未指定' }}）</dd></div>
                <div v-if="step.groupFields.length"><dt class="inline text-dark-textSecondary">分类字段：</dt><dd class="inline break-all">{{ step.groupFields.join('、') }}</dd></div>
                <div v-if="step.field !== null"><dt class="inline text-dark-textSecondary">字段单位：</dt><dd class="inline">{{ formatStatisticUnit(step.unit) }}</dd></div>
              </dl>
            </div>
          </div>
        </section>

        <h4 class="font-semibold mb-2">具体工具调用 ({{ currentDraft.tool_calls.length }})</h4>
        <div class="space-y-2 mb-4 max-h-72 overflow-y-auto">
          <div
            v-for="(call, index) in currentDraft.tool_calls"
            :key="`${call.step_id}-${call.tool_name}-${index}`"
            class="p-3 bg-dark-bg rounded-lg border border-dark-border"
          >
            <div class="flex items-center justify-between gap-2">
              <div class="font-medium break-all">步骤 {{ call.step_id }} · {{ call.tool_name }}</div>
              <span class="text-xs shrink-0">{{ call.risk_level }} 风险</span>
            </div>
            <div class="text-xs text-dark-textSecondary mt-1">
              调用级审核: {{ call.requires_review ? '需要' : '不需要' }}
            </div>
            <div v-if="call.distance_unit" class="text-xs text-dark-textSecondary mt-1">
              缓冲距离单位: {{ call.distance_unit === 'm' ? '米 (m)' : call.distance_unit }}
            </div>
            <pre class="mt-2 text-xs whitespace-pre-wrap break-all overflow-auto">{{ formatJson(call.arguments) }}</pre>
          </div>
          <p v-if="currentDraft.tool_calls.length === 0" class="text-sm text-red-400">
            计划没有可审核的具体工具调用，不能批准执行。
          </p>
        </div>

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
        <el-button @click="reviewModalVisible = false">稍后处理</el-button>
        <el-button type="danger" @click="handleReview(false)">拒绝并重新规划</el-button>
        <el-button type="success" :disabled="!currentDraft?.tool_calls.length" @click="handleReview(true)">批准执行</el-button>
      </template>
    </el-dialog>

    <!-- 执行结果确认弹窗 -->
    <el-dialog
      v-model="resultModalVisible"
      title="📋 确认执行结果"
      width="600px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <p class="mb-4">执行节点已完成，请确认任务是否成功完成：</p>
      <div class="bg-dark-bgLight p-4 rounded-lg mb-4">
        <strong>执行摘要：</strong>
        <pre class="mt-2 text-sm whitespace-pre-wrap">{{ executionSummary }}</pre>
        <p class="mt-2 text-sm">工具执行：{{ toolCompletionSummary }}</p>
        <p class="text-sm">文件完整性：{{ fileEvidenceSummary }}</p>
        <p class="text-sm">空间验证：{{ spatialEvidenceSummary }}</p>
        <p class="text-sm">用户确认：{{ userConfirmationSummary }}</p>
        <section v-if="statisticsResults.length" class="mt-3 max-h-[40vh] space-y-3 overflow-y-auto" aria-label="统计结果">
          <div v-for="(stats, index) in statisticsResults" :key="`${stats.kind}-${stats.path}-${index}`" class="rounded border border-dark-border p-2">
            <strong>统计结果</strong>
            <pre class="mt-1 whitespace-pre-wrap break-words text-sm">{{ formatStatisticsReview(stats) }}</pre>
          </div>
        </section>
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
        您的确认将保存在任务结果中，并用于生成总结。
      </p>
      <template #footer>
        <el-button @click="resultModalVisible = false">稍后处理</el-button>
        <el-button type="danger" @click="handleResultConfirm(false)">❌ 任务失败</el-button>
        <el-button type="success" @click="handleResultConfirm(true)">✅ 任务成功</el-button>
      </template>
    </el-dialog>

    <!-- 高风险工具审核弹窗 -->
    <el-dialog
      v-model="riskReviewModalVisible"
      title="🛡️ 高风险工具审核"
      width="680px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <div v-if="currentRiskReview">
        <div class="bg-dark-bgLight p-4 rounded-lg mb-4">
          <div class="font-semibold text-primary-600 mb-2">
            {{ currentRiskReview.tool_call?.tool_name }}
          </div>
          <pre class="text-xs whitespace-pre-wrap overflow-auto max-h-64">{{ formatJson(currentRiskReview.tool_call?.arguments) }}</pre>
        </div>
        <label class="block text-sm mb-2">审核备注：</label>
        <el-input
          v-model="riskReviewComment"
          type="textarea"
          :rows="2"
          placeholder="可选：说明批准或拒绝原因"
        />
        <p class="text-xs text-dark-textSecondary mt-2">
          如需更改参数，请拒绝此操作，重新提交任务并审核新计划。
        </p>
      </div>
      <template #footer>
        <el-button @click="riskReviewModalVisible = false">稍后处理</el-button>
        <el-button type="danger" @click="handleRiskReview('rejected')">拒绝</el-button>
        <el-button type="success" @click="handleRiskReview('approved')">批准执行</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, toRefs } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElInput, ElDialog, ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Promotion, Setting } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chat'
import { useSSE, getNodeDisplayName, formatTime } from '../composables/useSSE'
import {
  confirmResult,
  createSession,
  deleteSession as apiDeleteSession,
  getCurrentState,
  getExecution,
  getFiles,
  getQGISStatus,
  probeReconciliation,
  reconcileExecution,
  getSessionHistory,
  getSessions,
  reviewPlan,
  submitRiskReview,
  uploadFile as apiUploadFile
} from '../api'
import type { LayerInfo, Message, QGISIdentity, ReviewPause, SessionHistory, TaskPlan, UploadedFile } from '../types'

interface RiskReviewData {
  task_id: string
  revision: number
  review_id: string
  tool_call?: {
    tool_name?: string
    arguments?: Record<string, unknown>
  }
}

interface StatisticValues {
  featureCount: number | null
  validValueCount: number | null
  nullValueCount: number | null
  sum: number | null
  min: number | null
  max: number | null
}

interface StatisticGroup extends StatisticValues {
  group: Record<string, unknown>
}

interface StatisticsResult extends StatisticValues {
  kind: 'feature_count' | 'numeric' | 'categorical'
  field: string | null
  groupFields: string[]
  unit: 'm2' | 'm' | null
  empty: boolean
  noValidValues: boolean
  groups: StatisticGroup[]
  omittedGroups: StatisticGroup[]
  sourceKind: 'path' | 'layer'
  path: string
  layerId: string | null
}

interface PlanPreflightStep {
  stepId: number | string
  tool: string | null
  algorithm: string | null
  inputCrs: Array<{ ref: string; crs: string | null }>
  outputCrs: string | null
  outputKind: string | null
  geometry: string | null
  field: string | null
  fieldType: string | null
  groupFields: string[]
  unit: 'm2' | 'm' | null
}

interface SkillCandidate {
  id: string
  version: string
  catalogUrl: string | null
  sources: Array<{ taskId: string; url: string | null; qgisVersion: string | null }>
}

const router = useRouter()
const chatStore = useChatStore()

const inputMessage = ref('')
const reviewModalVisible = ref(false)
const resultModalVisible = ref(false)
const riskReviewModalVisible = ref(false)
const reviewAdvise = ref('')
const resultFeedback = ref('')
const riskReviewComment = ref('')
const currentRiskReview = ref<RiskReviewData | null>(null)
const currentPause = ref<ReviewPause | null>(null)
const currentContinuation = ref<{ kind: 'resume'; task_id: string; revision: number } | null>(null)
const canStartTask = ref(true)
const currentRunActive = ref(false)
const snapshotLoading = ref(false)
const reconciliationProbe = ref<QGISIdentity | null>(null)
const reconciliationNote = ref('')
const reconciliationChecked = ref(false)
const isDragOver = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const screenshotTime = ref('暂无')
const pluginStatusTimer = ref<number | null>(null)
const selectedLayerByFileId = ref<Record<string, string>>({})
const thinkingCollapsed = ref<Record<number, boolean>>({})

const {
  currentThreadId,
  sessions,
  messages,
  uploadedFiles,
  selectedFileIds,
  isExecuting,
  currentDraft,
  currentExecutionResult,
  qgisStatus,
  qgisToolsCount,
  screenshotPath
} = toRefs(chatStore)

const qgisStatusText = computed(() => {
  switch (qgisStatus.value) {
    case 'running': return '插件已启动'
    case 'error': return '插件未启动'
    default: return '检测中...'
  }
})

const qgisStatusDetail = computed(() => {
  if (qgisStatus.value === 'running') {
    return `工具数: ${qgisToolsCount.value}`
  }
  return '请检查 QGIS MCP 插件'
})

const planSkillCandidates = computed(() => readSkillCandidates(currentDraft.value))
const executionSkillCandidates = computed(() => readSkillCandidates(asRecord(currentExecutionResult.value)?.plan))
const executionCandidateRecord = computed(() => {
  const record = asRecord(currentExecutionResult.value)
  const plan = asRecord(record?.plan)
  if (!record || !plan) return null
  const taskId = readString(record, 'task_id') ?? readString(plan, 'task_id')
  const revision = record.plan_revision
  const knownRevision = typeof revision === 'number' && Number.isInteger(revision) && revision > 0
  const current = currentDraft.value
  return {
    taskId,
    revisionLabel: knownRevision ? `r${revision}` : '未记录',
    status: readString(record, 'status'),
    currentRevision: current?.revision,
    isCurrent: knownRevision && current != null && current.task_id === taskId && current.revision === revision && plan.revision === revision,
  }
})

const planPreflightSteps = computed<PlanPreflightStep[]>(() => {
  const plan = currentDraft.value
  const preflight = asRecord(asRecord(plan?.metadata)?.spatial_preflight)
  if (!plan || preflight?.task_id !== plan.task_id || preflight.revision !== plan.revision
    || !Array.isArray(preflight.review_steps)) return []
  return preflight.review_steps.flatMap(value => {
    const step = asRecord(value)
    const stepId = step?.step_id
    if (typeof stepId !== 'string' && (typeof stepId !== 'number' || !Number.isFinite(stepId))) return []
    const inputCrs = Array.isArray(step?.input_crs) ? step.input_crs.flatMap(value => {
      const input = asRecord(value)
      const ref = readString(input, 'ref')
      return ref ? [{ ref, crs: readString(input, 'crs') ?? null }] : []
    }) : []
    return [{
      stepId,
      tool: readString(step, 'tool') ?? null,
      algorithm: readString(step, 'algorithm') ?? null,
      inputCrs,
      outputCrs: readString(step, 'output_crs') ?? null,
      outputKind: readString(step, 'output_kind') ?? null,
      geometry: readString(step, 'geometry') ?? null,
      field: readString(step, 'field') ?? null,
      fieldType: readString(step, 'field_type') ?? null,
      groupFields: Array.isArray(step?.group_fields)
        ? step.group_fields.filter((field): field is string => typeof field === 'string') : [],
      unit: step?.unit === 'm2' || step?.unit === 'm' ? step.unit : null
    }]
  })
})

const executionSummary = computed(() => {
  const result = asRecord(currentExecutionResult.value)
  return readString(result, 'summary')
    || readString(result, 'execution_summary')
    || readString(result, 'status')
    || '执行结果正在读取'
})

const executionStatus = computed(() => readString(asRecord(currentExecutionResult.value), 'status'))
const toolCompletionSummary = computed(() => {
  const attempts = asRecord(currentExecutionResult.value)?.attempts
  if (!Array.isArray(attempts)) return '等待记录'
  if (!attempts.length) return '尚无工具调用'
  const observations = attempts.map(asRecord).map(attempt => asRecord(attempt?.observation))
  const completed = observations.filter(item => item?.status === 'success').length
  const unknown = observations.filter(item => item?.outcome === 'unknown').length
  return `${completed}/${observations.length} 次工具调用返回成功${unknown ? `；${unknown} 次结果未知` : ''}`
})
const fileEvidenceSummary = computed(() => {
  const artifacts = asRecord(currentExecutionResult.value)?.artifacts
  if (!Array.isArray(artifacts)) return '等待记录'
  // Old records may repeat a produced path in add_layer's source echo. The
  // producer owns file evidence; a later reference neither replaces it nor
  // creates another unverified file. No checksum is inferred or copied.
  const pathsByName = new Map<string, Record<string, unknown>>()
  for (const item of artifacts.map(asRecord)) {
    const path = readString(item, 'path')
    if (item?.kind !== 'path' || !path) continue
    const previous = pathsByName.get(path)
    if (!previous || (!previous.producer_step_id && item.producer_step_id)) pathsByName.set(path, item)
  }
  const paths = [...pathsByName.values()]
  if (!paths.length) return '无文件产物'
  const verified = paths.filter(item => typeof item?.sha256 === 'string' && typeof item?.size_bytes === 'number'
    && asRecord(item?.integrity_validation)?.status !== 'failed').length
  return `${verified}/${paths.length} 个文件具有可读性和校验和证据`
})
const spatialArtifactDetails = computed(() => {
  const artifacts = asRecord(currentExecutionResult.value)?.artifacts
  if (!Array.isArray(artifacts)) return []
  return artifacts.map(asRecord).flatMap(item => {
    const report = asRecord(item?.spatial_validation)
    const path = readString(item, 'path')
    const kind = readString(item, 'kind') || ''
    const status = readString(report, 'status')
    if (!path || !status || status === 'not_applicable') return []
    const errors = Array.isArray(report?.errors)
      ? report.errors.filter((error): error is string => typeof error === 'string') : []
    const integrity = asRecord(item?.integrity_validation)
    if (Array.isArray(integrity?.errors)) {
      errors.push(...integrity.errors.filter((error): error is string => typeof error === 'string'))
    }
    const checks = Array.isArray(report?.checks) ? report.checks : []
    const facts = asRecord(report?.facts)
    const layerId = readString(item, 'layer_id') || null
    return [{ kind, path, layerId, status, errors, checks, facts, deliverable: item?.deliverable === true && status === 'passed' }]
  })
})
const statisticsResults = computed<StatisticsResult[]>(() => spatialArtifactDetails.value.flatMap(detail => {
  if (!detail.deliverable || (detail.kind !== 'path' && detail.kind !== 'layer')) return []
  const statistics = parseStatistics(asRecord(detail.facts?.statistics))
  if (!statistics) return []
  return [{ ...statistics, sourceKind: detail.kind, path: detail.path, layerId: detail.layerId }]
}))
const spatialEvidenceSummary = computed(() => {
  const reports = spatialArtifactDetails.value
  if (!reports.length) return '未提供独立空间验证'
  const passed = reports.filter(report => report.status === 'passed').length
  const failed = reports.length - passed
  return `${passed} 项通过${failed ? `；${failed} 项未通过` : ''}`
})
const userConfirmationSummary = computed(() => {
  const confirmed = asRecord(currentExecutionResult.value)?.confirmed
  return confirmed === true ? '已确认成功' : confirmed === false ? '已确认失败' : '尚未确认'
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

const { isStreaming, startStreaming, resumeStreaming, stopStreaming } = useSSE(
  handleSSEEvent,
  handleSSEComplete,
  handleSSEError
)

onMounted(async () => {
  await Promise.all([loadSessions(), loadFiles()])
  startPluginStatusMonitor()
})

onUnmounted(() => {
  if (pluginStatusTimer.value !== null) {
    clearInterval(pluginStatusTimer.value)
  }
})

async function loadSessions() {
  try {
    chatStore.setSessions(await getSessions())
  } catch (error) {
    console.error('加载会话失败:', error)
    ElMessage.error('加载会话失败')
  }
}

async function loadFiles() {
  try {
    const response = await getFiles()
    chatStore.setUploadedFiles(Array.isArray(response.files) ? response.files : [])
    for (const file of response.files || []) {
      ensureLayerSelection(file)
    }
  } catch (error) {
    console.error('加载文件列表失败:', error)
    ElMessage.error('加载文件列表失败')
  }
}

async function handleCreateSession() {
  try {
    const session = await createSession()
    chatStore.addSession(session)
    await handleSwitchSession(session.thread_id)
    ElMessage.success(`已创建新会话: ${session.name}`)
  } catch (error) {
    console.error('创建会话失败:', error)
    ElMessage.error('创建会话失败')
  }
}

function handleOpenSettings() {
  void router.push('/settings')
}

let sessionEpoch = 0
let snapshotVersion = 0

async function handleSwitchSession(threadId: string) {
  const epoch = ++sessionEpoch
  stopStreaming()
  resetStreamingState()
  reviewModalVisible.value = false
  resultModalVisible.value = false
  riskReviewModalVisible.value = false
  currentRiskReview.value = null
  currentPause.value = null
  currentContinuation.value = null
  canStartTask.value = false
  currentRunActive.value = false
  reconciliationProbe.value = null
  reconciliationNote.value = ''
  reconciliationChecked.value = false
  chatStore.setCurrentDraft(null)
  chatStore.setExecutionResult(null)
  chatStore.setExecuting(false)
  chatStore.setScreenshotPath(null)
  chatStore.setThreadId(threadId)
  chatStore.clearMessages()

  await loadSessionHistory(threadId, epoch)
  if (epoch === sessionEpoch) await restoreCurrentTaskPlan(threadId, epoch)
}

async function loadSessionHistory(threadId: string, epoch: number) {
  try {
    const history = await getSessionHistory(threadId)
    if (currentThreadId.value === threadId && epoch === sessionEpoch) renderHistory(history)
  } catch (error) {
    console.error('加载历史失败:', error)
    ElMessage.error('加载历史失败')
  }
}

function renderHistory(data: SessionHistory) {
  chatStore.clearMessages()
  for (const message of data.chat_messages || []) {
    appendHistoryMessage(message)
  }

  if (!data.chat_messages?.length) {
    if (data.input_query) chatStore.addUserMessage(data.input_query)
    if (data.log_summary) chatStore.addAssistantMessage(data.log_summary)
    if (data.final_summary) chatStore.addAssistantMessage(data.final_summary)
  }

  if (data.screenshot_path) {
    chatStore.setScreenshotPath(data.screenshot_path)
    screenshotTime.value = new Date().toLocaleTimeString()
  }
}

function appendHistoryMessage(message: Message) {
  if (message.role === 'user') {
    chatStore.addUserMessage(message.content)
  } else if (message.role === 'system') {
    chatStore.addSystemMessage(message.content)
  } else if (message.role === 'thinking') {
    chatStore.addThinkingMessage(message.content)
  } else {
    chatStore.addAssistantMessage(message.content)
  }
}

function showCurrentPause() {
  if (currentPause.value?.kind === 'plan_review') reviewModalVisible.value = true
  if (currentPause.value?.kind === 'risk_review') riskReviewModalVisible.value = true
  if (currentPause.value?.kind === 'result_review') resultModalVisible.value = true
}

async function restoreCurrentTaskPlan(threadId: string, epoch: number) {
  const version = ++snapshotVersion
  snapshotLoading.value = true
  try {
    const snapshot = await getCurrentState(threadId)
    if (currentThreadId.value !== threadId || epoch !== sessionEpoch || version !== snapshotVersion) return
    const plan = snapshot.state.task_plan
    if (isTaskPlan(plan)) chatStore.setCurrentDraft(plan)
    const pause = snapshot.pause || null
    currentPause.value = pause
    currentContinuation.value = snapshot.continuation || null
    canStartTask.value = snapshot.can_start_task
    currentRunActive.value = snapshot.running
    reviewModalVisible.value = false
    riskReviewModalVisible.value = false
    resultModalVisible.value = false
    if (pause?.kind === 'plan_review' && isTaskPlan(plan)) {
      reviewAdvise.value = ''
      showCurrentPause()
    } else if (pause?.kind === 'risk_review') {
      currentRiskReview.value = parseRiskReview(pause)
      riskReviewComment.value = ''
      if (currentRiskReview.value) showCurrentPause()
    } else if (pause?.kind === 'result_review') {
      chatStore.setExecutionResult(pause)
      resultFeedback.value = ''
      showCurrentPause()
    }
    if (isTaskPlan(plan)) await loadExecutionDetails(plan.task_id, threadId)
  } catch (error) {
    console.debug('当前会话没有待恢复的执行状态:', error)
  } finally {
    if (version === snapshotVersion) snapshotLoading.value = false
  }
}

function refreshCurrentState() {
  const threadId = currentThreadId.value
  if (threadId) void restoreCurrentTaskPlan(threadId, sessionEpoch)
}

async function loadExecutionDetails(taskId: string, threadId = currentThreadId.value) {
  const epoch = sessionEpoch
  try {
    const response = await getExecution(taskId)
    if (threadId === currentThreadId.value && epoch === sessionEpoch && currentDraft.value?.task_id === taskId) {
      chatStore.setExecutionResult(response.execution)
    }
  } catch (error) {
    console.debug(`任务 ${taskId} 暂无可查询执行记录:`, error)
  }
}

function handleStopReading() {
  stopStreaming()
  chatStore.setExecuting(false)
  canStartTask.value = false
  chatStore.addSystemMessage('已停止读取；后台任务可能仍在运行，QGIS 操作没有被取消。')
  refreshCurrentState()
}

async function handleContinueTask() {
  const threadId = currentThreadId.value
  const epoch = sessionEpoch
  if (!threadId || !currentContinuation.value) return
  currentContinuation.value = null
  canStartTask.value = false
  chatStore.setExecuting(true)
  resetStreamingState()
  await resumeStreaming(threadId)
  if (currentThreadId.value === threadId && epoch === sessionEpoch) {
    await restoreCurrentTaskPlan(threadId, epoch)
  }
}

async function handleProbeReconciliation() {
  const threadId = currentThreadId.value
  const epoch = sessionEpoch
  const taskId = currentDraft.value?.task_id
  if (!threadId || !taskId) return
  try {
    const probe = await probeReconciliation(taskId)
    if (currentThreadId.value === threadId && epoch === sessionEpoch && currentDraft.value?.task_id === taskId) {
      reconciliationProbe.value = probe.qgis
      reconciliationChecked.value = false
    }
  } catch (error) {
    if (currentThreadId.value === threadId && epoch === sessionEpoch && currentDraft.value?.task_id === taskId) {
      ElMessage.error(readString(asRecord(error), 'detail') || '无法读取 QGIS 身份')
    }
  }
}

async function handleReconciliation() {
  const threadId = currentThreadId.value
  const epoch = sessionEpoch
  const taskId = currentDraft.value?.task_id
  const probe = reconciliationProbe.value
  if (!threadId || !taskId || !probe || !reconciliationChecked.value || !reconciliationNote.value.trim()) return
  try {
    await reconcileExecution(taskId, threadId, reconciliationNote.value.trim(), probe)
    if (currentThreadId.value !== threadId || epoch !== sessionEpoch || currentDraft.value?.task_id !== taskId) return
    reconciliationProbe.value = null
    await restoreCurrentTaskPlan(threadId, epoch)
  } catch (error) {
    if (currentThreadId.value === threadId && epoch === sessionEpoch && currentDraft.value?.task_id === taskId) {
      ElMessage.error(readString(asRecord(error), 'detail') || '无法记录核查结果')
    }
  }
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
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除会话失败:', error)
      ElMessage.error('删除会话失败')
    }
  }
}

async function handleSendMessage() {
  const message = inputMessage.value.trim()
  if (!message) return
  if (isStreaming.value) {
    ElMessage.warning('请等待当前任务完成')
    return
  }

  resetStreamingState()
  const files = selectedFileReferences()
  if (!files) {
    ElMessage.warning('请为每个已选择的 GeoPackage 明确选择一个图层')
    return
  }

  try {
    if (!currentThreadId.value) {
      const session = await createSession()
      chatStore.addSession(session)
      chatStore.setThreadId(session.thread_id)
    }
    const threadId = currentThreadId.value
    if (!threadId) throw new Error('无法创建会话')

    chatStore.setCurrentDraft(null)
    chatStore.setExecutionResult(null)
    chatStore.addUserMessage(message)
    inputMessage.value = ''
    chatStore.setExecuting(true)
    canStartTask.value = false
    await startStreaming({
      message,
      thread_id: threadId,
      files
    })
    if (currentThreadId.value === threadId) await restoreCurrentTaskPlan(threadId, sessionEpoch)
  } catch (error) {
    console.error('发送消息失败:', error)
    ElMessage.error(error instanceof Error ? error.message : '发送消息失败')
    chatStore.setExecuting(false)
  }
}

function handleKeyDown(event: Event | KeyboardEvent) {
  if (!(event instanceof KeyboardEvent)) return
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void handleSendMessage()
  }
}

function toggleThinkingCollapse(index: number) {
  thinkingCollapsed.value[index] = !thinkingCollapsed.value[index]
}

let isStreamingMessage = false
let isStreamingThinking = false

function resetStreamingState() {
  isStreamingMessage = false
  isStreamingThinking = false
}

function handleSSEEvent(event: string, data: unknown) {
  const payload = asRecord(data)
  switch (event) {
    case 'start':
      chatStore.addSystemMessage(`🚀 ${readString(payload, 'message') || '开始处理'}`)
      break
    case 'node_start':
      chatStore.addSystemMessage(`🔄 正在执行: ${getNodeDisplayName(readString(payload, 'node') || '未知节点')}`)
      break
    case 'node_end':
      chatStore.addSystemMessage(`✅ 完成: ${getNodeDisplayName(readString(payload, 'node') || '未知节点')}`)
      break
    case 'thinking': {
      const content = readString(payload, 'content')
      if (!content) break
      if (!isStreamingThinking) {
        chatStore.startThinkingMessage()
        isStreamingThinking = true
      }
      chatStore.appendThinkingMessage(content)
      break
    }
    case 'output':
    case 'message_delta': {
      const content = readString(payload, 'content')
      if (!content) break
      if (!isStreamingMessage) {
        chatStore.startStreamingMessage()
        isStreamingMessage = true
      }
      isStreamingThinking = false
      chatStore.appendAssistantMessage(content)
      break
    }
    case 'human_review_required': {
      const taskPlan = payload?.task_plan
      if (!isTaskPlan(taskPlan)) {
        chatStore.addSystemMessage('后端返回的 TaskPlan 格式无效，已停止审核。')
        chatStore.setExecuting(false)
        break
      }
      resetStreamingState()
      chatStore.setCurrentDraft(taskPlan)
      currentPause.value = { kind: 'plan_review', task_id: taskPlan.task_id, revision: taskPlan.revision }
      chatStore.setExecutionResult(null)
      reviewAdvise.value = ''
      reviewModalVisible.value = true
      chatStore.setExecuting(false)
      void loadExecutionDetails(taskPlan.task_id)
      break
    }
    case 'risk_review_required': {
      const riskReview = parseRiskReview(data)
      if (!riskReview) {
        chatStore.addSystemMessage('高风险审核请求缺少调用身份，已停止审核。')
        chatStore.setExecuting(false)
        break
      }
      resetStreamingState()
      currentRiskReview.value = riskReview
      currentPause.value = { kind: 'risk_review', ...riskReview }
      riskReviewComment.value = ''
      riskReviewModalVisible.value = true
      chatStore.setExecuting(false)
      break
    }
    case 'result_review_required': {
      const result = parseRiskReview(data)
      if (!result) {
        chatStore.addSystemMessage('结果确认缺少任务身份，已停止审核。')
        chatStore.setExecuting(false)
        break
      }
      currentPause.value = { kind: 'result_review', ...result }
      resetStreamingState()
      chatStore.setExecutionResult(data)
      resultFeedback.value = ''
      resultModalVisible.value = true
      chatStore.setExecuting(false)
      if (currentDraft.value) void loadExecutionDetails(currentDraft.value.task_id)
      void refreshScreenshotFromState()
      break
    }
    case 'complete': {
      resetStreamingState()
      const summary = readString(payload, 'summary')
      if (summary) {
        const lastAssistant = [...messages.value].reverse().find(message => message.role === 'assistant')
        const lastContent = lastAssistant?.content || ''
        if (!lastContent.trim().endsWith(summary.trim())) {
          chatStore.addAssistantMessage(`任务处理结束。\n\n${summary}`)
        }
      } else {
        chatStore.addSystemMessage('任务处理结束。')
      }
      const screenshot = readString(payload, 'screenshot_path')
      if (screenshot) {
        chatStore.setScreenshotPath(screenshot)
        screenshotTime.value = new Date().toLocaleTimeString()
      }
      chatStore.setExecuting(false)
      if (currentDraft.value) void loadExecutionDetails(currentDraft.value.task_id)
      break
    }
    case 'error':
      resetStreamingState()
      chatStore.addAssistantMessage(`❌ 错误: ${readString(payload, 'message') || '未知错误'}`)
      chatStore.setExecuting(false)
      if (currentDraft.value) void loadExecutionDetails(currentDraft.value.task_id)
      break
    case 'paused':
      resetStreamingState()
      chatStore.setExecuting(false)
      chatStore.addSystemMessage(`⏸️ ${readString(payload, 'message') || '任务已暂停'}`)
      break
  }
}

function parseRiskReview(value: unknown): RiskReviewData | null {
  const record = asRecord(value)
  const reviewId = readString(record, 'review_id')
  const taskId = readString(record, 'task_id')
  const revision = record?.revision
  if (!reviewId || !taskId || typeof revision !== 'number') return null
  const toolCall = asRecord(record?.tool_call)
  const argumentsValue = asRecord(toolCall?.arguments) || undefined
  return {
    review_id: reviewId,
    task_id: taskId,
    revision,
    tool_call: toolCall
      ? { tool_name: readString(toolCall, 'tool_name'), arguments: argumentsValue }
      : undefined
  }
}

function readStatisticNumber(record: Record<string, unknown> | null, key: string): number | null {
  const value = record?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function readStatisticValues(record: Record<string, unknown> | null): StatisticValues {
  return {
    featureCount: readStatisticNumber(record, 'feature_count'),
    validValueCount: readStatisticNumber(record, 'valid_value_count'),
    nullValueCount: readStatisticNumber(record, 'null_value_count'),
    sum: readStatisticNumber(record, 'sum'),
    min: readStatisticNumber(record, 'min'),
    max: readStatisticNumber(record, 'max')
  }
}

function readStatisticGroups(value: unknown): StatisticGroup[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(item => {
    const record = asRecord(item)
    if (!record) return []
    return [{ ...readStatisticValues(record), group: asRecord(record.group) || {} }]
  })
}

function parseStatistics(record: Record<string, unknown> | null): Omit<StatisticsResult, 'sourceKind' | 'path' | 'layerId'> | null {
  const kind = readString(record, 'kind')
  if (kind !== 'feature_count' && kind !== 'numeric' && kind !== 'categorical') return null
  return {
    ...readStatisticValues(record),
    kind,
    field: readString(record, 'field') ?? null,
    groupFields: Array.isArray(record?.group_fields)
      ? record.group_fields.filter((field): field is string => typeof field === 'string') : [],
    unit: record?.unit === 'm2' || record?.unit === 'm' ? record.unit : null,
    empty: record?.empty === true,
    noValidValues: record?.no_valid_values === true,
    groups: readStatisticGroups(record?.groups),
    omittedGroups: readStatisticGroups(record?.omitted_groups)
  }
}

function hasNumericStatistics(stats: StatisticsResult): boolean {
  return stats.kind === 'numeric' || (stats.kind === 'categorical' && stats.field !== null)
}

function formatStatisticNumber(value: number | null, unit: StatisticsResult['unit'] = null): string {
  const suffix = unit === 'm2' ? ' m²' : unit === 'm' ? ' m' : ''
  return value === null ? '无值' : `${value}${suffix}`
}

function formatStatisticUnit(unit: StatisticsResult['unit']): string {
  return unit === 'm2' ? 'm²' : unit === 'm' ? 'm' : '未指定'
}

function formatStatisticSum(stats: StatisticsResult): string {
  if (stats.noValidValues) {
    // The basic statistics table contains a QGIS empty sum. A categorical
    // table has no global row, so its independently computed sum is not a
    // QGIS scalar and should not be presented as one.
    if (stats.kind === 'numeric' && stats.sum !== null) {
      return `无有效数值（QGIS 原始求和：${formatStatisticNumber(stats.sum, stats.unit)}）`
    }
    return '无有效数值'
  }
  return formatStatisticNumber(stats.sum, stats.unit)
}

function formatGroupSum(group: StatisticGroup, unit: StatisticsResult['unit']): string {
  return group.validValueCount === 0 ? '无有效数值' : formatStatisticNumber(group.sum, unit)
}

function formatStatisticExtremum(value: number | null, noValidValues: boolean, unit: StatisticsResult['unit']): string {
  return noValidValues ? '无值' : formatStatisticNumber(value, unit)
}

function formatStatisticGroup(group: Record<string, unknown>): string {
  const entries = Object.entries(group)
  if (!entries.length) return '未提供类别'
  return entries.map(([field, value]) => {
    let label: string
    if (value === null) label = '（空值）'
    else if (value === '') label = '（空字符串）'
    else if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') label = String(value)
    else label = JSON.stringify(value) ?? '未提供'
    return `${field}：${label}`
  }).join('、')
}

function formatStatisticsReview(stats: StatisticsResult): string {
  const lines = [`${stats.sourceKind === 'layer' ? '已加载图层来源' : '统计表'}：${stats.path}`]
  if (stats.layerId) lines.push(`图层 ID：${stats.layerId}`)
  if (stats.kind !== 'feature_count') lines.push(`来源字段：${stats.field ?? (stats.kind === 'categorical' ? '无（仅分类计数）' : '未提供')}`)
  if (stats.groupFields.length) lines.push(`分类字段：${stats.groupFields.join('、')}`)
  lines.push(`总要素数：${formatStatisticNumber(stats.featureCount)}`)
  if (hasNumericStatistics(stats)) {
    lines.push(`单位：${formatStatisticUnit(stats.unit)}`)
    lines.push(`有效数值数：${formatStatisticNumber(stats.validValueCount)} · 空值数：${formatStatisticNumber(stats.nullValueCount)}`)
    lines.push(`求和：${formatStatisticSum(stats)}`)
    lines.push(`最小值：${formatStatisticExtremum(stats.min, stats.noValidValues, stats.unit)} · 最大值：${formatStatisticExtremum(stats.max, stats.noValidValues, stats.unit)}`)
  }
  if (stats.empty) lines.push('源图层为空（0 个要素）。')
  else if (hasNumericStatistics(stats) && stats.noValidValues) lines.push('输入含要素，但该字段无有效数值。')
  if (stats.kind === 'categorical') {
    lines.push('分类结果：')
    if (!stats.groups.length) lines.push('统计表没有类别行。')
    for (const group of stats.groups) {
      const values = [`${formatStatisticGroup(group.group)}`, `总要素数 ${formatStatisticNumber(group.featureCount)}`]
      if (hasNumericStatistics(stats)) {
        values.push(`有效数值数 ${formatStatisticNumber(group.validValueCount)}`, `空值数 ${formatStatisticNumber(group.nullValueCount)}`,
          `求和 ${formatGroupSum(group, stats.unit)}`, `最小值 ${formatStatisticExtremum(group.min, group.validValueCount === 0, stats.unit)}`,
          `最大值 ${formatStatisticExtremum(group.max, group.validValueCount === 0, stats.unit)}`)
      }
      lines.push(values.join(' · '))
    }
    if (stats.omittedGroups.length) {
      lines.push('以下类别没有有效数值，未出现在 QGIS 统计表中；来源仍包含这些要素：')
      for (const group of stats.omittedGroups) {
        lines.push(`${formatStatisticGroup(group.group)} · 总要素数 ${formatStatisticNumber(group.featureCount)} · 有效数值数 ${formatStatisticNumber(group.validValueCount)} · 空值数 ${formatStatisticNumber(group.nullValueCount)}`)
      }
    }
  }
  return lines.join('\n')
}

function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2) || '{}'
}

function handleSSEComplete() {
  // The event handler owns the UI transitions for each named SSE event.
}

function handleSSEError(error: Error) {
  console.error('SSE 错误:', error)
  chatStore.setExecuting(false)
  ElMessage.error(error.message)
}

async function handleReview(approved: boolean) {
  const threadId = currentThreadId.value
  const epoch = sessionEpoch
  const taskPlan = currentDraft.value
  if (!threadId || !taskPlan) return

  const advise = reviewAdvise.value.trim()
  if (!approved && !advise) {
    ElMessage.warning('拒绝计划时请填写修改意见')
    return
  }

  try {
    await reviewPlan({
      thread_id: threadId,
      task_id: taskPlan.task_id,
      revision: taskPlan.revision,
      approved,
      advise: approved ? undefined : advise
    })
    if (currentThreadId.value !== threadId || epoch !== sessionEpoch) return
    currentPause.value = null
    reviewModalVisible.value = false
    chatStore.addSystemMessage(approved ? '✅ 已批准当前计划，继续执行' : '📝 已提交意见，将在同一任务中重新规划')
    chatStore.setExecuting(true)
    resetStreamingState()
    await resumeStreaming(threadId)
    if (currentThreadId.value === threadId && epoch === sessionEpoch) await restoreCurrentTaskPlan(threadId, epoch)
  } catch (error) {
    console.error('审核失败:', error)
    if (currentThreadId.value === threadId && epoch === sessionEpoch) {
      ElMessage.error(readString(asRecord(error), 'detail') || '审核失败')
      chatStore.setExecuting(false)
      await restoreCurrentTaskPlan(threadId, epoch)
    }
  }
}

async function handleResultConfirm(isCompleted: boolean) {
  const threadId = currentThreadId.value
  const epoch = sessionEpoch
  const pause = currentPause.value
  if (!threadId || !pause || pause.kind !== 'result_review' || !pause.review_id) return

  try {
    const feedback = isCompleted ? undefined : resultFeedback.value.trim() || undefined
    await confirmResult(threadId, pause.task_id, pause.revision, pause.review_id, isCompleted, feedback)
    if (currentThreadId.value !== threadId || epoch !== sessionEpoch) return
    currentPause.value = null
    resultModalVisible.value = false
    chatStore.addSystemMessage(isCompleted ? '✅ 确认任务成功，继续反思' : '❌ 确认任务失败，继续反思')
    chatStore.setExecuting(true)
    resultFeedback.value = ''
    resetStreamingState()
    await resumeStreaming(threadId)
    if (currentThreadId.value === threadId && epoch === sessionEpoch) await restoreCurrentTaskPlan(threadId, epoch)
  } catch (error) {
    console.error('结果确认失败:', error)
    if (currentThreadId.value === threadId && epoch === sessionEpoch) {
      ElMessage.error(readString(asRecord(error), 'detail') || '结果确认失败')
      chatStore.setExecuting(false)
      await restoreCurrentTaskPlan(threadId, epoch)
    }
  }
}

async function handleRiskReview(status: 'approved' | 'rejected') {
  const threadId = currentThreadId.value
  const epoch = sessionEpoch
  const riskReview = currentRiskReview.value
  if (!threadId || !riskReview) return

  try {
    await submitRiskReview(
      threadId,
      riskReview.task_id,
      riskReview.revision,
      riskReview.review_id,
      status,
      riskReviewComment.value.trim() || undefined
    )
    if (currentThreadId.value !== threadId || epoch !== sessionEpoch) return
    currentPause.value = null
    riskReviewModalVisible.value = false
    chatStore.addSystemMessage(
      status === 'approved'
        ? '✅ 风险操作已批准，继续执行'
        : '❌ 风险操作已拒绝'
    )
    chatStore.setExecuting(true)
    resetStreamingState()
    await resumeStreaming(threadId)
    if (currentThreadId.value === threadId && epoch === sessionEpoch) await restoreCurrentTaskPlan(threadId, epoch)
  } catch (error) {
    console.error('风险审核失败:', error)
    if (currentThreadId.value === threadId && epoch === sessionEpoch) {
      ElMessage.error(readString(asRecord(error), 'detail') || '风险审核失败')
      chatStore.setExecuting(false)
      await restoreCurrentTaskPlan(threadId, epoch)
    }
  }
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
  if (files) void Promise.all(Array.from(files).map(uploadSingleFile))
}

function handleUploadClick() {
  fileInputRef.value?.click()
}

function handleFileUpload(event: Event) {
  const input = event.target
  if (!(input instanceof HTMLInputElement) || !input.files) return
  const files = Array.from(input.files)
  input.value = ''
  void Promise.all(files.map(uploadSingleFile))
}

async function uploadSingleFile(file: File) {
  try {
    const uploaded = await apiUploadFile(file)
    chatStore.addUploadedFile(uploaded)
    chatStore.setFileSelected(uploaded.file_id, true)
    ensureLayerSelection(uploaded)
    ElMessage.success(`已上传并选择 ${uploaded.filename}`)
  } catch (error) {
    console.error('文件上传失败:', error)
    ElMessage.error(error instanceof Error ? error.message : '文件上传失败')
  }
}

function handleFileSelection(file: UploadedFile, event: Event) {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) return
  chatStore.setFileSelected(file.file_id, input.checked)
}

function selectedFileReferences(): Array<string | { file_id: string; layer_name: string }> | null {
  const references: Array<string | { file_id: string; layer_name: string }> = []
  for (const fileId of selectedFileIds.value) {
    const file = uploadedFiles.value.find(candidate => candidate.file_id === fileId)
    if (!file) continue
    if (file.layers && file.layers.length > 1) {
      const layerName = selectedLayerByFileId.value[file.file_id]
      if (!layerName) return null
      references.push({ file_id: file.file_id, layer_name: layerName })
    } else {
      references.push(file.file_id)
    }
  }
  return references
}

function getLayerName(layer: LayerInfo): string {
  return layer.layer_name || layer.name || ''
}

function getFileLayerInfos(file: UploadedFile): LayerInfo[] {
  if (file.layers?.length) return file.layers
  return file.layer_info ? [file.layer_info] : []
}

function ensureLayerSelection(file: UploadedFile) {
  if (file.layers && file.layers.length > 1 && !(file.file_id in selectedLayerByFileId.value)) {
    selectedLayerByFileId.value[file.file_id] = ''
  }
}

function formatFields(fields: unknown[] | undefined): string {
  if (!fields?.length) return fields ? '无' : '未提供'
  return fields.map((field) => {
    if (typeof field === 'string') return field
    const record = asRecord(field)
    if (record) {
      const name = readString(record, 'name') || readString(record, 'field_name')
      const type = readString(record, 'type') || readString(record, 'data_type') || readString(record, 'field_type')
      if (name) return type ? `${name} (${type})` : name
    }
    return JSON.stringify(field) || String(field)
  }).join(', ')
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

async function refreshPluginStatus() {
  try {
    const data = await getQGISStatus()
    chatStore.setQGISStatus(data.status)
    chatStore.setQGISToolsCount(data.tools_count)
  } catch {
    chatStore.setQGISStatus('error')
  }
}

function startPluginStatusMonitor() {
  void refreshPluginStatus()
  pluginStatusTimer.value = window.setInterval(refreshPluginStatus, 5000)
}

async function refreshScreenshotFromState() {
  const threadId = currentThreadId.value
  const epoch = sessionEpoch
  if (!threadId) return
  try {
    const data = await getCurrentState(threadId)
    if (currentThreadId.value !== threadId || epoch !== sessionEpoch) return
    const path = readString(asRecord(data.state), 'screenshot_path')
    if (path) {
      chatStore.setScreenshotPath(path)
      screenshotTime.value = new Date().toLocaleTimeString()
    }
  } catch (error) {
    console.error('获取截图状态失败:', error)
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function readString(record: Record<string, unknown> | null, key: string): string | undefined {
  const value = record?.[key]
  return typeof value === 'string' ? value : undefined
}

function readSkillCandidates(plan: unknown): SkillCandidate[] {
  const metadata = asRecord(asRecord(plan)?.metadata)
  const facts = asRecord(metadata?.skill_candidates)
  if (facts?.enabled !== true || !Array.isArray(facts.provided)) return []
  return facts.provided.flatMap(value => {
    const item = asRecord(value)
    const id = readString(item, 'skill_id')
    const version = readString(item, 'version')
    if (!id || !version) return []
    const sources = Array.isArray(item?.provenance) ? item.provenance.flatMap(value => {
      const source = asRecord(value)
      const taskId = readString(source, 'task_id')
      if (!taskId) return []
      const evidenceUrl = readString(source, 'evidence_url')
      return [{
        taskId,
        url: evidenceUrl?.startsWith('https://github.com/') ? evidenceUrl : null,
        qgisVersion: readString(source, 'qgis_version') ?? null,
      }]
    }) : []
    // Historical TaskPlan metadata was model-controlled. Render references,
    // never use copied review fields as proof of human approval.
    const catalogFiles: Record<string, string> = {
      'qwa-load': 'load', 'qwa-reproject': 'reproject', 'qwa-buffer': 'buffer',
      'qwa-clip': 'clip', 'qwa-dissolve': 'dissolve', 'qwa-statistics': 'statistics',
    }
    const file = catalogFiles[id]
    const catalogUrl = file
      ? `https://github.com/init-wei/qgis-workflow-agent/blob/codex/v1-refactor/agent/skills/${file}.json`
      : null
    return [{ id, version, sources, catalogUrl }]
  })
}

function isTaskPlan(value: unknown): value is TaskPlan {
  const record = asRecord(value)
  return typeof record?.task_id === 'string'
    && typeof record.revision === 'number'
    && typeof record.review_status === 'string'
    && Array.isArray(record.required_inputs)
    && Array.isArray(record.expected_outputs)
    && Array.isArray(record.steps)
    && Array.isArray(record.tool_calls)
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
