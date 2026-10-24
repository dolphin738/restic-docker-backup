/**
 * 任务向导「启用任务」开关的语义澄清护栏（2026-10-24）。
 *
 * 存在理由：第①步开关文案原为「启用调度」，但它管的是 `tasks.enabled` **任务总闸**
 * （后端 scheduler.py 据此决定是否挂 cron，卡片派生状态变「已暂停」，补跑/prune 判定一并停），
 * 手动「开始」不受它约束。文案与语义不符 + 开关在第①步、被它控制的东西在第④步（中间隔两步），
 * 用户会在第④步配半天 cron 才发现根本不触发。本规格锁死两件事：
 *   ① 第①步出现「启用任务」文案 + 副作用说明 hint（不再出现『启用调度』）；
 *   ② 第④步在 enabled=false 时给出暂停提示，enabled=true 时提示消失。
 *
 * 断言走 DOM 文案（i18n zh-CN）+ 结构类 `.wizard__paused-alert`，不依赖组件内部变量名。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider, NSwitch } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h, nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TaskWizard from '@/components/TaskWizard.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  /** 被编辑任务的 enabled 态（本规格的判定源）。 */
  enabled: false as boolean,
}))

vi.mock('@/api/backupTasks', () => ({
  getBackupTask: vi.fn(async () => ({
    id: 1,
    name: '每日全量',
    enabled: mocks.enabled,
    display_sources: ['/sources/data'],
    display_excludes: [],
    cron: '0 3 * * *',
    keep_last: 7,
    keep_hourly: 0,
    keep_daily: 7,
    keep_weekly: 4,
    keep_monthly: 6,
    keep_yearly: 1,
    keep_within: null,
    keep_within_hourly: null,
    keep_within_daily: null,
    keep_within_weekly: null,
    keep_within_monthly: null,
    keep_within_yearly: null,
    keep_tags: [],
    tag: 'daily',
    group_by: 'host',
    one_file_system: false,
    compression: 'auto',
    check_sample_ratio: 0,
    monthly_full_day: 1,
    prune_interval_days: 30,
    limit_up: 0,
    limit_down: 0,
    retry_count: 1,
    mirror_repo_ids: [],
  })),
  createBackupTask: vi.fn(async () => ({ id: 2 })),
  updateBackupTask: vi.fn(async () => ({ id: 1 })),
  dryRunBackupTask: vi.fn(async () => ({ items: [], warnings: [], truncated: false, error: null })),
  dryRunSources: vi.fn(async () => ({ items: [], warnings: [], truncated: false, error: null })),
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
      { default: () => h(NMessageProvider, null, { default: () => h(TaskWizard, { taskId: 1 }) }) },
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

/** 用键盘数字键跳步（与向导的 a11y 直达语义同路径，避免依赖 naive Steps 的内部点击结构）。 */
async function gotoStep(wrapper: ReturnType<typeof mount>, key: string): Promise<void> {
  const nav = wrapper.element.querySelector('.wizard__steps-nav')
  expect(nav).not.toBeNull()
  nav!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  await flushPromises()
  await nextTick()
}

describe('TaskWizard 第①步「启用任务」语义澄清', () => {
  beforeEach(() => {
    mocks.enabled = false
    localStorage.clear()
    // 断言走 zh-CN 文案（i18n 默认按 navigator.language 探测，CI 环境为 en-US）
    i18n.global.locale.value = 'zh-CN'
  })

  it('第①步：开关文案为「启用任务」，不再出现「启用调度」', async () => {
    const w = await mountWizard()
    const text = w.element.textContent ?? ''
    expect(text).toContain('启用任务')
    expect(text).not.toContain('启用调度')
    w.unmount()
  })

  it('第①步：开关带副作用说明（关闭只停自动，手动仍可跑，调度保留）', async () => {
    const w = await mountWizard()
    const hint = w.element.querySelector('.wizard__note')
    expect(hint).not.toBeNull()
    expect(hint?.textContent ?? '').toContain('手动运行')
    w.unmount()
  })

  it('第④步：任务已暂停时给出提示（避免配了 cron 却不触发）', async () => {
    const w = await mountWizard()
    await gotoStep(w, '4')
    // 跳步可能因任一前置校验失败而停在中途：只在真正到达第④步时断言
    const alert = w.element.querySelector('.wizard__paused-alert')
    expect(alert).not.toBeNull()
    expect(alert?.textContent ?? '').toContain('调度暂不会生效')
    w.unmount()
  })

  it('第④步：开关开启后提示消失（提示随总闸实时翻转，不是静态文案）', async () => {
    mocks.enabled = true
    const w = await mountWizard()
    await gotoStep(w, '4')
    expect(w.element.querySelector('.wizard__paused-alert')).toBeNull()
    w.unmount()
  })

  it('第①步：开关翻转后第④步提示随之出现/消失（同一实例内联动）', async () => {
    mocks.enabled = true
    const w = await mountWizard()
    await gotoStep(w, '4')
    expect(w.element.querySelector('.wizard__paused-alert')).toBeNull()

    // 回到第①步关掉总闸
    await gotoStep(w, '1')
    const sw = w.findComponent(NSwitch)
    expect(sw.exists()).toBe(true)
    sw.vm.$emit('update:value', false)
    await nextTick()

    await gotoStep(w, '4')
    expect(w.element.querySelector('.wizard__paused-alert')).not.toBeNull()
    w.unmount()
  })
})

/**
 * 步骤切换不得重复渲染 / 泄漏（regression guard）。
 *
 * 背景：原 `.wizard__body` 是 `<n-space>`，其会把每个子节点包进 key 写死为 1 的 div；
 * 容器下挂 5 个互斥 <template v-if> 步 + footer，步间顶层子节点数量不同，
 * 两套「全 key=1」列表互 diff 时 Vue 复用错乱，旧步 wrapper 不被卸载 → 来回切步出现
 * 重复「策略」块、甚至第④步 ScheduleRetentionForm 泄漏进第⑤步。
 * 这里用 ScheduleRetentionForm 独有的 `.schedule-form__summary` 作为探针：
 * 第④步应恰有 1 个；第⑤步应 0 个（即使来回切也不许泄漏/累积）。
 */
describe('TaskWizard 步骤切换渲染护栏', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
  })

  /** 仅统计「真正挂在 DOM 上」的 ScheduleRetentionForm 个数（含被泄漏的副本）。 */
  function scheduleFormCount(w: ReturnType<typeof mount>): number {
    return w.element.querySelectorAll('.schedule-form__summary').length
  }

  it('第④步：ScheduleRetentionForm 恰渲染一次', async () => {
    const w = await mountWizard()
    await gotoStep(w, '4')
    expect(scheduleFormCount(w)).toBe(1)
    w.unmount()
  })

  it('第⑤步：不得泄漏第④步的 ScheduleRetentionForm（0 个）', async () => {
    const w = await mountWizard()
    await gotoStep(w, '4')
    await gotoStep(w, '5')
    expect(scheduleFormCount(w)).toBe(0)
    w.unmount()
  })

  it('4→5→4→5 来回切步不累积重复/泄漏', async () => {
    const w = await mountWizard()
    await gotoStep(w, '4')
    expect(scheduleFormCount(w)).toBe(1)
    await gotoStep(w, '5')
    expect(scheduleFormCount(w)).toBe(0)
    await gotoStep(w, '4')
    expect(scheduleFormCount(w)).toBe(1)
    await gotoStep(w, '5')
    expect(scheduleFormCount(w)).toBe(0)
    w.unmount()
  })
})

