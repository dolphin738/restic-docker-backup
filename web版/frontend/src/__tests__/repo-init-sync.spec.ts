/**
 * 缺陷 issue 2 回归（建仓闭环同步）：初始化完成后返回仓库列表，仓库须即时显示「已挂载」
 * 且「初始化仓库」按钮消失——不依赖手动刷新页面。
 *
 * 关键：既有 repo-page-setup-flow 把 useJobStream 替身成 `start: () => {}`，从未走
 * 「done → onInitDone → reposQuery.refetch() + 失效行状态」这条同步链。本规格专门覆盖它，
 * 锁死「创建 → 设密码 → 初始化 → 返回列表」终态的界面一致性。
 *
 * 断言只走 DOM 文案（i18n zh-CN），不依赖组件内部结构。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RepoPage from '@/pages/repo/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const DONE_PAYLOAD = {
  status: 'success' as const,
  exit_code: 0,
  snapshot_id: null,
  skipped_count: 0,
  bytes_done: null,
  files_done: null,
  summary: null,
}

const mocks = vi.hoisted(() => ({
  passfile: 'configured' as 'missing' | 'configured',
  repos: [
    {
      id: 'default',
      name: '默认仓库',
      type: 'local',
      url: '/repo/default',
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'configured' as 'missing' | 'configured',
      created_at: '2026-10-01T00:00:00Z',
    },
  ] as Array<Record<string, unknown>>,
  /** 行状态探测结论：初始为「未初始化」，初始化完成后被改成已连接。 */
  rowStatus: { connected: false, failure_kind: 'not_initialized' } as Record<string, unknown>,
  testResult: {
    ok: false,
    latency_ms: 12,
    reason: '仓库不存在或未初始化（请检查 repo.url，或先执行 init）',
    failure_kind: 'not_initialized',
  } as Record<string, unknown>,
}))

vi.mock('@/api/repo', () => ({
  listRepos: vi.fn(async () => mocks.repos.map((r) => ({ ...r, passfile_status: mocks.passfile }))),
  getRepo: vi.fn(async () => ({ ...mocks.repos[0], passfile_status: mocks.passfile })),
  getRepoStatus: vi.fn(async () => ({ ...mocks.rowStatus })),
  getRepoUsage: vi.fn(async () => ({ repo_id: 'x', mirror_refs: [], snapshot_total: 12 })),
  listRcloneRemotes: vi.fn(async () => ({ conf_present: false, remotes: [] })),
  listRcloneConfigs: vi.fn(async () => ({
    conf_path: '/etc/rclone/rclone.conf',
    conf_present: false,
    configs: [],
    available: true,
    reason: null,
  })),
  createRepo: vi.fn(async () => ({ id: 'repo-new' })),
  deleteRepo: vi.fn(async () => ({ ok: true })),
  updateRepo: vi.fn(async () => ({ ok: true })),
  testRepo: vi.fn(async () => mocks.testResult),
  manualCopy: vi.fn(async () => ({ ok: true, exit_code: 0, message: '' })),
  rotateRepoKey: vi.fn(async () => ({ ok: true })),
  rebuildRepo: vi.fn(async () => ({ ok: true, generated: false, password: null })),
  setRepoPassword: vi.fn(async () => ({ generated: false, password: null })),
  suggestRepoId: vi.fn(async () => ({ id: 'repo-auto' })),
}))

vi.mock('@/api/backupTasks', () => ({
  listBackupTasks: vi.fn(async () => ({ items: [], total: 0 })),
  deleteBackupTask: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/jobs', () => ({
  createJob: vi.fn(async () => ({ id: 'job-1' })),
  cancelJob: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(async () => ({ items: [] })),
  putConfig: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/fs', () => ({
  browseFs: vi.fn(async () => ({ path: '/', mounts: [], items: [], next_cursor: null })),
}))

vi.mock('@/api/settings', () => ({
  getCacheUsage: vi.fn(async () => ({
    size_bytes: 1024,
    items_count: 3,
    path: '/cache',
    measured_at: '2026-10-19T08:00:00Z',
    truncated: false,
  })),
  clearCache: vi.fn(async () => ({ ok: true })),
}))

