<template>
  <div class="min-h-screen bg-dark-bg text-dark-text p-8">
    <div class="max-w-4xl mx-auto">
      <h1 class="text-3xl font-semibold mb-8">⚙️ 设置</h1>

      <el-card class="mb-6 bg-dark-bgDark border-dark-border">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-semibold">API 配置</span>
            <el-icon><Connection /></el-icon>
          </div>
        </template>
        
        <el-form :model="settingsForm" label-width="120px">
          <el-form-item label="模型 API 地址">
            <el-input v-model="settingsForm.apiUrl" placeholder="留空则使用后端 env（如 https://api.openai.com/v1）" />
          </el-form-item>
          
          <el-form-item label="模型 API Key">
            <el-input v-model="settingsForm.apiKey" type="password" show-password placeholder="留空则使用后端 env" />
          </el-form-item>
          
          <el-form-item label="模型">
            <el-input v-model="settingsForm.model" placeholder="留空则使用后端 env（如 gpt-4o / deepseek-chat）" />
          </el-form-item>
        </el-form>
      </el-card>

      <el-card class="mb-6 bg-dark-bgDark border-dark-border">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-semibold">模型参数</span>
            <el-icon><Setting /></el-icon>
          </div>
        </template>
        
        <el-form :model="settingsForm" label-width="140px">
          <el-form-item label="Temperature">
            <el-slider v-model="settingsForm.temperature" :min="0" :max="2" :step="0.1" :format-tooltip="formatTooltip" />
            <div class="text-sm text-dark-textSecondary mt-1">控制随机性，值越高越随机</div>
          </el-form-item>
          
          <el-form-item label="Max Tokens">
            <el-input-number v-model="settingsForm.maxTokens" :min="100" :max="8000" :step="100" />
            <div class="text-sm text-dark-textSecondary mt-1">最大生成 token 数量</div>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card class="mb-6 bg-dark-bgDark border-dark-border">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="font-semibold">界面设置</span>
            <el-icon><Brush /></el-icon>
          </div>
        </template>
        
        <el-form :model="settingsForm" label-width="120px">
          <el-form-item label="主题">
            <el-radio-group v-model="settingsForm.theme">
              <el-radio label="dark">深色</el-radio>
              <el-radio label="light">浅色</el-radio>
            </el-radio-group>
          </el-form-item>
          
          <el-form-item label="语言">
            <el-select v-model="settingsForm.language" placeholder="选择语言">
              <el-option label="简体中文" value="zh-CN" />
              <el-option label="English" value="en-US" />
            </el-select>
          </el-form-item>
        </el-form>
      </el-card>

      <div class="flex justify-end gap-4">
        <el-button @click="handleReset">重置默认</el-button>
        <el-button type="primary" @click="handleSave">保存设置</el-button>
      </div>

      <el-button 
        class="fixed bottom-6 left-6" 
        circle 
        :icon="ArrowLeft" 
        @click="$router.push('/')"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElCard, ElForm, ElFormItem, ElInput, ElSelect, ElOption, ElRadioGroup, ElRadio, ElSlider, ElInputNumber, ElButton, ElIcon, ElMessage } from 'element-plus'
import { Connection, Setting, Brush, ArrowLeft } from '@element-plus/icons-vue'
import { useSettingsStore } from '../stores/settings'

const router = useRouter()
const settingsStore = useSettingsStore()

// 表单数据
const settingsForm = ref({
  apiUrl: settingsStore.apiUrl,
  apiKey: settingsStore.apiKey,
  model: settingsStore.model,
  temperature: settingsStore.temperature,
  maxTokens: settingsStore.maxTokens,
  theme: settingsStore.theme,
  language: settingsStore.language
})

function formatTooltip(value: number) {
  return value.toFixed(1)
}

async function handleSave() {
  settingsStore.setApiUrl(settingsForm.value.apiUrl)
  settingsStore.setApiKey(settingsForm.value.apiKey)
  settingsStore.setModel(settingsForm.value.model)
  settingsStore.setTemperature(settingsForm.value.temperature)
  settingsStore.setMaxTokens(settingsForm.value.maxTokens)
  settingsStore.setTheme(settingsForm.value.theme)
  settingsStore.setLanguage(settingsForm.value.language)
  
  ElMessage.success('设置已保存')
}

function handleReset() {
  settingsStore.resetToDefaults()
  
  // 更新表单数据
  settingsForm.value = {
    apiUrl: settingsStore.apiUrl,
    apiKey: settingsStore.apiKey,
    model: settingsStore.model,
    temperature: settingsStore.temperature,
    maxTokens: settingsStore.maxTokens,
    theme: settingsStore.theme,
    language: settingsStore.language
  }
  
  ElMessage.success('已重置为默认设置')
}
</script>

<style scoped>
:deep(.el-card) {
  background-color: var(--tw-colors-dark-bgDark);
  border-color: var(--tw-colors-dark-border);
}

:deep(.el-form-item__label) {
  color: var(--tw-colors-dark-text);
}

:deep(.el-input__inner),
:deep(.el-textarea__inner) {
  background-color: var(--tw-colors-dark-bgLight);
  border-color: var(--tw-colors-dark-border);
  color: var(--tw-colors-dark-text);
}

:deep(.el-input__inner:focus),
:deep(.el-textarea__inner:focus) {
  border-color: var(--tw-colors-primary-600);
}
</style>