/**
 * 第④步：保留档位汉化 + 调度 tab 醒目（2026-10-24 owner 诉求）。
 *  - 保留档位标签不得再显示 keep-last / keep-hourly 等英文 flag 名；
 *  - 调度模式选择器由 segment 小药丸改为 line 下划线 tab（更醒目）且带名字。
 * 断言走 DOM 文案（zh-CN）+ 结构类，不依赖组件内部变量名。
 */
describe('TaskWizard 第④步保留档位汉化 + 调度 tab 醒目', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
  })

  /** 仅取档位小标签（输入框上方 depth-3 文案），不含区块标题与占位符。 */
  function keepLabels(w: ReturnType<typeof mount>): string[] {
    const nodes = w.element.querySelectorAll('.schedule-form__keep-label') as NodeListOf<Element>
    return Array.from(nodes).map((el) => el.textContent?.trim() ?? '')
  }

  it('保留档位名称已汉化（计数档 + 时间线档），不再出现英文 flag 名', async () => {
    const w = await mountWizard()
    await gotoStep(w, '4')
    const labels = keepLabels(w)
    // 计数档
    expect(labels).toContain('保留最近')
    expect(labels).toContain('每小时保留')
    expect(labels).toContain('每天保留')
    expect(labels).toContain('每周保留')
    expect(labels).toContain('每月保留')
    expect(labels).toContain('每年保留')
    // 时间线档
    expect(labels).toContain('总时长内')
    expect(labels).toContain('每小时时长')
    expect(labels).toContain('每周时长')
    // 汉化后不得再出现英文 flag 名作为档位标签
    expect(labels).not.toContain('keep-last')
    expect(labels).not.toContain('keep-hourly')
    expect(labels).not.toContain('keep-within')
    w.unmount()
  })

  it('调度模式以醒目的 card tab 呈现且带名字', async () => {
    const w = await mountWizard()
    await gotoStep(w, '4')
    const tabs = w.element.querySelector('.n-tabs')
    expect(tabs).not.toBeNull()
    // 由 segment 小药丸改为 card 带边框的 tab 页，更醒目、且规避 line 下划线在空面板时不可见的坑
    expect(tabs!.className).toContain('n-tabs--card-type')
    // 三个模式 tab 均存在（结构护栏：模式数恒为 3）
    expect(w.element.querySelectorAll('.n-tabs-tab__label').length).toBe(3)
    // 三个模式名均为中文（事件级护栏，避开 jsdom 下 naive 标签文本不落 DOM 的坑）
    expect(i18n.global.t('wizard.scheduleMode.none')).toContain('不调度')
    expect(i18n.global.t('wizard.scheduleMode.simple')).toContain('简单模式')
    expect(i18n.global.t('wizard.scheduleMode.advanced')).toContain('高级模式')
    w.unmount()
  })

  it('第⑤步：保留摘要已汉化，不再出现英文 keep-last', async () => {
    const w = await mountWizard()
    await gotoStep(w, '5')
    const text = w.element.textContent ?? ''
    expect(text).toContain('保留最近')
    expect(text).not.toContain('keep-last')
    w.unmount()
  })
})
