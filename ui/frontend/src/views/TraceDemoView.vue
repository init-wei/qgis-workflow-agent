<template>
  <div class="min-h-screen bg-dark-bg text-dark-text">
    <aside class="fixed inset-y-0 left-0 w-80 bg-dark-bgDark border-r border-dark-border flex flex-col">
      <div class="p-5 border-b border-dark-border flex items-center justify-between gap-3">
        <div>
          <h1 class="text-xl font-semibold text-primary-600">Trace Demo</h1>
          <p class="text-sm text-dark-textSecondary mt-1">Phase 9/10 replay fixtures</p>
        </div>
        <el-button :icon="Refresh" circle size="small" @click="loadCases" />
      </div>

      <div class="flex-1 overflow-y-auto p-4 space-y-2">
        <button
          v-for="demoCase in cases"
          :key="demoCase.case_id"
          class="w-full text-left p-3 rounded-lg border transition-colors"
          :class="selectedCaseId === demoCase.case_id
            ? 'border-primary-600 bg-primary-600/15'
            : 'border-dark-border bg-dark-bgLight hover:border-primary-600'"
          @click="selectCase(demoCase.case_id)"
        >
          <div class="flex items-center justify-between gap-2">
            <span class="font-semibold truncate">{{ demoCase.title }}</span>
            <span class="text-xs px-2 py-0.5 rounded bg-green-500/15 text-green-300">
              {{ demoCase.trace_status }}
            </span>
          </div>
          <p class="text-xs text-dark-textSecondary mt-2 leading-relaxed">
            {{ demoCase.summary }}
          </p>
          <div class="mt-3 flex flex-wrap gap-1">
            <span
              v-for="tag in demoCase.tags"
              :key="tag"
              class="text-[11px] px-2 py-0.5 rounded bg-dark-bg border border-dark-border"
            >
              {{ tag }}
            </span>
          </div>
        </button>
      </div>

      <div class="p-4 border-t border-dark-border">
        <el-button :icon="ArrowLeft" class="w-full" @click="$router.push('/')">
          返回
        </el-button>
      </div>
    </aside>

    <main class="ml-80 min-h-screen p-6">
      <div v-if="loading" class="h-[calc(100vh-3rem)] flex items-center justify-center">
        <el-skeleton :rows="8" animated class="max-w-3xl w-full" />
      </div>

      <div v-else-if="detail && trace" class="max-w-7xl mx-auto space-y-6">
        <section class="border-b border-dark-border pb-5">
          <div class="flex items-start justify-between gap-4">
            <div>
              <h2 class="text-2xl font-semibold">{{ detail.case.title }}</h2>
              <p class="text-dark-textSecondary mt-2 max-w-3xl">{{ detail.case.summary }}</p>
            </div>
            <div class="grid grid-cols-3 gap-3 text-center">
              <div class="bg-dark-bgDark border border-dark-border rounded-lg px-4 py-3">
                <div class="text-xl font-semibold text-primary-600">{{ detail.replay.attempts_used }}</div>
                <div class="text-xs text-dark-textSecondary">Attempts</div>
              </div>
              <div class="bg-dark-bgDark border border-dark-border rounded-lg px-4 py-3">
                <div class="text-xl font-semibold text-primary-600">{{ detail.replay.artifact_count }}</div>
                <div class="text-xs text-dark-textSecondary">Artifacts</div>
              </div>
              <div class="bg-dark-bgDark border border-dark-border rounded-lg px-4 py-3">
                <div class="text-xl font-semibold text-primary-600">{{ detail.replay.status }}</div>
                <div class="text-xs text-dark-textSecondary">Replay</div>
              </div>
            </div>
          </div>
        </section>

        <section class="grid grid-cols-1 xl:grid-cols-[minmax(0,1.4fr)_minmax(360px,0.8fr)] gap-6">
          <div class="space-y-4">
            <h3 class="text-lg font-semibold">Execution Trace</h3>
            <article
              v-for="attempt in trace.attempts"
              :key="attempt.attempt_id"
              class="bg-dark-bgDark border border-dark-border rounded-lg p-4"
            >
              <div class="flex items-center justify-between gap-3">
                <div class="min-w-0">
                  <div class="font-semibold truncate">
                    Step {{ attempt.step_id }} · {{ attempt.tool_call.tool_name }}
                  </div>
                  <div class="text-xs text-dark-textSecondary mt-1">
                    {{ attempt.attempt_id }}
                  </div>
                </div>
                <span
                  class="text-xs px-2 py-1 rounded"
                  :class="attempt.status === 'success'
                    ? 'bg-green-500/15 text-green-300'
                    : 'bg-red-500/15 text-red-300'"
                >
                  {{ attempt.status }}
                </span>
              </div>

              <div class="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div>
                  <div class="text-xs uppercase text-dark-textSecondary mb-2">Arguments</div>
                  <pre class="code-block">{{ formatJson(attempt.tool_call.arguments) }}</pre>
                </div>
                <div>
                  <div class="text-xs uppercase text-dark-textSecondary mb-2">Observation</div>
                  <pre class="code-block">{{ formatJson(attempt.observation) }}</pre>
                </div>
              </div>

              <div v-if="attempt.metadata && Object.keys(attempt.metadata).length" class="mt-4">
                <div class="text-xs uppercase text-dark-textSecondary mb-2">Metadata</div>
                <pre class="code-block">{{ formatJson(attempt.metadata) }}</pre>
              </div>
            </article>
          </div>

          <div class="space-y-4">
            <section class="bg-dark-bgDark border border-dark-border rounded-lg p-4">
              <h3 class="text-lg font-semibold">Replay Summary</h3>
              <pre class="code-block mt-3">{{ formatJson(detail.replay.trace_summary) }}</pre>
            </section>

            <section
              v-if="detail.replay.repair_summaries.length"
              class="bg-dark-bgDark border border-dark-border rounded-lg p-4"
            >
              <h3 class="text-lg font-semibold">Repair Evidence</h3>
              <pre class="code-block mt-3">{{ formatJson(detail.replay.repair_summaries) }}</pre>
            </section>

            <section
              v-if="detail.replay.skill_draft"
              class="bg-dark-bgDark border border-dark-border rounded-lg p-4"
            >
              <h3 class="text-lg font-semibold">SkillDraft Preview</h3>
              <div class="mt-3 space-y-3">
                <div>
                  <div class="text-xs uppercase text-dark-textSecondary mb-2">Variables</div>
                  <pre class="code-block">{{ formatJson(detail.replay.skill_draft.variables) }}</pre>
                </div>
                <div v-if="detail.replay.reuse_preview">
                  <div class="text-xs uppercase text-dark-textSecondary mb-2">Reuse Preview</div>
                  <pre class="code-block">{{ formatJson(detail.replay.reuse_preview) }}</pre>
                </div>
              </div>
            </section>

          </div>
        </section>
      </div>

      <el-empty v-else description="No demo case selected" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElButton, ElEmpty, ElMessage, ElSkeleton } from 'element-plus'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import { getDemoCase, getDemoCases, getDemoCaseTrace } from '../api'
