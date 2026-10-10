/** 全局 UI 状态：me 缓存、主题偏好、语言、全局 message 出口。
 *
 * 约定：本 store **不缓存任何服务端业务数据**（仓库/任务/快照一律走 vue-query）。
 */

import { usePreferredDark } from '@vueuse/core'
import type { MessageApi } from 'naive-ui'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { MeResponse } from '@/api/types'
import { currentLocale, i18n, setLocale, type AppLocale } from '@/i18n'

export type ThemePreference = 'dark' | 'light' | 'auto'

const THEME_KEY = 'rw.theme'

function readThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'dark' || stored === 'light' || stored === 'auto') {
      return stored
    }
  } catch {
    // 忽略存储不可用
  }
  return 'dark' // 深色为默认
}

export const useUiStore = defineStore('ui', () => {
  const me = ref<MeResponse | null>(null)
  const bootstrapSteps = ref<string[]>([])
  const messageApi = ref<MessageApi | null>(null)
  const themePreference = ref<ThemePreference>(readThemePreference())
  const locale = ref<AppLocale>(currentLocale())

  const systemPrefersDark = usePreferredDark()
  const isDark = computed(() =>
    themePreference.value === 'auto' ? systemPrefersDark.value : themePreference.value === 'dark',
  )

  function setMe(value: MeResponse): void {
    me.value = value
    bootstrapSteps.value = []
  }

  function clearMe(): void {
    me.value = null
  }

  function setBootstrapSteps(steps: string[]): void {
    bootstrapSteps.value = steps
  }

  function setThemePreference(preference: ThemePreference): void {
    themePreference.value = preference
    try {
      localStorage.setItem(THEME_KEY, preference)
    } catch {
      // 忽略持久化失败
    }
  }

  function setLocalePreference(value: AppLocale): void {
    locale.value = value
    setLocale(value)
  }

  /** 由 App.vue 树内的页面（layout / login / bootstrap）注册，卸载时置空。 */
  function setMessageApi(api: MessageApi | null): void {
    messageApi.value = api
  }

  function notify(level: 'success' | 'error' | 'warning' | 'info', text: string): void {
    if (!messageApi.value) {
      console.warn('[ui] message api 未注册：', text)
      return
    }
    messageApi.value[level](text)
  }

  /** 错误码 → 词条；缺失时回退后端 message，再回退 UNKNOWN。 */
  function notifyError(code: string, fallback?: string): void {
    const key = `errors.${code}`
    const translated = i18n.global.t(key)
    const text = translated === key ? fallback ?? i18n.global.t('errors.UNKNOWN') : translated
    notify('error', text)
  }

  return {
    me,
    bootstrapSteps,
    isDark,
    themePreference,
    locale,
    setMe,
    clearMe,
    setBootstrapSteps,
    setThemePreference,
    setLocalePreference,
    setMessageApi,
    notify,
    notifyError,
  }
})
