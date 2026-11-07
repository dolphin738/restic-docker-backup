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
  /** 抑制 401 自动跳登录（默认 false = 既有行为）。
   *
   * 唯一消费位是「全站备份包导入」：导入会清空服务端 `sessions` 表，本会话随之作废，
   * 但**导入报告本身就是用户最需要看到的东西**（含回滚点目录名——误导入后唯一的
   * 回退依据）。此时顶栏 15s 轮询拿到的 401 若照旧触发跳转，刚渲染的报告会在几秒内
   * 被冲掉并跳到 /login。故导入成功后临时置位本标志：401 仍清本地会话态
   * （`clearMe`），但**不跳转**，由页面上的「前往登录」按钮显式带走用户。
   *
   * 置位者负责复位：组件卸载时清（离开该页后其他页面的 401 行为必须回到原样）。 */
  const suppressAuthRedirect = ref(false)

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

  /** 置位/清除「抑制 401 跳转」标志（见 `suppressAuthRedirect` 说明）。 */
  function setSuppressAuthRedirect(value: boolean): void {
    suppressAuthRedirect.value = value
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
    suppressAuthRedirect,
    setMe,
    clearMe,
    setBootstrapSteps,
    setSuppressAuthRedirect,
    setThemePreference,
    setLocalePreference,
    setCurrentRepoId,
    setMessageApi,
    notify,
    notifyError,
  }
})
