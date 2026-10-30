<template>
  <div class="min-h-screen bg-dark-bg text-dark-text p-8">
    <div class="max-w-4xl mx-auto">
      <h1 class="text-3xl font-semibold mb-8">⚙️ 设置</h1>

      <el-card class="mb-6 bg-dark-bgDark border-dark-border">
        <template #header>
          <div class="flex items-center gap-2">
            <el-icon><Connection /></el-icon>
            <span class="font-semibold">模型连接</span>
          </div>
        </template>
        <p class="text-sm text-dark-textSecondary leading-relaxed">
          模型 API 地址、模型名称和密钥由服务端环境配置。浏览器不会收集或保存 API Key。
        </p>
      </el-card>

      <el-card class="mb-6 bg-dark-bgDark border-dark-border">
        <template #header>
          <div class="flex items-center gap-2">
            <el-icon><Brush /></el-icon>
            <span class="font-semibold">界面设置</span>
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
        aria-label="返回聊天"
        @click="router.push('/')"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElButton,
  ElCard,
  ElForm,
  ElFormItem,
  ElIcon,
  ElMessage,
  ElOption,
  ElRadio,
  ElRadioGroup,
  ElSelect
} from 'element-plus'
import { ArrowLeft, Brush, Connection } from '@element-plus/icons-vue'
import { useSettingsStore } from '../stores/settings'

const router = useRouter()
const settingsStore = useSettingsStore()

const settingsForm = ref({
  theme: settingsStore.theme,
  language: settingsStore.language
})

function handleSave() {
  settingsStore.setTheme(settingsForm.value.theme)
  settingsStore.setLanguage(settingsForm.value.language)
  ElMessage.success('设置已保存')
}

function handleReset() {
  settingsStore.resetToDefaults()
  settingsForm.value = {
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

:deep(.el-input__wrapper),
:deep(.el-select__wrapper) {
  background-color: var(--tw-colors-dark-bgLight);
  box-shadow: 0 0 0 1px var(--tw-colors-dark-border) inset;
}
</style>
