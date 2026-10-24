/**
 * 快照浏览页行为回归（真挂载 + API 全 mock）。
 *
 * 存在理由：本页此前**零前端 spec**（`page-render-smoke` 只覆盖 dashboard/repo），
 * 而下列缺陷全部是「挂载后才暴露」的运行时行为：
 *   ① 切筛选必须回到第 1 页（否则深页切筛选 → 旧 offset 命中不足 → 空列表误导）；
 *   ② tag 下拉不得自噬（历史 tag 累积归并，筛选后不能消失，否则切不回去）；
 *   ③ A3 打/去标签必须回显新旧 id 映射（PRD US-E2 AC5，改标签会生成新快照对象）；
 *   ④ E18 输出上限截断必须明示（否则用户以为看全了）；
 *   ⑤ size 走 utils/format 单一口径（不裸显字节数）+ 文件树行末 size/mtime 元信息。
 *
 * DOM 查询纪律：文件树抽屉由 naive `n-drawer` teleport 到 body，故抽屉内断言
 * 一律走 `document.body`（每个用例结束 unmount，避免跨用例残留）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import {
  NButton,
  NConfigProvider,
  NInput,
  NInputNumber,
  NMessageProvider,
  NPagination,
  NSelect,
} from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { tagSnapshots } from '@/api/snapshots'
import { createJob } from '@/api/jobs'
import { previewPrune } from '@/api/prune'
import ConfirmGate from '@/components/ConfirmGate.vue'
import SnapshotsPage from '@/pages/snapshots/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

/** 可变 mock 数据 + 调用记录（vi.hoisted：工厂 hoist 后仍可被测试改写）。 */
const mocks = vi.hoisted(() => ({
  tasks: [] as Array<{ id: number; name: string; tag: string; snapshot_count?: number | null }>,
  /** 快照列表响应；`total` 决定分页器页数。 */
  snapshots: { items: [] as Array<Record<string, unknown>>, total: 0 },
  /** E17 调用记录（断言 offset 是否被正确重置）。 */
  listCalls: [] as Array<{ limit: number; offset: number; tag?: string; host?: string }>,
  /** E18 文件页响应。 */
  files: {
    snapshot_id: 'snap1',
    path: '/',
    items: [] as Array<Record<string, unknown>>,
    next_cursor: null as string | null,
    truncated: false,
  },
  tagResult: {
    changed_count: 0,
    changed: [] as Array<{ old_snapshot_id: string; new_snapshot_id: string }>,
    truncated: false,
  },
}))

vi.mock('@/api/backupTasks', () => ({
  listBackupTasks: vi.fn(async () => ({ items: mocks.tasks, total: mocks.tasks.length })),
  getBackupTask: vi.fn(async (id: number) => ({
    id,
    name: 't',
    tag: `task-${id}`,
    keep_daily: null,
    keep_weekly: null,
    keep_monthly: null,
    keep_within: '',
    keep_tags: [],
  })),
}))

vi.mock('@/api/snapshots', () => ({
  listSnapshots: vi.fn(
    async (limit: number, offset: number, tag?: string, host?: string) => {
      mocks.listCalls.push({ limit, offset, tag, host })
      return mocks.snapshots
    },
  ),
  listSnapshotFiles: vi.fn(async () => mocks.files),
  previewForget: vi.fn(async () => ({
    remove: [],
    remove_count: 0,
    keep_count: 0,
    group_count: 0,
    params: {},
    truncated: false,
  })),
  tagSnapshots: vi.fn(async () => mocks.tagResult),
}))

vi.mock('@/api/jobs', () => ({
  createJob: vi.fn(async () => ({ id: 'job-1' })),
}))

vi.mock('@/api/prune', () => ({
  previewPrune: vi.fn(async () => ({
    would_remove_packs: 3,
    freed_bytes: 1024 * 1024 * 100,
    freed_text: '100 MiB',
    clean: false,
    lines: ['will delete 3 packs', 'total prune: 100 blobs / 100 MiB'],
  })),
}))

function snapshot(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'snap1',
    time: '2026-10-20T10:00:00Z',
    host: 'nas',
    tags: [] as string[],
    paths: ['/etc'],
    size: null,
    ...over,
  }
}

