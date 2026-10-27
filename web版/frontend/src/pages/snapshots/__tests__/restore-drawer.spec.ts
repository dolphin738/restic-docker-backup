/**
 * 恢复流程并入快照页护栏（2026-10-28）。
 *
 * 存在理由：原 `/restore` 入口页 + `/restore/:id/*` 向导路由**整体删除**，
 * 三步流程改由本页右侧抽屉承载。这类「删页面 + 改入口」的改造，vue-tsc 与
 * 页面渲染都不会报错，坏起来全是静默的：
 *   ① 行内「恢复」/ 文件树「恢复此快照」点了没反应（抽屉没被唤醒）；
 *   ② 预选错快照（点 A 行恢复，抽屉里选中的是最新的 B）——恢复错版本后果严重；
 *   ③ 深链 `?restore=<id>` 不再自动开抽屉；
 *   ④ 关闭后 query 残留 → 刷新又自动弹抽屉，用户以为关不掉。
 *
 * DOM 纪律：抽屉由 naive `n-drawer` teleport 到 body，故抽屉内断言一律走
 * `document.body`（组件实例查询对 teleport 内容无效）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NButton, NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SnapshotsPage from '@/pages/snapshots/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  snapshots: { items: [] as Array<Record<string, unknown>>, total: 0 },
  files: { snapshot_id: 'snap-a', path: '/', items: [] as Array<Record<string, unknown>>, next_cursor: null, truncated: false },
}))

vi.mock('@/api/backupTasks', () => ({
  listBackupTasks: vi.fn(async () => ({ items: [], total: 0 })),
  getBackupTask: vi.fn(async (id: number) => ({ id, name: 't', tag: `task-${id}`, keep_tags: [] })),
}))

vi.mock('@/api/snapshots', () => ({
  listSnapshots: vi.fn(async () => mocks.snapshots),
  listSnapshotFiles: vi.fn(async () => mocks.files),
  previewForget: vi.fn(async () => ({ remove: [], remove_count: 0, keep_count: 0, group_count: 0, params: {}, truncated: false })),
  tagSnapshots: vi.fn(async () => ({ changed_count: 0, changed: [], truncated: false })),
  searchSnapshotFiles: vi.fn(async () => ({ snapshot_id: 'snap-a', query: '', items: [], total: 0, next_cursor: null })),
}))

vi.mock('@/api/jobs', () => ({
  createJob: vi.fn(async () => ({ id: 'job-1' })),
  getJobLog: vi.fn(async () => ({ id: 'job-1', log_path: '/x.log', tail_bytes: 0, content: '', truncated: false })),
}))

vi.mock('@/api/prune', () => ({
  previewPrune: vi.fn(async () => ({ would_remove_packs: 0, freed_bytes: 0, freed_text: '0 B', clean: true, lines: [] })),
}))

vi.mock('@/api/restore', () => ({
  INPLACE_TARGET_ROOT: '/sources',
  RESTORE_TARGET_ROOT: '/restore',
  previewRestore: vi.fn(async () => ({ items: [], overwrite_count: 0, total_bytes: 0, truncated: false, next_cursor: null })),
  restoreSnapshot: vi.fn(async () => ({ job_id: 'job-1', restore_target: '/restore/x' })),
}))

/** 两个快照：snap-b 更新（默认选中会选它），用来区分「预选」是否真的生效。 */
function seedSnapshots(): void {
  mocks.snapshots = {
    items: [
      { id: 'snap-b', time: '2026-10-21T10:00:00Z', host: 'nas', tags: [], paths: ['/var'], size: null },
      { id: 'snap-a', time: '2026-10-20T10:00:00Z', host: 'nas', tags: [], paths: ['/etc'], size: null },
    ],
    total: 2,
  }
}

let lastRouter: ReturnType<typeof createRouter> | null = null

async function mountPage(initialPath = '/snapshots') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/snapshots', name: 'snapshots', component: SnapshotsPage },
      { path: '/jobs', name: 'jobs', component: { template: '<div />' } },
      { path: '/repo', name: 'repo', component: { template: '<div />' } },
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
    ],
  })
  lastRouter = router
  await router.push(initialPath)
  await router.isReady()
  const wrapper = mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(SnapshotsPage) }) },
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router] },
  })
  await flushPromises()
  return wrapper
}

/** 恢复抽屉（teleport 到 body，按 class 定位）+ 其中显示的快照下拉文本。 */
function restoreDrawer(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('.snap-restore-drawer')
}

function selectedSnapshotText(): string {
  return restoreDrawer()?.querySelector('.n-base-selection')?.textContent ?? ''
}

/** 按文字点按钮（表格行内按钮由 DataTable render 生成，wrapper.find 拿不到 vnode 组件名）。 */
async function clickButtonByText(wrapper: Awaited<ReturnType<typeof mountPage>>, text: string): Promise<void> {
  const btn = wrapper.findAllComponents(NButton).find((item) => item.text().trim() === text)
  expect(btn, `未找到按钮：${text}`).toBeTruthy()
  await btn!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  seedSnapshots()
  // teleport 内容会跨用例累积，先清干净
  document.body.innerHTML = ''
})

describe('快照页 → 恢复抽屉（入口 / 预选 / 深链 / 关闭）', () => {
  it('① 行内「恢复」打开抽屉，且预选的是该行快照（不是最新的那个）', async () => {
    const wrapper = await mountPage()
    // 表格按时间降序：第 1 行 snap-b，第 2 行 snap-a（paths=/etc）
    const rows = wrapper.findAllComponents(NButton).filter((item) => item.text().trim() === '恢复')
    expect(rows.length).toBe(2)
    await rows[1].trigger('click')
    await flushPromises()

    expect(restoreDrawer()).not.toBeNull()
    // 预选生效：下拉显示 /etc（snap-a），若预选失效会退回最新的 /var（snap-b）
    expect(selectedSnapshotText()).toContain('/etc')
    expect(selectedSnapshotText()).not.toContain('/var')
  })

  it('② 文件树抽屉「恢复此快照」同样打开恢复抽屉', async () => {
    const wrapper = await mountPage()
    await clickButtonByText(wrapper, '浏览文件')
    // 文件树抽屉已开：其「恢复此快照」按钮在 body 里
    document.body.querySelectorAll<HTMLButtonElement>('button').forEach((btn) => {
      if (btn.textContent?.trim() === '恢复此快照') {
        btn.click()
      }
    })
    await flushPromises()
    expect(restoreDrawer()).not.toBeNull()
  })

  it('③ 深链 ?restore=<id>：进页即自动打开抽屉并预选', async () => {
    await mountPage('/snapshots?restore=snap-a')
    expect(restoreDrawer()).not.toBeNull()
    expect(selectedSnapshotText()).toContain('/etc')
  })

  it('④ 关闭抽屉后 query 清空（否则刷新会再次自动弹出）', async () => {
    await mountPage('/snapshots?restore=snap-a')
    expect(lastRouter!.currentRoute.value.query.restore).toBe('snap-a')

    // 面板「关闭」页脚按钮（步骤 1 的左侧按钮）
    const panel = document.body.querySelector('.snap-restore-drawer')
    const closeBtn = [...(panel?.querySelectorAll('button') ?? [])].find(
      (btn) => btn.textContent?.trim() === '关闭',
    )
    expect(closeBtn).toBeTruthy()
    closeBtn!.click()
    await flushPromises()

    expect(lastRouter!.currentRoute.value.query.restore).toBeUndefined()
  })
})
