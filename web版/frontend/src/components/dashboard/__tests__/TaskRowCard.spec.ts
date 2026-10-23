/**
 * TaskRowCard 任务行护栏（方案 §4.5.6:292 —— **list 视图卡片行**；P7 A1 裁决后取代旧 TaskCard）。
 *
 * 锁定语义：
 * - 内容：名称 + 提示 chip + 快照数 chip（null 不渲染）/ 指标区（上次成功备份于、
 *   下一次排程执行、用时）+ **上次结果徽章（紧跟「用时」之后，即原位置）**；
 *   **徽章不得跑到名称行，也不得贴到右侧按钮旁**；
 * - 「上次成功」全行只允许出现一次（徽章走短状态词，不与指标文案撞词）；
 * - 指标位恒定：空值一律「—」（用时 null 也渲染，不因空值让行高跳动）；
 * - 时间口径 timeMode：relative → 相对文案，actual → 绝对时间（同一函数 timeText 驱动）；
 * - 操作区：Start（播放图标 + 文案「开始」；dry-run 未预览时禁用）/ Cancel（运行中）/ 日志
 *   / **编辑画笔** / ⋯ 菜单；**三键同一形态档**（small + 有边框，非 quaternary 裸图标）；
 * - 菜单分组含 P7 要求的 **Export / Log / Verify / Restore / Delete**（编辑已提到行内），删除项运行中禁用；
 * - 事件全部对外抛（edit/restore/verify/export/log/delete/start/cancel），不自行跳转。
 *
 * 查询纪律：组件根即 .rw-row-card（div 根），原生 querySelector 不含自身，
 * 根断言走 w.classes()；后代走 w.element。
 */

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import TaskRowCard from '@/components/dashboard/TaskRowCard.vue'
import TaskActionsMenu from '@/components/dashboard/TaskActionsMenu.vue'
import { i18n } from '@/i18n'
import type { TaskCard as TaskCardModel } from '@/api/types'

/** 基准任务：启用中、上次成功、有耗时与快照数。 */
function makeTask(overrides: Partial<TaskCardModel> = {}): TaskCardModel {
  return {
    id: 7,
    name: '每日全量',
    enabled: true,
    tag: 'task-7',
    cron: '0 3 * * *',
    status: 'success',
    last_run_at: '2026-10-19T03:00:00Z',
    last_result: 'success',
    last_duration_ms: 93_000,
    snapshot_count: 12,
    limit_up: 0,
    limit_down: 0,
    next_run_at: '2026-10-20T03:00:00Z',
    running_job_id: null,
    dry_run_pending: false,
    prune_due: false,
    ...overrides,
  } as TaskCardModel
}

