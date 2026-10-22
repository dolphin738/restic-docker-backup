/**
 * GlobalStatusBar 全局状态条护栏（方案 §4.5.6 / §4.7 P6；形态来源 Duplicati status-bar）。
 *
 * 锁定语义：
 * - jobId 为空（null / ''）= 无进行中作业，整条不渲染（不占位空条）；
 * - Stop → 外抛 stop 事件（消费方接 POST /api/jobs/{id}/cancel）；
 * - 限速（owner 2026-10-22 任务级裁决）→ **无运行中入口**：仅 limitText 只读展示，
 *   不渲染限速按钮、无 throttle 事件（restic CLI 无控制通道，运行中不可改；
 *   改限速走任务编辑表单）；
 * - **Abort 二段暂缺**（后端 cancel 无 grace=0 强杀路径）：本用例反向锁定
 *   「不得渲染第二个强杀按钮」——待后端补端点后再放开，勿提前造假 UI；
 * - 进度订阅走 useJobStream（本用例 mock 之：不发真实 SSE / HTTP）。
 */

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GlobalStatusBar from '@/components/dashboard/GlobalStatusBar.vue'
import { i18n } from '@/i18n'
import type { JobProgressPayload } from '@/api/types'

const mocks = vi.hoisted(() => ({
  progressHandler: null as ((payload: JobProgressPayload) => void) | null,
  streamCalls: [] as string[],
}))

vi.mock('@/composables/useJobStream', () => ({
  useJobStream: (jobId: () => string, options: { onProgress?: (p: JobProgressPayload) => void }) => {
    mocks.streamCalls.push(jobId())
    mocks.progressHandler = options.onProgress ?? null
    return { state: { value: 'idle' }, lastEventId: { value: 0 }, logs: { value: [] }, start: vi.fn(), stop: vi.fn() }
  },
}))

function mountBar(props: Partial<InstanceType<typeof GlobalStatusBar>['$props']> = {}) {
  return mount(GlobalStatusBar, {
    props: { jobId: 'job-1', title: '每日备份', ...props },
    global: { plugins: [i18n] },
  })
}

describe('GlobalStatusBar 全局状态条', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
    mocks.streamCalls = []
    mocks.progressHandler = null
  })

  it('jobId 为空整条不渲染（不发流、不占位）', () => {
    const w = mountBar({ jobId: null })
    expect(w.find('.rw-status-bar').exists()).toBe(false)
    expect(mocks.streamCalls).toEqual([''])
    w.unmount()
  })

  it('有作业时渲染标题 / 进度 / 指标 / 动作区', () => {
    const w = mountBar({ limitText: '上传 0 · 下载 0 KiB/s（不限速）' })
    // 组件根即 .rw-status-bar（v-if 渲染时），VTU find 含根匹配
    expect(w.find('.rw-status-bar').exists()).toBe(true)
    expect(w.text()).toContain('每日备份')
    expect(w.text()).toContain('上传 0 · 下载 0 KiB/s（不限速）')
    expect(mocks.streamCalls).toEqual(['job-1'])
    w.unmount()
  })

  it('缺省 title 回落 jobId', () => {
    const w = mountBar({ title: undefined })
    expect(w.text()).toContain('job-1')
    w.unmount()
  })

  it('Stop 按钮外抛 stop 事件', async () => {
    const w = mountBar()
    const buttons = w.element.querySelectorAll('.rw-status-bar__actions button')
    // 最后一个动作按钮 = Stop（warning secondary）
    const stop = buttons[buttons.length - 1] as HTMLElement
    stop.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(w.emitted('stop')).toHaveLength(1)
    w.unmount()
  })

  it('Abort 二段暂缺 + 限速入口已裁决砍掉：只渲染一个作业终止按钮（Stop），不得提前造假 UI', () => {
    const w = mountBar()
    const buttons = w.element.querySelectorAll('.rw-status-bar__actions button')
    expect(buttons).toHaveLength(1) // 仅 Stop（限速按钮按 owner 2026-10-22 任务级裁决移除）
    expect(w.text()).not.toContain('Abort')
    expect(w.text()).not.toContain('强杀')
    w.unmount()
  })

  it('progress 帧驱动百分比与文件数文案（mock 流）', async () => {
    const w = mountBar()
    mocks.progressHandler?.({
      percent: 42,
      files_done: 7,
      total_files: 20,
      bytes_done: 1024,
      total_bytes: 4096,
    } as JobProgressPayload)
    await w.vm.$nextTick()
    expect(w.text()).toContain('42%')
    expect(w.text()).toContain('文件 7/20')
    w.unmount()
  })
})
