import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'Chat',
    component: () => import('../views/ChatView.vue')
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/SettingsView.vue')
  },
  {
    path: '/trace-demo',
    name: 'TraceDemo',
    component: () => import('../views/TraceDemoView.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
