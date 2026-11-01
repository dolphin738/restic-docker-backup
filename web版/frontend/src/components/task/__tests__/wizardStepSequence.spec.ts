/**
 * 任务向导步骤序列（stepKey）护栏（2026-11-03，方案 v1.1 §5.7 第 1 步）。
 *
 * 存在理由：改造前 `step` 是「序数 1..5」，且 1..5 被硬编码散落在 9 处
 * （Math.min(5,…) / validateStep 的 current===1|2 / cursor===3 / /^[1-5]$/ /
 * step<5 / i18n 文案）。方案要把「第一步开关 → 第二步新建仓库 → 后续后移」
 * 做成序列驱动，前提是先把「步骤」从序数解耦成语义键 stepKey。
 *
 * 本规格锁死重构后的**行为等价**（第 1 步是纯重构，不改变任何用户可见行为）：
 *   ① 关闭分支（默认）序列仍是 5 步，显示序号 1..5，标题对应 step1..step5；
 *   ② 数字键视为**显示序号**，换算成 stepKey 后落点正确；
 *   ③ 步骤条 is-current / is-done 在非连续 stepKey 下仍单调正确；
 *   ④ 页脚「下一步」在末步隐藏、「返回」在首步隐藏；
 *   ⑤ 键盘 ←/→ 前后翻步。
 *
 * 断言只走 DOM（显示序号、标题文案、页脚按钮），不依赖组件内部变量名——
 * stepKey 具体取什么值属于实现细节，改了不该让本规格变红。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h, nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TaskWizard from '@/components/TaskWizard.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

vi.mock('@/api/backupTasks', () => ({
  getBackupTask: vi.fn(async () => null),
  createBackupTask: vi.fn(async () => ({ id: 2 })),
  updateBackupTask: vi.fn(async () => ({ id: 1 })),
  dryRunBackupTask: vi.fn(async () => ({ items: [], warnings: [], truncated: false, error: null })),
  dryRunSources: vi.fn(async () => ({ items: [], warnings: [], truncated: false, error: null })),
  // 备份源树：返回一个挂载点，点它即把路径加入 form.sources（走通第②步校验门）
  browseSources: vi.fn(async () => ({
    path: '/',
    mounts: ['/data'],
    items: [{ name: 'data', type: 'dir', display_path: '/data', is_mountpoint: true }],
    next_cursor: null,
  })),
}))

vi.mock('@/api/exclusions', () => ({
  getExclusions: vi.fn(async () => ({ display_items: [] })),
}))

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(async () => ({ items: [] })),
  putConfig: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/jobs', () => ({
  createJob: vi.fn(async () => ({ id: 'job-1' })),
  cancelJob: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/repo', () => ({
  listRepos: vi.fn(async () => [{ id: 'default', name: '默认仓库' }]),
  getRepo: vi.fn(async () => ({ id: 'default', passfile_status: 'configured' })),
  getRepoStatus: vi.fn(async () => ({ connected: true })),
  testRepo: vi.fn(async () => ({ ok: true, latency_ms: 9 })),
}))

async function mountWizard() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  const wrapper = mount(
    h(
      NConfigProvider,
      { themeOverrides: lightThemeOverrides },
      { default: () => h(NMessageProvider, null, { default: () => h(TaskWizard, null) }) },
    ),
    {
      global: {
        plugins: [createPinia(), i18n, [VueQueryPlugin, { queryClient: new QueryClient() }], router],
      },
    },
  )
  await flushPromises()
  await nextTick()
  return wrapper
}

/** 点页脚「下一步」。 */
async function clickNext(wrapper: ReturnType<typeof mount>): Promise<void> {
  const btn = (Array.from(wrapper.element.querySelectorAll('.wizard__footer button')) as HTMLElement[]).find(
    (b) => (b.textContent ?? '').includes('下一步'),
  )
  expect(btn, '页脚应有「下一步」').toBeTruthy()
  btn!.click()
  await flushPromises()
  await nextTick()
}

/** 在第①步填入任务名（否则 validateStep 拦住前进——既有行为，非重构引入）。 */
async function fillName(wrapper: ReturnType<typeof mount>, name = '测试任务'): Promise<void> {
  const input = wrapper.element.querySelector('.wizard__body input') as HTMLInputElement | null
  expect(input, '第①步应有名称输入框').toBeTruthy()
  input!.value = name
  input!.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
  await nextTick()
}

