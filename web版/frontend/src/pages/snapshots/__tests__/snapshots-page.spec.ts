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

import { previewForget, tagSnapshots } from '@/api/snapshots'
import { getBackupTask } from '@/api/backupTasks'
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

/** E34b 任务详情最小形（只填 forget 回填读到的字段）。
 *  刻意不补全 BackupTask 的 30+ 字段：本页只读这五个，补全会掩盖真实依赖面。 */
function stubTaskDetail(over: Record<string, unknown> = {}): void {
  const detail = {
    id: 1,
    name: 't',
    tag: 'task-1',
    keep_daily: null,
    keep_weekly: null,
    keep_monthly: null,
    keep_within: '',
    keep_tags: [] as string[],
    ...over,
  }
  vi.mocked(getBackupTask).mockImplementation((async () => detail) as never)
}

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
      // 2026-10-28：`/restore` 已并入本页（原独立向导路由删除），此处不再设同名路由
      { path: '/jobs', name: 'jobs', component: { template: '<div />' } },
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

describe('快照浏览页：forget 保留档位 0 值口径', () => {
  /** 任务 keep_* 全为 0（DDL 缺省）时回填 0 → 界面必须当作「未启用」，
   *  不得让预览按钮亮起来：E21 请求 schema 是 ge=1，0 提交上去必 422。 */
  it('选中任务后回填 0：不填入 0，且预览按钮保持禁用', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tasks = [{ id: 1, name: 't', tag: 'task-1' }]
    stubTaskDetail({ keep_last: 0, keep_daily: 0, keep_weekly: 0, keep_monthly: 7 })
    const w = mountPage()
    await flushPromises()

    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    await flushPromises()

    // 输入框顺序：[keep_last, daily, weekly, monthly]
    // 0 被转成 null → 输入框留空（不是 0）；未回填的 keep_last 也是 null
    const nums = w.findAllComponents(NInputNumber)
    expect(nums[0].props('value')).toBeNull() // keep_last（任务里没有此档）
    expect(nums[1].props('value')).toBeNull() // daily=0 → null
    expect(nums[2].props('value')).toBeNull() // weekly=0 → null
    // 只有 monthly=7 生效 → 按钮可点
    expect(nums[3].props('value')).toBe(7)
    expect(buttonByText(w, '预览将被清理的快照')).toBeTruthy()
    w.unmount()
  })

  it('手输 0：请求体里不带该档（不把 0 发给 ge=1 的后端）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tasks = [{ id: 1, name: 't', tag: 'task-1' }]
    stubTaskDetail({ keep_daily: 0, keep_weekly: 0, keep_monthly: 0 })
    vi.mocked(previewForget).mockClear()
    const w = mountPage()
    await flushPromises()

    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    // 先让回填跑完，否则下面手输的值会被异步回填覆盖（回填是 watch + await 详情）
    await flushPromises()
    // 用户手动把日档塞成 0（脏值：清空会回 null，这里模拟 0 漏进 payload 的路径），
    // 周档填 1 让按钮可点。顺序：[keep_last, daily, weekly, monthly]
    w.findAllComponents(NInputNumber)[1].vm.$emit('update:value', 0) // daily
    w.findAllComponents(NInputNumber)[2].vm.$emit('update:value', 1) // weekly
    await flushPromises()

    const btn = buttonByText(w, '预览将被清理的快照')
    await btn!.find('button').trigger('click')
    await flushPromises()

    expect(previewForget).toHaveBeenCalled()
    const payload = vi.mocked(previewForget).mock.calls[0]![0] as Record<string, unknown>
    // 0 必须被吃掉（不发），有效值照常发
    expect(payload.keep_last).toBeUndefined()
    expect(payload.keep_daily).toBeUndefined()
    expect(payload.keep_weekly).toBe(1)
    expect(payload.keep_monthly).toBeUndefined()
    w.unmount()
  })

  it('填「保留最近 N 个」即 keep_last：进请求体，且单独一档就能过门', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tasks = [{ id: 1, name: 't', tag: 'task-1' }]
    stubTaskDetail({ keep_last: 0, keep_daily: 0, keep_weekly: 0, keep_monthly: 0 })
    vi.mocked(previewForget).mockClear()
    const w = mountPage()
    await flushPromises()

    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    await flushPromises()
    // 输入框顺序：[keep_last, daily, weekly, monthly]——只填最后一档
    w.findAllComponents(NInputNumber)[0].vm.$emit('update:value', 1)
    await flushPromises()

    const btn = buttonByText(w, '预览将被清理的快照')
    await btn!.find('button').trigger('click')
    await flushPromises()

    expect(previewForget).toHaveBeenCalled()
    const payload = vi.mocked(previewForget).mock.calls[0]![0] as Record<string, unknown>
    expect(payload.keep_last).toBe(1)
    // 其余档位未填 → 不该出现在请求体（否则会误伤保留范围）
    expect(payload.keep_daily).toBeUndefined()
    expect(payload.keep_weekly).toBeUndefined()
    expect(payload.keep_monthly).toBeUndefined()
    w.unmount()
  })

  it('任务三档全 0：预览按钮禁用（不发请求，也就不会 422）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tasks = [{ id: 1, name: 't', tag: 'task-1' }]
    stubTaskDetail({ keep_daily: 0, keep_weekly: 0, keep_monthly: 0 })
    vi.mocked(previewForget).mockClear()
    const w = mountPage()
    await flushPromises()

    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    await flushPromises()

    const btn = buttonByText(w, '预览将被清理的快照')
    if (btn) {
      await btn.find('button').trigger('click')
      await flushPromises()
    }
    expect(previewForget).not.toHaveBeenCalled()
    w.unmount()
  })
})