import type { DemoCaseDetail, DemoCaseSummary } from '../types'

const cases = ref<DemoCaseSummary[]>([])
const selectedCaseId = ref('')
const detail = ref<DemoCaseDetail | null>(null)
const trace = ref<Record<string, any> | null>(null)
const loading = ref(false)

onMounted(loadCases)

async function loadCases() {
  loading.value = true
  try {
    const response = await getDemoCases()
    cases.value = response.cases
    const nextCaseId = selectedCaseId.value || response.cases[0]?.case_id || ''
    if (nextCaseId) {
      await selectCase(nextCaseId)
    }
  } catch (error) {
    console.error('Failed to load demo cases:', error)
    ElMessage.error('加载 demo cases 失败')
  } finally {
    loading.value = false
  }
}

async function selectCase(caseId: string) {
  selectedCaseId.value = caseId
  loading.value = true
  try {
    const [caseDetail, traceResponse] = await Promise.all([
      getDemoCase(caseId),
      getDemoCaseTrace(caseId)
    ])
    detail.value = caseDetail
    trace.value = traceResponse.trace
  } catch (error) {
    console.error('Failed to load demo case:', error)
    ElMessage.error('加载 trace demo 失败')
  } finally {
    loading.value = false
  }
}

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2)
}
</script>

<style scoped>
.code-block {
  max-height: 24rem;
  overflow: auto;
  border-radius: 0.5rem;
  border: 1px solid rgb(75 85 99 / 0.45);
  background: rgb(15 23 42 / 0.75);
  padding: 0.75rem;
  font-size: 0.75rem;
  line-height: 1.45;
  color: rgb(226 232 240);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