/** 最近一次挂载所用的 router（断言页内跳转结果，如 forget 提交后 `?job=`）。 */
let lastRouter: ReturnType<typeof createRouter> | null = null

function mountPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/snapshots', name: 'snapshots', component: SnapshotsPage },
      { path: '/restore', name: 'restore', component: SnapshotsPage },
    ],
  })
  lastRouter = router
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(SnapshotsPage) }) },
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router] },
  })
}

/** 设筛选草稿 + 点「应用筛选」（筛选是 draft 模式，改下拉本身不触发查询）。 */
async function applyFilter(w: ReturnType<typeof mountPage>, index: number, value: string) {
  w.findAllComponents(NSelect)[index].vm.$emit('update:value', value)
  await flushPromises()
  const apply = buttonExact(w, '应用筛选')
  expect(apply).toBeTruthy()
  await apply!.find('button').trigger('click')
  await flushPromises()
}

/** 找一个按钮组件（按可见文案匹配）。 */
function buttonByText(w: ReturnType<typeof mountPage>, text: string) {
  return w.findAllComponents(NButton).find((btn) => btn.text().includes(text))
}

/** 精确匹配按钮文案（「应用」与「应用筛选」前缀重叠时用它消歧）。 */
function buttonExact(w: ReturnType<typeof mountPage>, text: string) {
  return w.findAllComponents(NButton).find((btn) => btn.text().trim() === text)
}


beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.tasks = []
  mocks.snapshots = { items: [], total: 0 }
  mocks.listCalls = []
  mocks.files = {
    snapshot_id: 'snap1',
    path: '/',
    items: [],
    next_cursor: null,
    truncated: false,
  }
  mocks.tagResult = { changed_count: 0, changed: [], truncated: false }
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('快照浏览页：筛选与分页', () => {
  it('切换标签筛选后回到第 1 页（深页切筛选不得沿用旧 offset）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 45 }
    const w = mountPage()
    await flushPromises()

    // 翻到第 2 页：offset 应为 20
    w.findComponent(NPagination).vm.$emit('update:page', 2)
    await flushPromises()
    expect(mocks.listCalls.at(-1)?.offset).toBe(20)

    // 应用筛选后：offset 必须回到 0
    await applyFilter(w, 0, 'task-1')
    expect(mocks.listCalls.at(-1)?.offset).toBe(0)
    w.unmount()
  })

  it('draft 模式：改下拉不发请求，点「应用筛选」才查询', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    const w = mountPage()
    await flushPromises()
    const before = mocks.listCalls.length

    w.findAllComponents(NSelect)[0].vm.$emit('update:value', 'task-1')
    await flushPromises()
    expect(mocks.listCalls.length).toBe(before) // 改下拉本身不触发 restic

    await applyFilter(w, 0, 'task-1')
    expect(mocks.listCalls.at(-1)?.tag).toBe('task-1')
    w.unmount()
  })

  it('历史遗留 tag 在筛选后仍留在下拉里（不得随筛选结果缩水）', async () => {
    mocks.snapshots = {
      items: [snapshot({ id: 's1', tags: ['legacy-a'] }), snapshot({ id: 's2', tags: ['legacy-b'] })],
      total: 2,
    }
    const w = mountPage()
    await flushPromises()

    const select = w.findAllComponents(NSelect)[0]
    const labels = () => (select.props('options') as Array<{ value: string }>).map((o) => o.value)
    expect(labels()).toContain('legacy-a')
    expect(labels()).toContain('legacy-b')

    // 筛掉 legacy-b：本页只剩 legacy-a，选项集合不得因此丢失 legacy-b
    mocks.snapshots = { items: [snapshot({ id: 's1', tags: ['legacy-a'] })], total: 1 }
    await applyFilter(w, 0, 'legacy-a')
    expect(labels()).toContain('legacy-b')
    w.unmount()
  })
})

