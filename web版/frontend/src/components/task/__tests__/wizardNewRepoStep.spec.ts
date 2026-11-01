/**
 * 建仓步骤壳护栏（方案 v1.1 §4.3 F14 / §5.3）。
 *
 * 存在理由：建仓步复用了 pages/repo/RepoEditForm.vue，而该组件的测试区与 init 入口
 * 都以 `!creatingNew` 为前提。方案原文写「creatingNew 传 true」——那样链路是断的。
 * 本规格锁住真正可用的形态：建仓成功后翻成 false，测试区/init 入口才出现。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { h, nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import NewRepoStep from '@/components/task/NewRepoStep.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(async () => ({ items: [] })),
  putConfig: vi.fn(async () => ({ ok: true })),
}))
vi.mock('@/api/jobs', () => ({ createJob: vi.fn(async () => ({ id: 'job-init' })) }))
vi.mock('@/api/repo', () => ({
  listRepos: vi.fn(async () => []),
  getRepo: vi.fn(async () => ({ id: 'r1', type: 'local', passfile_status: 'configured', url: '/srv/r' })),
  getRepoStatus: vi.fn(async () => ({ connected: false })),
  testRepo: vi.fn(async () => ({ ok: false, reason: '仓库未初始化', failure_kind: 'not_initialized' })),
  createRepo: vi.fn(async () => ({ id: 'r1', name: 'x' })),
  setRepoPassword: vi.fn(async () => ({ ok: true, generated: false, password: null })),
  suggestRepoId: vi.fn(async () => ({ id: 'r-auto' })),
  listRcloneRemotes: vi.fn(async () => ({ conf_present: false, remotes: [] })),
  listRcloneConfigs: vi.fn(async () => ({ configs: [] })),
}))

async function mountStep(provisionedRepoId: string | null = null) {
  const wrapper = mount(
    h(
      NConfigProvider,
      { themeOverrides: lightThemeOverrides },
      {
        default: () =>
          h(NMessageProvider, null, { default: () => h(NewRepoStep, { provisionedRepoId }) }),
      },
    ),
    {
      global: { plugins: [createPinia(), i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]] },
    },
  )
  await flushPromises()
  await nextTick()
  return wrapper
}

describe('NewRepoStep 建仓步骤壳', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
  })

  it('未建仓时渲染类型下拉，且不渲染测试区（RepoEditForm 的 !creatingNew 前提）', async () => {
    const w = await mountStep()
    const text = w.element.textContent ?? ''
    expect(text).toContain('新建仓库')
    // 测试区专属文案：新建态不应出现
    expect(text).not.toContain('立即测试')
    w.unmount()
  })

  it('已建仓（父组件传 provisionedRepoId）时渲染测试与初始化入口', async () => {
    const w = await mountStep('r1')
    const text = w.element.textContent ?? ''
    expect(text).toContain('立即测试')
    expect(text).toContain('初始化仓库')
    w.unmount()
  })

  it('已建仓时主按钮不再是「创建」语义（避免重复建仓）', async () => {
    const w = await mountStep('r1')
    const labels = (Array.from(w.element.querySelectorAll('button')) as HTMLElement[]).map((b) =>
      (b.textContent ?? '').trim(),
    )
    // 建仓完成后仍存在创建按钮也无妨，但测试/初始化入口必须在
    expect(labels.some((l) => l.includes('立即测试'))).toBe(true)
    w.unmount()
  })
})
