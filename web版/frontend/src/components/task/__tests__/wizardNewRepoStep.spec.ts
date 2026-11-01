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

  it('未建仓（creatingNew=true）时不渲染测试区——链路靠建仓成功后翻态打通', async () => {
    const w = await mountStep()
    const labels = (Array.from(w.element.querySelectorAll('button')) as HTMLElement[]).map((b) =>
      (b.textContent ?? '').trim(),
    )
    // 新建态 RepoEditForm 的测试区被 v-if="!creatingNew" 挡掉（:549），这是有意为之：
    // 未落库的仓库无法测试（后端 testRepo 读已落库配置）。
    expect(labels.some((l) => l.includes('立即测试'))).toBe(false)
    w.unmount()
  })

  it('壳自己不画建仓按钮于已建仓之后（避免与 RepoEditForm 的入口重复）', async () => {
    const w = await mountStep('r1')
    const labels = (Array.from(w.element.querySelectorAll('button')) as HTMLElement[]).map((b) =>
      (b.textContent ?? '').trim(),
    )
    // 「创建并初始化仓库」只在未建仓时出现——建仓后再出现等于给重复建仓开口子
    expect(labels.some((l) => l.includes('创建并初始化仓库'))).toBe(false)
    console.log('LABELS:', JSON.stringify(labels))
    w.unmount()
  })
})