describe('快照浏览页：forget 分组维度', () => {
  /** restic 按**组**应用 keep-*，默认分组下 keep_last=1 是「每组留 1 个」而不是
   *  「整个仓库留 1 个」。三种取值里只有空串是「不分组」，且空串必须原样进请求体
   *  ——payload 若用 `||` 兜底就会把它吞掉，能力静默失效。 */
  it('默认送 host,paths,tags，并给出三档合法取值（不给自由输入）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tasks = [{ id: 1, name: 't', tag: 'task-1' }]
    stubTaskDetail({ keep_last: 1 })
    vi.mocked(previewForget).mockClear()
    const w = mountPage()
    await flushPromises()

    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    await flushPromises()
    const btn = buttonByText(w, '预览将被清理的快照')
    await btn!.find('button').trigger('click')
    await flushPromises()

    expect(previewForget).toHaveBeenCalled()
    const payload = vi.mocked(previewForget).mock.calls[0]![0] as Record<string, unknown>
    expect(payload.group_by).toBe('host,paths,tags')

    // 三档下拉（索引 3：0/1 是筛选器、2 是标签）只有合法取值，无自由输入
    const groupSelect = w.findAllComponents(NSelect)[3]
    const values = (groupSelect.props('options') as Array<{ value: string }>).map((o) => o.value)
    expect(values).toEqual(['host,paths,tags', 'paths', ''])
    w.unmount()
  })

  it('选「不分组」：空串必须进请求体（不能被 || 吞成默认值）', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tasks = [{ id: 1, name: 't', tag: 'task-1' }]
    stubTaskDetail({ keep_last: 1 })
    vi.mocked(previewForget).mockClear()
    const w = mountPage()
    await flushPromises()

    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    await flushPromises()
    w.findAllComponents(NSelect)[3].vm.$emit('update:value', '')
    await flushPromises()

    const btn = buttonByText(w, '预览将被清理的快照')
    await btn!.find('button').trigger('click')
    await flushPromises()

    expect(previewForget).toHaveBeenCalled()
    const payload = vi.mocked(previewForget).mock.calls[0]![0] as Record<string, unknown>
    expect(payload.group_by).toBe('')
    w.unmount()
  })

  it('选「按路径」：group_by 送 paths', async () => {
    mocks.snapshots = { items: [snapshot()], total: 1 }
    mocks.tasks = [{ id: 1, name: 't', tag: 'task-1' }]
    stubTaskDetail({ keep_last: 1 })
    vi.mocked(previewForget).mockClear()
    const w = mountPage()
    await flushPromises()

    w.findAllComponents(NSelect)[2].vm.$emit('update:value', 'task-1')
    await flushPromises()
    w.findAllComponents(NSelect)[3].vm.$emit('update:value', 'paths')
    await flushPromises()

    const btn = buttonByText(w, '预览将被清理的快照')
    await btn!.find('button').trigger('click')
    await flushPromises()

    const payload = vi.mocked(previewForget).mock.calls[0]![0] as Record<string, unknown>
    expect(payload.group_by).toBe('paths')
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