describe('快照浏览页：主机筛选选项', () => {
  it('主机选项累积归并，筛选后其他主机不消失（与 tag 同款自噬缺陷）', async () => {
    mocks.snapshots = {
      items: [snapshot({ id: 's1', host: 'nas-a' }), snapshot({ id: 's2', host: 'nas-b' })],
      total: 2,
    }
    const w = mountPage()
    await flushPromises()

    const hostSelect = w.findAllComponents(NSelect)[1]
    const hosts = () => (hostSelect.props('options') as Array<{ value: string }>).map((o) => o.value)
    expect(hosts()).toContain('nas-a')
    expect(hosts()).toContain('nas-b')

    // 筛掉 nas-b：本页只剩 nas-a，选项不得因此丢失 nas-b
    mocks.snapshots = { items: [snapshot({ id: 's1', host: 'nas-a' })], total: 1 }
    await applyFilter(w, 1, 'nas-a')
    expect(hosts()).toContain('nas-b')
    w.unmount()
  })
})

describe('快照浏览页：A3 打/去标签回显', () => {
  it('回显新旧快照 id 映射（AC5）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tagResult = {
      changed_count: 2,
      changed: [
        { old_snapshot_id: 'aaaaaaaabbbb', new_snapshot_id: 'cccccccdddd' },
        { old_snapshot_id: '111111112222', new_snapshot_id: '333333334444' },
      ],
      truncated: false,
    }
    const w = mountPage()
    await flushPromises()
    // 先设筛选：ID 留空时「标签/主机筛选」是唯一合法目标范围（否则前置拦截挡下）
    await applyFilter(w, 0, 'task-1')

    // 填「添加标签」：按 placeholder prop 精确定位（避开 forget 区同族输入框与索引猜测）。
    // 走组件 `update:value`——naive Input 的内部输入链在 VTU 下不回传 v-model（实证）。
    const addInput = w
      .findAllComponents(NInput)
      .find((input) => input.props('placeholder') === '添加标签（逗号分隔）')
    expect(addInput).toBeTruthy()
    await addInput!.vm.$emit('update:value', 'prod')
    await flushPromises()
    const apply = buttonExact(w, '应用')
    expect(apply).toBeTruthy()
    await apply!.find('button').trigger('click')
    await flushPromises()

    expect(tagSnapshots).toHaveBeenCalled()
    const text = w.text()
    expect(text).toContain('已更新 2 个快照的标签')
    expect(text).toContain('aaaaaaaa')
    expect(text).toContain('cccccccd')
    expect(text).toContain('11111111')
    w.unmount()
  })
})

describe('快照浏览页：文件树抽屉', () => {
  it('E18 输出被截断时明示提示', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.files = {
      snapshot_id: 'snap1',
      path: '/',
      items: [],
      next_cursor: null,
      truncated: true,
    }
    const w = mountPage()
    await flushPromises()

    const browse = buttonByText(w, '浏览文件')
    expect(browse).toBeTruthy()
    await browse!.trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('因输出上限被截断')
    w.unmount()
  })

  it('文件树行末展示 size 与 mtime 元信息（E18 已返回，不得丢弃）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.files = {
      snapshot_id: 'snap1',
      path: '/',
      items: [
        { name: 'nginx.conf', type: 'file', path: '/sources/etc/nginx.conf', display_path: '/etc/nginx.conf', size: 1024, mtime: '2026-10-20T10:00:00Z' },
      ],
      next_cursor: null,
      truncated: false,
    }
    const w = mountPage()
    await flushPromises()

    const browse = buttonByText(w, '浏览文件')
    await browse!.trigger('click')
    await flushPromises()

    const meta = document.body.querySelector('.vtree__meta')
    expect(meta).toBeTruthy()
    expect(meta?.textContent).toContain('1.0 KiB')
    w.unmount()
  })
})

// ---------------------------------------------------------------- R-016 独立回收（prune）

