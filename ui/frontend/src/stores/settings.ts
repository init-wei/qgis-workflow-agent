import { defineStore } from 'pinia'
import { ref } from 'vue'

type Theme = 'dark' | 'light'
type Language = 'zh-CN' | 'en-US'

function readTheme(): Theme {
  return localStorage.getItem('theme') === 'light' ? 'light' : 'dark'
}

function readLanguage(): Language {
  return localStorage.getItem('language') === 'en-US' ? 'en-US' : 'zh-CN'
}

export const useSettingsStore = defineStore('settings', () => {
  const theme = ref<Theme>(readTheme())
  const language = ref<Language>(readLanguage())

  function setTheme(value: Theme) {
    theme.value = value
    localStorage.setItem('theme', value)
    document.documentElement.classList.toggle('dark', value === 'dark')
  }

  function setLanguage(value: Language) {
    language.value = value
    localStorage.setItem('language', value)
  }

  function resetToDefaults() {
    setTheme('dark')
    setLanguage('zh-CN')
  }

  return {
    theme,
    language,
    setTheme,
    setLanguage,
    resetToDefaults
  }
})
