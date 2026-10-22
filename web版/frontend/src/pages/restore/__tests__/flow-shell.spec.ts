/**
 * P9 恢复向导外壳护栏（方案 §4.8.5 R4/R10，风险 V-2）。
 *
 * 存在理由：外壳把只读 `n-steps` 换成可跳步步骤条后，最容易坏的是三件事——
 * ① 未过步骤被跳过（必填未填就进了下一步）；② 跳步后 URL 与内容不同步；
 * ③ 进行中离开无确认。vue-tsc 查不到这三类语义，故在此锁死：
 *   - RestoreStepper：inactive 步骤**鼠标与键盘双向不可达**（V-2）；
 *   - 外壳：点步骤 → 路由 name 同步（select-files / options / progress）；
 *   - 外壳：脏态离开被拦截（confirm 返回 false 则不跳转）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RestoreStepper from '@/components/restore/RestoreStepper.vue'
import WizardPage from '@/pages/restore/flow/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  snapshots: [] as Array<Record<string, unknown>>,
}))

vi.mock('@/api/snapshots', () => ({
  listSnapshots: vi.fn(async () => ({ items: mocks.snapshots })),
  listSnapshotFiles: vi.fn(async () => ({ items: [], next_cursor: null })),
}))

vi.mock('@/api/restore', () => ({
  INPLACE_TARGET_ROOT: '/sources',
  RESTORE_TARGET_ROOT: '/restore',
  previewRestore: vi.fn(async () => ({ items: [], total: 0, truncated: false })),
  restoreSnapshot: vi.fn(async () => ({ job_id: 'job-1' })),
}))

const STEPS = [
  { key: 1, title: '选择文件' },
  { key: 2, title: '恢复选项' },
  { key: 3, title: '执行与结果' },
]

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.snapshots = [
    { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: ['/data'], size: 1024 },
  ]
})

describe('RestoreStepper（P9 可跳步步骤条）', () => {
  function mountStepper(current: number, enabled?: Record<number, boolean>) {
    return mount(RestoreStepper, {
      props: { steps: STEPS, current, enabled, ariaLabel: '恢复向导步骤' },
    })
  }

  it('① 当前步标 aria-current，已过步标 is-done', () => {
    const w = mountStepper(2)
    const btns = w.findAll('.restore-stepper__btn')
    expect(btns[1].attributes('aria-current')).toBe('step')
    expect(btns[0].classes()).toContain('is-done')
    expect(btns[2].classes()).not.toContain('is-done')
  })

  it('② V-2：未过步骤 inactive —— 鼠标不可点且键盘不可达', async () => {
    const w = mountStepper(1, { 1: true, 2: false, 3: false })
    const btns = w.findAll('.restore-stepper__btn')
    expect(btns[1].classes()).toContain('is-inactive')
    expect(btns[1].attributes('tabindex')).toBe('-1')
    expect(btns[1].attributes('aria-disabled')).toBe('true')
    await btns[1].trigger('click')
    expect(w.emitted('select')).toBeUndefined()
  })

  it('③ 可跳步骤点击即 emit select', async () => {
    const w = mountStepper(1, { 1: true, 2: true, 3: false })
    await w.findAll('.restore-stepper__btn')[1].trigger('click')
    expect(w.emitted('select')?.[0]).toEqual([2])
  })
})

/** 经 router-view 渲染的宿主：onBeforeRouteLeave 必须挂在有 active route record 的
 *  组件上才生效——直接 mount 组件会报「No active route record」且**守卫不注册**，
 *  离开确认用例会假失败（真实运行时组件由 router-view 渲染，无此问题）。 */
const RouterHost = { template: '<router-view />' }

describe('恢复向导外壳（P9 步骤 ↔ 路由 + 离开确认）', () => {
  /** 挂载向导：宿主路由注册三个步骤 name（契约与真实路由表一致）。 */
  async function mountWizard(initialPath: string) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/restore/:id/select-files', name: 'restore-select-files', component: WizardPage },
        { path: '/restore/:id/options', name: 'restore-options', component: WizardPage },
        { path: '/restore/:id/progress', name: 'restore-progress', component: WizardPage },
        { path: '/:pathMatch(.*)*', component: WizardPage },
      ],
    })
    await router.push(initialPath)
    await router.isReady()
    const wrapper = mount(NConfigProvider, {
      props: { themeOverrides: lightThemeOverrides },
      slots: { default: () => h(NMessageProvider, null, { default: () => h(RouterHost) }) },
      global: {
        plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router],
      },
    })
    return { wrapper, router }
  }

  it('④ 进入 options 路由 → 步骤条当前步为第 2 步', async () => {
    const { wrapper } = await mountWizard('/restore/snap-1/options')
    await flushPromises()
    expect(wrapper.findAll('.restore-stepper__btn')[1].classes()).toContain('is-current')
  })

  it('⑤ 点第 1 步跳回 → URL 同步到 select-files', async () => {
    const { wrapper, router } = await mountWizard('/restore/snap-1/options')
    await flushPromises()
    await wrapper.findAll('.restore-stepper__btn')[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('restore-select-files')
    expect(router.currentRoute.value.params.id).toBe('snap-1')
  })

  it('⑥ 未提交作业时第 3 步 inactive（不能跳到进度页）', async () => {
    const { wrapper } = await mountWizard('/restore/snap-1/select-files')
    await flushPromises()
    expect(wrapper.findAll('.restore-stepper__btn')[2].classes()).toContain('is-inactive')
  })

  it('⑦ 跳步不弹离开确认（内部步骤切换不算离开，防误报回归）', async () => {
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)
    try {
      const { wrapper, router } = await mountWizard('/restore/snap-1/options')
      await flushPromises()
      await wrapper.findAll('.restore-stepper__btn')[0].trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.name).toBe('restore-select-files')
      expect(confirmSpy).not.toHaveBeenCalled()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('⑧ 脏态离开被拦截：confirm 返回 false 则不跳转', async () => {
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)
    try {
      const { router } = await mountWizard('/restore/snap-1/select-files')
      await flushPromises()
      await router.push('/somewhere-else').catch(() => undefined)
      expect(confirmSpy).toHaveBeenCalled()
      expect(router.currentRoute.value.name).toBe('restore-select-files')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