// 关键替身：复刻真实 useJobStream 的「挂载即启动、作业已终态则立刻收尾」语义——
// 真实实现靠 watch(immediate) → start() → getJob 命中 TERMINAL_STATUSES → finishTerminal
// → options.onDone（异步，因 getJob 是网络请求）。此处用「挂载微任务」模拟该异步抵达，
// 保证 @done → onInitDone 在组件挂载后、下一个微任务里传播，与浏览器 EventSource 时序一致。
// jobId 为空（unlock/repair/recover 等未触发的进度卡）时不触发，避免误伤其它 done 处理器。
vi.mock('@/composables/useJobStream', async () => {
  const vue = await import('vue')
  const { ref, toValue } = vue
  const fire = (options: { onDone?: (p: unknown) => void }, id: unknown): void => {
    if (id) {
      Promise.resolve().then(() => {
        options.onDone?.(DONE_PAYLOAD)
      })
    }
  }
  return {
    useJobStream: (jobId: unknown, options: { onDone?: (p: unknown) => void } = {}) => {
      const id = toValue(jobId as never)
      fire(options, id)
      return {
        state: ref('closed'),
        lastEventId: ref(0),
        logs: ref([]),
        start: () => fire(options, id),
        stop: () => {},
      }
    },
  }
})

function mountRepoPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: RepoPage }],
  })
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(RepoPage) }) },
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router] },
  })
}

function buttonByText(w: ReturnType<typeof mountRepoPage>, label: string) {
  return w.findAll('button').find((b) => b.text() === label)
}

function bodyButton(label: string): HTMLButtonElement | undefined {
  return Array.from(document.body.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === label,
  )
}

async function clickBody(label: string): Promise<void> {
  bodyButton(label)?.click()
  await flushPromises()
}

async function passConfirmGate(keyword: string, confirmLabel = '确认执行'): Promise<boolean> {
  await clickBody('下一步')
  const input = document.body.querySelector('input')
  if (!input) {
    return false
  }
  input.value = keyword
  input.dispatchEvent(new Event('input'))
  await flushPromises()
  await clickBody('下一步')
  const confirmBtn = bodyButton(confirmLabel)
  if (!confirmBtn) {
    return false
  }
  confirmBtn.click()
  await flushPromises()
  return true
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  localStorage.clear()
  // 初始：已配置密码（编辑态 init 按钮可用），但行状态为「未初始化」（init 按钮仍显示）。
  mocks.passfile = 'configured'
  mocks.rowStatus = { connected: false, failure_kind: 'not_initialized' }
  mocks.repos = [
    {
      id: 'default',
      name: '默认仓库',
      type: 'local',
      url: '/repo/default',
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'configured',
      created_at: '2026-10-01T00:00:00Z',
    },
  ]
  mocks.testResult = {
    ok: false,
    latency_ms: 12,
    reason: '仓库不存在或未初始化（请检查 repo.url，或先执行 init）',
    failure_kind: 'not_initialized',
  }
})

describe('仓库页：初始化完成后列表即时同步（缺陷 issue 2）', () => {
  it('编辑态初始化完成 → 返回列表：仓库显示「已挂载」且不再有「初始化仓库」按钮', async () => {
    const w = mountRepoPage()
    await flushPromises()

    // 进入编辑态并跑一次连接测试，停在「未初始化」失败结论上（init 按钮因此可用）。
    await w.findAll('button').find((b) => b.text() === '管理')?.trigger('click')
    await flushPromises()
    await w.findAll('button').find((b) => b.text() === '立即测试')?.trigger('click')
    await flushPromises()

    // 编辑态：init 按钮可见且可用（未初始化）。
    expect(buttonByText(w, '初始化仓库')?.attributes('disabled')).toBeUndefined()

    // 关键：在触发 done 之前把行状态结论改成「已连接」——模拟 init 作业已改变仓库可连性，
    // onInitDone 失效行状态后重取应拿到已连接，init 按钮须消失。
    mocks.rowStatus = { connected: true, failure_kind: null }

    // 提交初始化 → 确认门 → createJob → 编辑态 JobProgress 挂载 → start()（微任务）触发
    // onDone → onInitDone（失效列表 + 行状态、清 initTargetId / initJobMap）。
    buttonByText(w, '初始化仓库')?.trigger('click')
    await flushPromises()
    expect(await passConfirmGate('INIT')).toBe(true)
    await flushPromises()
    await flushPromises()

    // 返回列表态。
    await buttonByText(w, '返回列表')?.trigger('click')
    await flushPromises()

    // 列表：该仓库行应显示「已挂载」标签，且不应再有「初始化仓库」按钮。
    expect(w.text()).toContain('已挂载')
    expect(buttonByText(w, '初始化仓库')).toBeUndefined()
    w.unmount()
  })
})