function mountRow(props: Record<string, unknown> = {}) {
  return mount(TaskRowCard, {
    props: { task: makeTask(), timeMode: 'relative', ...props },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
})

describe('TaskRowCard 任务行', () => {
  it('名称 / 快照 chip / 用时渲染；徽章紧跟「用时」之后（不在名称行、不在按钮旁）', () => {
    const w = mountRow()
    expect(w.text()).toContain('每日全量')
    expect(w.text()).toContain('12 个快照')
    expect(w.text()).toContain('1:33') // 93000ms → M:SS

    // 版式（owner 2026-10-24）：徽章在指标行末、紧接「用时」；名称行与操作区都不得有徽章
    const metrics = w.element.querySelector('.task-row__metrics')
    expect(metrics?.querySelectorAll('.status-tag').length).toBeGreaterThan(0)
    // 顺序断言：徽章在「用时」文本之后
    const text = metrics?.textContent ?? ''
    expect(text.indexOf('用时')).toBeLessThan(text.indexOf('上次结果'))
    expect(w.element.querySelector('.rw-row-card__name .status-tag')).toBeNull()
    expect(w.element.querySelector('.rw-row-card__actions .status-tag')).toBeNull()
    w.unmount()
  })

  it('「上次成功」全行只出现一次（指标保留、徽章走短词）', () => {
    const w = mountRow()
    const text = w.element.textContent ?? ''
    expect(text.split('上次成功').length - 1).toBe(1)
    // 徽章短词仍在（否则状态无从表达）
    expect(w.element.querySelector('.task-row__result .status-tag')?.textContent ?? '').toContain(
      '成功',
    )
    w.unmount()
  })

  it('空值一律「—」：从未运行 → 上次成功备份于 / 用时都是破折号占位', () => {
    const w = mountRow({
      task: makeTask({
        status: 'never_run',
        last_run_at: null,
        last_result: null,
        last_duration_ms: null,
      }),
    })
    const text = w.element.textContent ?? ''
    expect(text).toContain('上次成功备份于：—')
    expect(text).toContain('用时：—')
    w.unmount()
  })

  it('上次失败 → 标签退回「上次运行」（不把失败的尝试说成成功备份）', () => {
    const w = mountRow({ task: makeTask({ status: 'failed', last_result: 'failed' }) })
    const text = w.element.textContent ?? ''
    expect(text).toContain('上次运行：')
    expect(text).not.toContain('上次成功备份于')
    w.unmount()
  })

  it('snapshot_count 为 null → 不渲染 chip（不可知 ≠ 0）', () => {
    const w = mountRow({ task: makeTask({ snapshot_count: null }) })
    expect(w.text()).not.toContain('个快照')
    w.unmount()
  })

  it('timeMode 驱动时间口径：relative 与 actual 文案不同', () => {
    const relative = mountRow()
    const actual = mountRow({ timeMode: 'actual' })
    const relativeText = relative.text()
    const actualText = actual.text()
    expect(relativeText).not.toBe(actualText)
    expect(actualText).toMatch(/\d{4}-\d{2}-\d{2}/) // 绝对时间形态
    relative.unmount()
    actual.unmount()
  })

  it('菜单含 Export / Log / Verify / Restore，删除项运行中禁用', () => {
    const idle = mountRow()
    const idleGroups = idle.findComponent(TaskActionsMenu).props('groups') as {
      actions: { key: string; disabled?: boolean }[]
    }[]
    const idleKeys = idleGroups.flatMap((group) => group.actions.map((action) => action.key))
    expect(idleKeys).toEqual(['restore', 'export', 'verify', 'log', 'delete'])
    // 条目图标（对齐 Duplicati 菜单：左图标 + 文案）——菜单条目不得裸文案
    const actions = idle.findComponent(TaskActionsMenu).props('groups') as {
      actions: { key: string; icon?: unknown }[]
    }[]
    for (const action of actions.flatMap((g) => g.actions)) {
      expect(action.icon, `${action.key} 缺图标`).toBeDefined()
    }
    expect(idleGroups.flatMap((g) => g.actions).find((a) => a.key === 'delete')?.disabled).toBe(false)
    idle.unmount()

    const running = mountRow({
      task: makeTask({ status: 'running', running_job_id: 'job-1' }),
    })
    const runningGroups = running.findComponent(TaskActionsMenu).props('groups') as {
      actions: { key: string; disabled?: boolean }[]
    }[]
    expect(runningGroups.flatMap((g) => g.actions).find((a) => a.key === 'delete')?.disabled).toBe(true)
    running.unmount()
  })

  it('菜单 select → 对外 emit（export / log 不自行跳转）', async () => {
    const w = mountRow()
    const menu = w.findComponent(TaskActionsMenu)
    menu.vm.$emit('select', 'export')
    menu.vm.$emit('select', 'log')
    menu.vm.$emit('select', 'restore')
    menu.vm.$emit('select', 'verify')
    menu.vm.$emit('select', 'delete')
    expect(w.emitted('export')?.[0]).toEqual([7])
    expect(w.emitted('log')?.[0]).toEqual([7])
    expect(w.emitted('restore')?.[0]).toEqual([7])
    expect(w.emitted('verify')?.[0]).toEqual([7])
    expect(w.emitted('delete')?.[0]).toEqual([7])
    w.unmount()
  })

  it('行内编辑画笔（图标按钮带 aria-label）→ emit edit', async () => {
    const w = mountRow()
    const pencil = [...w.element.querySelectorAll('button')].find(
      (b) => b.getAttribute('aria-label') === '编辑任务',
    )
    expect(pencil).toBeDefined()
    // 图标按钮无文本却有 aria-label（a11y-baseline 硬约束）
    expect(pencil!.textContent?.trim()).toBe('')
    await pencil!.click()
    expect(w.emitted('edit')?.[0]).toEqual([7])
    w.unmount()
  })

  it('dry_run_pending → Start 禁用（R-026-08 门禁）', () => {
    const w = mountRow({ task: makeTask({ dry_run_pending: true }) })
    const buttons = w.findAll('button')
    const start = buttons.find((button) => button.text() === '开始')
    expect(start?.attributes('disabled')).toBeDefined()
    w.unmount()
  })

  it('三键同形态：开始（播放图标 + 文案）/ 编辑 / 三点 都是 small 档且非 quaternary', () => {
    const w = mountRow()
    const buttons = [...w.element.querySelectorAll('button')]
    const start = buttons.find((b) => b.textContent?.trim() === '开始')
    const edit = buttons.find((b) => b.getAttribute('aria-label') === '编辑任务')
    const more = buttons.find((b) => b.getAttribute('aria-label') === '更多操作')
    for (const b of [start, edit, more]) {
      expect(b, '三个按钮都必须渲染').toBeDefined()
      expect(b!.className).toContain('n-button--small')
      expect(b!.className).not.toContain('quaternary')
    }
    // 开始 = 图标 + 文案（不是裸文字按钮）
    expect(start!.querySelector('svg')).not.toBeNull()
    // 编辑 / 三点：图标按钮无文本（可访问名靠 aria-label）
    expect(edit!.textContent?.trim()).toBe('')
    expect(more!.textContent?.trim()).toBe('')
    w.unmount()
  })

  it('运行中 → Cancel 按钮 emit cancel(jobId)', async () => {
    const w = mountRow({ task: makeTask({ status: 'running', running_job_id: 'job-9' }) })
    const cancel = w.findAll('button').find((button) => button.text() === '取消运行')
    expect(cancel).toBeDefined()
    await cancel!.trigger('click')
    expect(w.emitted('cancel')?.[0]).toEqual(['job-9'])
    w.unmount()
  })

  it('状态流转 idle → running → idle：不残留重复的 Start 按钮（n-space 按索引发 key）', async () => {
    const w = mountRow()
    const startCount = () =>
      [...w.element.querySelectorAll('button')].filter((b) => b.textContent?.trim() === '开始')
        .length
    const cancelCount = () =>
      [...w.element.querySelectorAll('button')].filter((b) => b.textContent?.trim() === '取消运行')
        .length

    expect(startCount()).toBe(1)

    // 运行中：Start 换成 Cancel + 日志
    await w.setProps({ task: makeTask({ status: 'running', running_job_id: 'job-1' }) })
    expect(startCount()).toBe(0)
    expect(cancelCount()).toBe(1)

    // 跑完回落空闲：Start 必须恰好一个（这里曾出现两个，刷新才消失）
    await w.setProps({ task: makeTask({ status: 'success', running_job_id: null }) })
    expect(startCount()).toBe(1)
    expect(cancelCount()).toBe(0)
    // 指标区同样含条件子节点（用时空值→有值），不得重复渲染
    expect((w.element.textContent ?? '').split('1:33').length - 1).toBe(1)
    w.unmount()
  })

  it('首次运行后（用时由 null 变有值）指标不重复渲染', async () => {
    const w = mountRow({
      task: makeTask({ status: 'never_run', last_duration_ms: null, last_result: null }),
    })
    const before = w.element.textContent ?? ''
    // 用时位恒定存在（空值为「—」）
    expect(before.includes('用时：—')).toBe(true)

    await w.setProps({
      task: makeTask({ status: 'success', last_duration_ms: 93_000, last_result: 'success' }),
    })
    const after = w.element.textContent ?? ''
    expect(after.split('1:33').length - 1).toBe(1)
    w.unmount()
  })
})