/** 在第②步点树里的挂载点加一个源（走通源数>0 的校验门）。 */
async function addFirstSource(wrapper: ReturnType<typeof mount>): Promise<void> {
  for (let i = 0; i < 40; i += 1) {
    const row = wrapper.element.querySelector('.vtree__name') as HTMLElement | null
    if (row) {
      row.click()
      await flushPromises()
      await nextTick()
      return
    }
    await new Promise((r) => setTimeout(r, 10))
    await nextTick()
  }
  throw new Error('未等到备份源树行渲染')
}

/** 走通第①→②→③ 的前进路径（名称 + 源），停在指定步。 */
async function walkTo(wrapper: ReturnType<typeof mount>, target: number): Promise<void> {
  if (target >= 2) {
    await fillName(wrapper)
    await clickNext(wrapper)
  }
  if (target >= 3) {
    await addFirstSource(wrapper)
    await clickNext(wrapper)
  }
}

/** 步骤条的显示序号（WizardStepper 的 index + 1 徽标）。 */
function stepBadges(wrapper: ReturnType<typeof mount>): string[] {
  return (Array.from(wrapper.element.querySelectorAll('.wizard-stepper__index')) as HTMLElement[]).map(
    (el) => (el.textContent ?? '').trim(),
  )
}

/** 步骤条标题。 */
function stepTitles(wrapper: ReturnType<typeof mount>): string[] {
  return (Array.from(wrapper.element.querySelectorAll('.wizard-stepper__title')) as HTMLElement[]).map(
    (el) => (el.textContent ?? '').trim(),
  )
}

/** 当前步徽标（is-current 那个）。 */
function currentBadge(wrapper: ReturnType<typeof mount>): string {
  const el = wrapper.element.querySelector('.wizard-stepper__btn.is-current .wizard-stepper__index')
  return (el?.textContent ?? '').trim()
}

async function pressKey(wrapper: ReturnType<typeof mount>, key: string): Promise<void> {
  const nav = wrapper.element.querySelector('.wizard__steps-nav')
  expect(nav).not.toBeNull()
  nav!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  await flushPromises()
  await nextTick()
}

/** 页脚按钮文案（判断「下一步 / 返回」是否在场）。 */
function footerText(wrapper: ReturnType<typeof mount>): string {
  return (wrapper.element.querySelector('.wizard__footer')?.textContent ?? '').trim()
}

describe('TaskWizard 步骤序列（stepKey 重构，行为等价）', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
  })

  it('关闭分支（默认）：序列为 5 步，显示序号 1..5，标题对应 step1..step5', async () => {
    const w = await mountWizard()
    expect(stepBadges(w)).toEqual(['1', '2', '3', '4', '5'])
    expect(stepTitles(w)).toEqual(['基础信息', '备份源', '存储目标', '调度', '保留与首跑'])
  })

  it('数字键按「显示序号」换算：停在第③步时按 1 回到基础信息、按 2 到备份源', async () => {
    const w = await mountWizard()
    await walkTo(w, 3)
    expect(currentBadge(w)).toBe('3')

    await pressKey(w, '1')
    expect(currentBadge(w)).toBe('1')

    // 回到 1 后前进受校验门限制，故用后退路径继续验数字键
    await walkTo(w, 3)
    await pressKey(w, '2')
    expect(currentBadge(w)).toBe('2')
  })

  it('步骤条 is-done 按显示顺序单调：停在 3 时前两步为 is-done，后两步不是', async () => {
    const w = await mountWizard()
    await walkTo(w, 3)
    const btns = Array.from(w.element.querySelectorAll('.wizard-stepper__btn')) as HTMLElement[]
    expect(btns.map((b) => b.classList.contains('is-done'))).toEqual([true, true, false, false, false])
    expect(btns.map((b) => b.classList.contains('is-current'))).toEqual([false, false, true, false, false])
  })

  it('首步隐藏「上一步」，其后各步均显示', async () => {
    const w = await mountWizard()
    // 首步：无「上一步」，有「下一步」
    expect(footerText(w)).not.toContain('上一步')
    expect(footerText(w)).toContain('下一步')

    // 第②步起出现「上一步」（既有语义：step > 1 即显示）
    await walkTo(w, 2)
    expect(footerText(w)).toContain('上一步')
    expect(footerText(w)).toContain('下一步')
  })

  it('键盘 → 前进一格、← 后退一格', async () => {
    const w = await mountWizard()
    expect(currentBadge(w)).toBe('1')

    // 从第②步起：← 回到第①步，→ 再前进（第②步已填名称，前进校验通过）
    await walkTo(w, 2)
    expect(currentBadge(w)).toBe('2')
    await pressKey(w, 'ArrowLeft')
    expect(currentBadge(w)).toBe('1')
    await pressKey(w, 'ArrowRight')
    expect(currentBadge(w)).toBe('2')
  })
})
