/**
 * 审计日志中文化（2026-10-19 用户要求「审计日志能更加中文化吗」）。
 *
 * 覆盖两条翻译路径 + 两条回落路径：
 *  - 动作名（`repo.test` → 「测试仓库连接」）与 detail 键名（`reason` → 「原因」）走 i18n；
 *  - 未收录的动作名 / 非 JSON detail 必须**原样透传**（后端新增动作或改文案时不能显示空标签、
 *    更不能把原文吞掉——审计是排障依据，丢了原文等于留痕失效）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { NConfigProvider, NDataTable, NMessageProvider, NSelect } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h, type Component } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuditPage from '@/pages/audit/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  items: [] as Array<Record<string, unknown>>,
  actions: [] as string[],
}))

vi.mock('@/api/audit', () => ({
  queryAudit: vi.fn(async () => ({
    items: mocks.items,
    total: mocks.items.length,
    actions: mocks.actions,
  })),
  // 导出（R-020）：页面 setup 会 import，仅点击时调用，桩为 no-op 即可
  exportAuditNdjson: vi.fn(async () => ''),
  listAuditArchives: vi.fn(async () => []),
}))

function mountPage(component: Component) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component }],
  })
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    // 审计页导出按钮的完成提示走 useMessage（R-020 裁决落地），须有 provider 包裹
    slots: { default: () => h(NMessageProvider, null, { default: () => h(component) }) },
    global: {
      plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router],
    },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.actions = ['repo.test', 'job.submit', 'restore.inplace', 'future.action']
  mocks.items = [
    {
      ts: '2026-10-19T10:00:00Z',
      action: 'repo.test',
      result: 'denied',
      target: 'repo-2',
      detail: JSON.stringify({ ok: false, reason: '仓库不存在或未初始化', latency_ms: 12 }),
    },
    {
      ts: '2026-10-19T10:01:00Z',
      action: 'future.action',
      result: 'ok',
      target: 'whatever',
      detail: 'not-json-plain-text',
    },
  ]
})

describe('审计日志中文化', () => {
  it('动作列显示中文动作名；未收录动作回落原文（不显示空标签）', async () => {
    const w = mountPage(AuditPage)
    await flushPromises()

    const text = w.text()
    // 已收录：repo.test → 测试仓库连接
    expect(text).toContain('测试仓库连接')
    // 结果列仍走 audit.result.* 中文化
    expect(text).toContain('已拒绝')
    // 未收录：整串原文照原样出现（后端新动作不会变成空白）
    expect(text).toContain('future.action')
    w.unmount()
  })

  it('筛选下拉选项为「中文（原文）」：既可读又可按英文前缀手输', async () => {
    const w = mountPage(AuditPage)
    await flushPromises()

    // 直接读下拉 props（happy-dom 下虚拟滚动会吞掉下拉项 DOM，断言 props 更稳）
    const select = w.findComponent(NSelect)
    const options = select.props('options') as Array<{ label: string; value: string }>
    const labels = options.map((o) => o.label)
    expect(labels).toContain('测试仓库连接（repo.test）')
    expect(labels).toContain('原位恢复（restore.inplace）')
    // 未收录动作：译文 = 原文，不重复拼括号
    expect(labels).toContain('future.action')
    expect(labels.some((l) => l.includes('future.action（'))).toBe(false)
    w.unmount()
  })

  it('展开区：detail 键名中文化 + 值格式化；非 JSON 原样透传', async () => {
    const w = mountPage(AuditPage)
    await flushPromises()

    // 行展开：happy-dom 下直接渲染展开内容（NDataTable expand 由 renderExpand 产出）
    const table = w.findComponent(NDataTable)
    const columns = table.props('columns') as unknown as Array<Record<string, unknown>>
    const expand = columns.find((c) => c.type === 'expand') as unknown as {
      renderExpand: (row: Record<string, unknown>) => unknown
    }
    expect(expand).toBeTruthy()

    // 第一行：JSON → 中文键名（结果/原因/耗时）+ 布尔走「否」+ 耗时补 ms
    const jsonRow = mocks.items[0]
    const rendered = expand.renderExpand(jsonRow) as { children?: unknown[] }
    const flat = JSON.stringify(rendered)
    expect(flat).toContain('原因')
    expect(flat).toContain('耗时')
    expect(flat).toContain('12 ms')
    expect(flat).toContain('否')

    // 第二行：非 JSON → 原样透传（不吞原文）
    const textRow = mocks.items[1]
    const renderedText = expand.renderExpand(textRow) as { children?: unknown }
    expect(JSON.stringify(renderedText)).toContain('not-json-plain-text')
    w.unmount()
  })
})
