/**
 * 顶栏轮询的 enabled 门（W6 收尾第二项：401 缺陷「方案 2」的单测）。
 *
 * 缺陷背景：全站备份包导入成功后服务端清空 sessions，本会话作废。此前后台的
 * repoStatusQuery / activeJobsQuery 仍在 15s 轮询，拿到的 401 会在约 7.7s 后把刚渲染的
 * 导入报告（含回滚点目录名——误导入后唯一回退依据）冲掉并跳 /login。
 * 修法：两个 useQuery 各加 enabled: () => ui.me !== null——登出即停轮询。
 *
 * 本 spec 锁死这个门真的关得住：
 *   ① 已登录 → 推进 15s → 两个数据函数都被再次调用（证明轮询确实在跑，不是门把它们全关了）；
 *   ② 已登录跑起来后 clearMe() → 推进 30s → 调用次数不再增长（门动态关闭，即缺陷现场）；
 *   ③ 挂载时就未登录 → 一次都不调用（门从一开始就关着）。
 *
 * 为什么用 fake timers：refetchInterval 是 vue-query 内部经 timeoutManager 调的全局
 * setInterval（node_modules/@tanstack/query-core/build/modern/timeoutManager.js），
 * vi.useFakeTimers() 拦的正是这一层，故不需真等 15 秒。用 Async 版本的
 * advanceTimersByTimeAsync 而非同步版：定时器回调里是 async 的 #executeFetch，
 * 同步推进只会把 setInterval 触发掉却不等 fetch 的 promise 链落定，断言会看到旧次数。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import BasicLayout from '@/layouts/BasicLayout.vue'
import { i18n } from '@/i18n'
import { useUiStore } from '@/stores/ui'
import { lightThemeOverrides } from '@/theme/tokens'
import * as jobsApi from '@/api/jobs'
import * as repoApi from '@/api/repo'

// 只 mock 数据函数（不做 fetch 层 stub）：断言对象就是这两个函数被调用的次数。
vi.mock('@/api/jobs', () => ({
  listActiveJobs: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })),
  listJobsHistory: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0 })),
  createJob: vi.fn(async () => ({ id: 'job-1' })),
  cancelJob: vi.fn(async () => ({ ok: true })),
  getJob: vi.fn(async () => ({})),
  getJobLog: vi.fn(async () => ({ content: '', tail_bytes: 0, truncated: false })),
  cleanupJobs: vi.fn(async () => ({ deleted_jobs: 0, deleted_logs: 0 })),
}))

vi.mock('@/api/repo', () => ({
  listRepos: vi.fn(async () => []),
  getRepoStatus: vi.fn(async () => ({
    connected: true,
    configured: true,
    locked: false,
    last_error: null,
    capacity: null,
    failure_kind: null,
  })),
  createRepo: vi.fn(async () => ({ id: 'repo-new' })),
  deleteRepo: vi.fn(async () => ({ ok: true })),
  updateRepo: vi.fn(async () => ({ ok: true })),
  testRepo: vi.fn(async () => ({ ok: true, latency_ms: 1 })),
}))

vi.mock('@/api/auth', () => ({
  logout: vi.fn(async () => ({ ok: true })),
}))

const listActiveJobs = vi.mocked(jobsApi.listActiveJobs)
const getRepoStatus = vi.mocked(repoApi.getRepoStatus)

/** 挂 BasicLayout：providers 按 App.vue 的真实嵌套（config → message → dialog），
 *  router 记忆式，pinia 单独持有以便从用例里直接改登录态。 */
function mountLayout() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const ui = useUiStore()
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        // 不设 staleTime：refetchInterval 与 staleTime 无关，但保持默认能让间隔照常触发
        retry: false,
        refetchOnWindowFocus: false,
      },
    },
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
    ],
  })
  const wrapper = mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: {
      default: () =>
        h(NMessageProvider, null, {
          default: () => h(NDialogProvider, null, { default: () => h(BasicLayout) }),
        }),
    },
    global: { plugins: [i18n, pinia, [VueQueryPlugin, { queryClient }], router] },
  })
  return { wrapper, ui, router, queryClient }
}

/** 已登录态（ui.me 非 null 即 authReady 为真）。 */
function signIn(ui: ReturnType<typeof useUiStore>): void {
  ui.setMe({ authenticated: true, must_change_password: false, user: 'admin' })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  localStorage.clear()
  listActiveJobs.mockClear()
  getRepoStatus.mockClear()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('顶栏轮询的 enabled 门（登出即停）', () => {
  it('已登录：推进 15s → listActiveJobs 与 getRepoStatus 均被再次调用（轮询在跑）', async () => {
    const { wrapper, ui } = mountLayout()
    signIn(ui)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(0)

    const jobsAfterMount = listActiveJobs.mock.calls.length
    const statusAfterMount = getRepoStatus.mock.calls.length
    // 挂载即取首屏（enabled 为真 + 首次 fetch）
    expect(jobsAfterMount).toBeGreaterThanOrEqual(1)
    expect(statusAfterMount).toBeGreaterThanOrEqual(1)

    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()

    // 一个 15s 间隔过后，两者都必须再次被调用
    expect(listActiveJobs.mock.calls.length).toBeGreaterThan(jobsAfterMount)
    expect(getRepoStatus.mock.calls.length).toBeGreaterThan(statusAfterMount)
    wrapper.unmount()
  })

  it('轮询跑起来后 clearMe()：推进 30s，两者调用次数不再增长（门动态关闭）', async () => {
    const { wrapper, ui } = mountLayout()
    signIn(ui)
    await flushPromises()
    // 先真切到一个已经跑过至少一轮的状态，否则「不增长」可能是假象
    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()
    expect(listActiveJobs.mock.calls.length).toBeGreaterThanOrEqual(2)

    // 缺陷现场：导入成功 → sessions 作废 → clearMe()
    ui.clearMe()
    await flushPromises()
    const jobsAtLogout = listActiveJobs.mock.calls.length
    const statusAtLogout = getRepoStatus.mock.calls.length

    // 两个 15s 周期
    await vi.advanceTimersByTimeAsync(30_000)
    await flushPromises()

    expect(listActiveJobs.mock.calls.length).toBe(jobsAtLogout)
    expect(getRepoStatus.mock.calls.length).toBe(statusAtLogout)
    wrapper.unmount()
  })

  it('挂载时即未登录：一次都不调用（门从一开始就关着）', async () => {
    const { wrapper, ui } = mountLayout()
    ui.clearMe()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30_000)
    await flushPromises()

    expect(listActiveJobs).not.toHaveBeenCalled()
    expect(getRepoStatus).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
