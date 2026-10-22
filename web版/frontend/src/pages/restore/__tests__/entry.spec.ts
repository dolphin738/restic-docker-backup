/**
 * 恢复入口页 + 向导预选护栏（P8 / 方案 §4.8.5，风险 V-1）。
 *
 * 存在理由：P8 把 `/restore` 改成入口页、新增 `/restore/:id/*` 向导路由，
 * 最容易**静默破坏**的是「快照页『恢复此快照』直达」——vue-tsc 查不到路由语义，
 * 页面照常渲染、测试照常绿，但用户点下去不再预选。本规格锁死三处契约：
 *   ① 真实路由表能解析 `restore-select-files`（name → path）；
 *   ② 入口页把 `?snapshot=` 原样转发到向导首步（V-1 主断言）；
 *   ③ 入口页行动卡进入向导首步时携带快照 id。
 *
 * 布局/像素断言不在本规格范围（happy-dom 无 CSS 布局）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import EntryPage from '@/pages/restore/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

/** 可变 mock 数据（vi.hoisted：工厂 hoist 后仍可被测试改写）。 */
const mocks = vi.hoisted(() => ({
  snapshots: [] as Array<Record<string, unknown>>,
}))

vi.mock('@/api/snapshots', () => ({
  listSnapshots: vi.fn(async () => ({ items: mocks.snapshots })),
  listSnapshotFiles: vi.fn(async () => ({ items: [], next_cursor: null })),
}))

const WIZARD_STUB = { template: '<div class="stub-wizard" />' }

/** 挂载入口页：宿主路由注册 restore 三个 name（契约与真实路由表一致）。
 *  注：vue-router 4 无 initialRoute 选项，须先 push + isReady 再挂载，
 *  否则组件 setup 读到的 route 仍是初始 '/'，?snapshot= 用例会假通过。 */
async function mountEntry(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/restore', name: 'restore', component: EntryPage },
      { path: '/restore/:id/select-files', name: 'restore-select-files', component: WIZARD_STUB },
      { path: '/restore/:id/options', name: 'restore-options', component: WIZARD_STUB },
      { path: '/restore/:id/progress', name: 'restore-progress', component: WIZARD_STUB },
      { path: '/:pathMatch(.*)*', component: WIZARD_STUB },
    ],
  })
  await router.push(initialPath)
  await router.isReady()
  const wrapper = mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(EntryPage) }) },
    global: {
      plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router],
    },
  })
  return { wrapper, router }
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.snapshots = [
    { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: ['/data'], size: 1024 },
    { id: 'snap-2', time: '2026-10-02T00:00:00Z', host: 'host-a', tags: [], paths: ['/etc'], size: 2048 },
  ]
})

describe('恢复入口页（P8 §4.8.5 R1）', () => {
  it('① 真实路由表能解析向导三个 name', async () => {
    // 动态 import 取真实 router（避免与宿主 router 抢全局实例）
    const { router } = await import('@/router')
    expect(router.resolve({ name: 'restore-select-files', params: { id: 'abc' } }).path).toBe('/restore/abc/select-files')
    expect(router.resolve({ name: 'restore-options', params: { id: 'abc' } }).path).toBe('/restore/abc/options')
    expect(router.resolve({ name: 'restore-progress', params: { id: 'abc' } }).path).toBe('/restore/abc/progress')
  })

  it('② ?snapshot= 直达：转发到向导首步且 id 原样透传（V-1）', async () => {
    const { router } = await mountEntry('/restore?snapshot=snap-2')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('restore-select-files')
    expect(router.currentRoute.value.params.id).toBe('snap-2')
  })

  it('③ 点快照行动卡：进入向导首步并携带该快照 id', async () => {
    const { wrapper, router } = await mountEntry('/restore')
    await flushPromises()
    const cards = wrapper.findAll('.rw-action-card')
    // 两张快照卡 + 两张备选卡（从配置恢复 / 从其他仓库恢复）
    expect(cards.length).toBe(4)
    await cards[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('restore-select-files')
    expect(router.currentRoute.value.params.id).toBeTruthy()
  })

  it('④ 无快照时给空态提示，不渲染行动卡', async () => {
    mocks.snapshots = []
    const { wrapper } = await mountEntry('/restore')
    await flushPromises()
    expect(wrapper.text()).toContain('还没有快照')
    expect(wrapper.findAll('.rw-action-card').length).toBe(2)
  })
})
