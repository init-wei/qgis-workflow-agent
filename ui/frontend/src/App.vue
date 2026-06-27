<template>
  <div id="app" class="h-screen flex">
    <router-view />
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useSettingsStore } from './stores/settings'

const settingsStore = useSettingsStore()

onMounted(() => {
  // 应用主题
  document.documentElement.classList.toggle('dark', settingsStore.theme === 'dark')
})
</script>

<style>
#app {
  width: 100%;
  height: 100%;
}
</style>
