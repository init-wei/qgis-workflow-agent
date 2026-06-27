import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useSettingsStore = defineStore('settings', () => {
  // 默认设置
  const defaultSettings = {
    apiUrl: '',
    apiKey: '',
    model: '',
    temperature: 0.7,
    maxTokens: 20000,
    theme: 'dark',
    language: 'zh-CN'
  }

  // 状态
  const apiUrl = ref(localStorage.getItem('apiUrl') || defaultSettings.apiUrl)
  const apiKey = ref(localStorage.getItem('apiKey') || defaultSettings.apiKey)
  const model = ref(localStorage.getItem('model') || defaultSettings.model)
  const temperature = ref(parseFloat(localStorage.getItem('temperature') || defaultSettings.temperature.toString()))
  const maxTokens = ref(parseInt(localStorage.getItem('maxTokens') || defaultSettings.maxTokens.toString()))
  const theme = ref(localStorage.getItem('theme') || defaultSettings.theme)
  const language = ref(localStorage.getItem('language') || defaultSettings.language)

  // Actions
  function setApiUrl(value: string) {
    apiUrl.value = value
    localStorage.setItem('apiUrl', value)
  }

  function setApiKey(value: string) {
    apiKey.value = value
    localStorage.setItem('apiKey', value)
  }

  function setModel(value: string) {
    model.value = value
    localStorage.setItem('model', value)
  }

  function setTemperature(value: number) {
    temperature.value = value
    localStorage.setItem('temperature', value.toString())
  }

  function setMaxTokens(value: number) {
    maxTokens.value = value
    localStorage.setItem('maxTokens', value.toString())
  }

  function setTheme(value: string) {
    theme.value = value
    localStorage.setItem('theme', value)
    document.documentElement.classList.toggle('dark', value === 'dark')
  }

  function setLanguage(value: string) {
    language.value = value
    localStorage.setItem('language', value)
  }

  function resetToDefaults() {
    setApiUrl(defaultSettings.apiUrl)
    setApiKey(defaultSettings.apiKey)
    setModel(defaultSettings.model)
    setTemperature(defaultSettings.temperature)
    setMaxTokens(defaultSettings.maxTokens)
    setTheme(defaultSettings.theme)
    setLanguage(defaultSettings.language)
  }

  return {
    // 状态
    apiUrl,
    apiKey,
    model,
    temperature,
    maxTokens,
    theme,
    language,
    // Actions
    setApiUrl,
    setApiKey,
    setModel,
    setTemperature,
    setMaxTokens,
    setTheme,
    setLanguage,
    resetToDefaults
  }
})
