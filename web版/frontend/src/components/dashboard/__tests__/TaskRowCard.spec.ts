/**
 * TaskRowCard 任务行护栏（方案 §4.5.6:292 —— **list 视图卡片行**；P7 A1 裁决后取代旧 TaskCard）。
 *
 * 锁定语义：
 * - 内容：名称 / 状态徽章 / 快照数 chip（null 不渲染）/ 指标区（上次、下次、耗时、上次结果）；
 * - 时间口径 timeMode：relative → 相对文案，actual → 绝对时间（同一函数 timeText 驱动）；
 * - 操作区：Start（dry-run 未预览时禁用）/ Cancel（运行中）/ ⋯ 菜单；
 * - 菜单分组含 P7 要求的 **Export / Log / Verify / Restore / Delete**，删除项运行中禁用；
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
  it('名称 / 状态徽章 / 快照 chip / 耗时渲染', () => {
    const w = mountRow()
    expect(w.text()).toContain('每日全量')
    expect(w.text()).toContain('上次成功')
    expect(w.text()).toContain('12 个快照')
    expect(w.text()).toContain('1:33') // 93000ms → M:SS
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
    expect(idleKeys).toEqual(['edit', 'restore', 'verify', 'export', 'log', 'delete'])
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
    menu.vm.$emit('select', 'edit')
    menu.vm.$emit('select', 'delete')
    expect(w.emitted('export')?.[0]).toEqual([7])
    expect(w.emitted('log')?.[0]).toEqual([7])
    expect(w.emitted('restore')?.[0]).toEqual([7])
    expect(w.emitted('verify')?.[0]).toEqual([7])
    expect(w.emitted('edit')?.[0]).toEqual([7])
    expect(w.emitted('delete')?.[0]).toEqual([7])
    w.unmount()
  })

  it('dry_run_pending → Start 禁用（R-026-08 门禁）', () => {
    const w = mountRow({ task: makeTask({ dry_run_pending: true }) })
    const buttons = w.findAll('button')
    const start = buttons.find((button) => button.text() === '立即运行')
    expect(start?.attributes('disabled')).toBeDefined()
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
})
