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
/** R-023：当前选中的仓库 id（多仓库视图）；缺省 default（后端自动播种的默认仓库）。 */
const CURRENT_REPO_KEY = 'rw.currentRepo'

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

function readCurrentRepoId(): string {
  try {
    const stored = localStorage.getItem(CURRENT_REPO_KEY)
    if (stored) {
      return stored
    }
  } catch {
    // 忽略存储不可用
  }
  return 'default'
}

export const useUiStore = defineStore('ui', () => {
  const me = ref<MeResponse | null>(null)
  const bootstrapSteps = ref<string[]>([])
  const messageApi = ref<MessageApi | null>(null)
  const themePreference = ref<ThemePreference>(readThemePreference())
  const locale = ref<AppLocale>(currentLocale())
  /** R-023：当前仓库 id，驱动所有读写请求经 http 层注入的 X-Repo-Id 头。 */
  const currentRepoId = ref<string>(readCurrentRepoId())

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

  /** R-023：切换当前仓库（持久化 + 供 http 层注入 X-Repo-Id）。 */
  function setCurrentRepoId(id: string): void {
    currentRepoId.value = id
    try {
      localStorage.setItem(CURRENT_REPO_KEY, id)
    } catch {
      // 忽略持久化失败
    }
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
    currentRepoId,
    setMe,
    clearMe,
    setBootstrapSteps,
    setThemePreference,
    setLocalePreference,
    setCurrentRepoId,
    setMessageApi,
    notify,
    notifyError,
  }
})