describe('快照浏览页：R-016 独立回收（prune）', () => {
  it('预览 → 显示待删 pack 数与释放量；确认门提交 prune 作业并跳任务页跟踪', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    const w = mountPage()
    await flushPromises()

    const previewBtn = w.findAllComponents(NButton).find((btn) => btn.text().includes('预览可回收空间'))
    expect(previewBtn).toBeTruthy()
    await previewBtn!.trigger('click')
    await flushPromises()

    // 页面有两道 ConfirmGate（forget 在前、prune 在后），须按标题精准命中 prune 门
    const gate = w.findAllComponents(ConfirmGate).find((g) => (g.props('title') ?? '').includes('回收'))!
    expect(gate).toBeTruthy()
    // 预览摘要含待删 pack 数与释放量（modal teleport 到 body，须查 document.body）
    expect(document.body.textContent ?? '').toContain('100 MiB')

    // memory history 未 isReady 时 currentRoute 不一定同步，故用 push spy 断言去向（对齐 forget 测试）
    const push = vi.spyOn(lastRouter!, 'push')
    await gate.vm.$emit('confirm')
    await flushPromises()
    expect(vi.mocked(createJob).mock.calls.at(-1)?.[0]).toBe('prune')
    expect(push).toHaveBeenCalledWith(expect.objectContaining({ query: { job: 'job-1' } }))
    w.unmount()
  })

  it('仓库已干净（clean）时预览给绿色提示而非释放量', async () => {
    vi.mocked(previewPrune).mockResolvedValueOnce({
      would_remove_packs: 0,
      freed_bytes: null,
      freed_text: null,
      clean: true,
      lines: ['nothing to remove'],
    })
    mocks.snapshots = { items: [snapshot()], total: 1 }
    const w = mountPage()
    await flushPromises()

    const previewBtn = w.findAllComponents(NButton).find((btn) => btn.text().includes('预览可回收空间'))
    await previewBtn!.trigger('click')
    await flushPromises()

    // 预览摘要给绿色「仓库已干净」提示（modal teleport 到 body，须查 document.body）
    expect(document.body.textContent ?? '').toContain('仓库已干净')
    w.unmount()
  })
})

describe('快照浏览页：forget 提交后去向', () => {
  it('确认清理后跳任务页并带 ?job=<id>（异步作业去任务页看结果，不在快照页干等）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    const w = mountPage()
    await flushPromises()
    // 用 spy 断言跳转（memory history 未 isReady 时 currentRoute 不一定同步）
    const push = vi.spyOn(lastRouter!, 'push')

    // 走通预览门：选标签 + 填保留日档 + 点预览（previewForget mock 恒成功）
    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    w.findAllComponents(NInputNumber)[0].vm.$emit('update:value', 1)
    await flushPromises()
    const previewBtn = buttonByText(w, '预览将被清理的快照')
    expect(previewBtn).toBeTruthy()
    await previewBtn!.find('button').trigger('click')
    await flushPromises()

    // 确认门：直接 emit confirm（关键词门是 UI 层校验，本例只验提交后去向）
    await w.findComponent(ConfirmGate).vm.$emit('confirm')
    await flushPromises()

    expect(push).toHaveBeenCalledWith(expect.objectContaining({ query: { job: 'job-1' } }))
    w.unmount()
  })
})

describe('快照浏览页：A3 目标范围前置拦截', () => {
  it('ID 与筛选都空时不发请求（避免作用于仓库内全部快照）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    vi.mocked(tagSnapshots).mockClear()
    const w = mountPage()
    await flushPromises()

    const addInput = w
      .findAllComponents(NInput)
      .find((input) => input.props('placeholder') === '添加标签（逗号分隔）')
    await addInput!.vm.$emit('update:value', 'prod')
    await flushPromises()

    const apply = buttonExact(w, '应用')
    await apply!.find('button').trigger('click')
    await flushPromises()
    expect(tagSnapshots).not.toHaveBeenCalled()
    w.unmount()
  })
})

describe('快照浏览页：forget 分组维度公示', () => {
  it('分组维度只读公示（不给自由输入——后端不校验维度合法性）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    const w = mountPage()
    await flushPromises()
    expect(w.text()).toContain('分组维度：host, paths, tags')
    w.unmount()
  })
})

describe('快照浏览页：展示口径', () => {
  it('size 列走 formatBytes 单一口径（不裸显字节数）', async () => {
    mocks.snapshots = { items: [snapshot({ size: 1536 })], total: 1 }
    const w = mountPage()
    await flushPromises()

    const text = w.text()
    expect(text).toContain('1.5 KiB')
    expect(text).not.toContain('1536')
    w.unmount()
  })
})
