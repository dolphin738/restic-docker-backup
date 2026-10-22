/**
 * 回归护栏：path-less 快照的 targetMode 自动回落（A7 潜伏缺陷，P11 暴露）。
 *
 * 存在理由：A7 把「原位置」设为恢复向导默认模式后，写了一行回落逻辑——
 * 入口被置灰（快照无 paths）时把模式切回时间戳流。但原实现
 * `watch(inPlaceDisabled)` 只认**变化**，而 `inPlaceDisabled` 在快照列表
 * 加载前就已恒为 true（无选中快照 → paths 为空），path-less 快照加载后
 * 依然是 true——watcher 根本不触发，「默认 inplace」把这个回落整个吞掉。
 * 后果：用户选了一个无原目录的快照，卡在「inplace 置灰却仍选中」的非法态，
 * `goConfirm` 因「原位未选目标」早退，步骤 1/2 的点击全部空转，且无任何
 * 报错提示（只是没反应）。vue-tsc 与渲染测试都查不到这种语义死锁。
 *
 * 修复：改 watch `selectedSnapshot`——切到（含默认选中）无 paths 的快照时
 * 才回落；有 paths 时不打扰 A7 的 inplace 默认。本规格锁死修复后行为。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import WizardPage from '@/pages/restore/flow/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  snapshots: [] as Array<Record<string, unknown>>,
}))

vi.mock('@/api/snapshots', () => ({
  listSnapshots: vi.fn(async () => ({ items: mocks.snapshots })),
  listSnapshotFiles: vi.fn(async () => ({ items: [], next_cursor: null })),
  searchSnapshotFiles: vi.fn(async () => ({ items: [], total: 0, next_cursor: null })),
}))

// 原位流进步骤 2 会触发 loadPreview（E20 预览）；mock 掉保持 hermetic，
// 本规格只关心模式回落，不断言预览内容。
vi.mock('@/api/restore', () => ({
  INPLACE_TARGET_ROOT: '/sources',
  RESTORE_TARGET_ROOT: '/restore',
  previewRestore: vi.fn(async () => ({ items: [], total: 0, truncated: false })),
  restoreSnapshot: vi.fn(async () => ({ job_id: 'job-1', restore_target: '/restore/x' })),
}))

const RouterHost = { template: '<router-view />' }

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

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
})

describe('path-less 快照 targetMode 回落（A7 缺陷回归）', () => {
  it('① path-less 快照（默认选中）：自动回落时间戳流，步骤 1「下一步」可点到步骤 2', async () => {
    mocks.snapshots = [
      { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: [], size: 1024 },
    ]
    const { wrapper } = await mountWizard('/restore/snap-1/select-files')
    await flushPromises()

    // 修复前：goConfirm 因「原位未选目标」早退，点击完全空转（无报错、无跳转）。
    const btn = wrapper.findAll('button').find((b) => b.text().includes('下一步'))
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()

    // 进入步骤 2 且为时间戳流：分区一 + 零覆盖说明 + 原位置灰原因均出现
    //（而非原位目标下拉 / 预览区——那需要 previewStats 才能开门）。
    expect(wrapper.text()).toContain('恢复到哪')
    expect(wrapper.text()).toContain('时间戳子目录为零覆盖语义，不涉及覆盖选项。')
    expect(wrapper.text()).toContain('该快照未记录可定位的原目录，无法原位恢复。')
  })

  it('② 有 paths 的快照：保持 A7 的 inplace 默认，不回落', async () => {
    mocks.snapshots = [
      { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: ['/data'], size: 1024 },
    ]
    const { wrapper } = await mountWizard('/restore/snap-1/select-files')
    await flushPromises()

    // inplace 可用：步骤 1 下一步进步骤 2 后，出现的是原位目标下拉（时间戳流没有）
    const btn = wrapper.findAll('button').find((b) => b.text().includes('下一步'))
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('恢复到哪')
    expect(wrapper.text()).not.toContain('时间戳子目录为零覆盖语义，不涉及覆盖选项。')
    expect(wrapper.text()).not.toContain('该快照未记录可定位的原目录，无法原位恢复。')
  })
})
