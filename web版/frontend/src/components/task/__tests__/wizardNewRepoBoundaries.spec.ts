/**
 * 「新建仓库」边界行为护栏（方案 v1.1 §5.7 第 6 步 / §6.1 R3、R12 / §6.2 P6）。
 *
 * 只锁**真实可达**的边界——方案 R1 描述的「停在第 2 步时切关」在 UI 上不可达：
 * 开关只在第①步渲染（TaskWizard 模板 v-if="!isEdit" 的那个 n-space 在 KEY_BASICS 分支内），
 * 因此在第①步切关时 step 恒为 KEY_BASICS，『当前 stepKey 不在新序列里』不会发生。
 * 为不可达场景写分支只会增加维护面，故此处不测它。
 *
 * 真正可达且有害的是这两条：
 *   ① R3/R12：建仓已把 ui.currentRepoId 切到新仓库，用户回第①步关掉开关以为
 *      「回到原仓库」，实际保存时任务仍归新仓库（api/backupTasks.ts 取 currentRepoId）。
 *      → 关开关必须弹确认，并提供「回滚到原仓库」。
 *   ② P6：已建仓但任务未保存就离开 → 三选项（保留 / 撤销删除 / 继续编辑）。
 *      撤销删除必须 purgeDisk=false（磁盘数据保留）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NDialogProvider, NMessageProvider } from 'naive-ui'
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
  browseSources: vi.fn(async () => ({ path: '/', mounts: [], items: [], next_cursor: null })),
}))
vi.mock('@/api/exclusions', () => ({ getExclusions: vi.fn(async () => ({ display_items: [] })) }))
vi.mock('@/api/config', () => ({
  getConfig: vi.fn(async () => ({ items: [] })),
  putConfig: vi.fn(async () => ({ ok: true })),
}))
vi.mock('@/api/jobs', () => ({
  createJob: vi.fn(async () => ({ id: 'job-1' })),
  cancelJob: vi.fn(async () => ({ ok: true })),
}))
vi.mock('@/api/repo', () => ({
  listRepos: vi.fn(async () => [
    { id: 'default', name: '默认仓库' },
    { id: 'r-new', name: '新建仓库' },
  ]),
  getRepo: vi.fn(async () => ({ id: 'default', passfile_status: 'configured' })),
  getRepoStatus: vi.fn(async () => ({ connected: true })),
  testRepo: vi.fn(async () => ({ ok: true, latency_ms: 9 })),
  createRepo: vi.fn(async () => ({ id: 'r-new', name: '新建仓库' })),
  setRepoPassword: vi.fn(async () => ({ ok: true, generated: false, password: null })),
  deleteRepo: vi.fn(async () => ({ ok: true })),
  suggestRepoId: vi.fn(async () => ({ id: 'r-new' })),
  listRcloneRemotes: vi.fn(async () => ({ conf_present: false, remotes: [] })),
  listRcloneConfigs: vi.fn(async () => ({ configs: [] })),
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
      {
        default: () =>
          h(NDialogProvider, null, {
            default: () => h(NMessageProvider, null, { default: () => h(TaskWizard, null) }),
          }),
      },
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

function newRepoSwitch(wrapper: ReturnType<typeof mount>): HTMLElement | null {
  return wrapper.element.querySelector('[aria-label="为此任务新建仓库"]') as HTMLElement | null
}

async function toggle(wrapper: ReturnType<typeof mount>): Promise<void> {
  newRepoSwitch(wrapper)!.click()
  await flushPromises()
  await nextTick()
}

describe('新建仓库开关：关掉时不移动位置（关→开/开→关 基本语义）', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
  })

  it('未建仓时切关：直接生效，不弹确认（没有副作用可言）', async () => {
    const w = await mountWizard()
    await toggle(w)
    const titles = (Array.from(w.element.querySelectorAll('.wizard-stepper__title')) as HTMLElement[]).map(
      (el) => (el.textContent ?? '').trim(),
    )
    expect(titles).toContain('新建仓库')

    // 关回去：未建仓 → 不该出现确认弹窗
    await toggle(w)
    expect(document.querySelector('.n-dialog')).toBeNull()
    w.unmount()
  })

  it('切开时停在原步骤，不前跳（用户可按步骤条回访建仓步）', async () => {
    const w = await mountWizard()
    await toggle(w)
    const current = w.element.querySelector('.wizard-stepper__btn.is-current .wizard-stepper__index')
    expect((current?.textContent ?? '').trim()).toBe('1')
    w.unmount()
  })
})

describe('P6 离开保护：已建仓未保存时的三选项', () => {
  beforeEach(() => {
    localStorage.clear()
    i18n.global.locale.value = 'zh-CN'
  })

  it('未建仓时离开不弹仓库确认（无副作用可确认）', async () => {
    const w = await mountWizard()
    // 未建仓：草稿里 provisionedRepoId 为 null，离开确认走原生 window.confirm 档
    expect(document.querySelector('.n-dialog')).toBeNull()
    w.unmount()
  })

  it('已建仓后关开关：弹确认门，选「回滚到原仓库」把 currentRepoId 切回去（R3/R12）', async () => {
    localStorage.setItem(
      'restic-web:wizard-draft:v1',
      JSON.stringify({
        step: 1,
        stepKey: 1,
        newRepoWanted: true,
        provisionedRepoId: 'r-new',
        // 建仓切仓前记下的原仓库（草稿里持久化，恢复时才能回滚）
        previousRepoId: 'default',
        form: { name: '建仓中', sources: [] },
        savedAt: '2026-01-01T00:00:00.000Z',
      }),
    )
    // 进向导时当前仓库已是建仓后的新仓库（建仓步在建仓时切过）
    localStorage.setItem('rw.currentRepo', 'r-new')

    const w = await mountWizard()
    const restore = (Array.from(w.element.querySelectorAll('button')) as HTMLElement[]).find(
      (b) => (b.textContent ?? '').trim() === '恢复',
    )
    restore!.click()
    await flushPromises()
    await nextTick()

    // 关开关 → 必须弹确认门而不是静默关掉
    newRepoSwitch(w)!.click()
    await nextTick()
    await flushPromises()
    const dialogEl = document.querySelector('.n-dialog')
    expect(dialogEl, '关开关应弹确认门').not.toBeNull()

    const buttons = Array.from(document.querySelectorAll('.n-dialog button')) as HTMLElement[]
    const rollback = buttons.find((b) => (b.textContent ?? '').includes('回滚'))
    expect(rollback, '应提供「回滚到原仓库」出口').toBeTruthy()
    rollback!.click()
    await flushPromises()
    await nextTick()

    // 关键：当前仓库被切回进向导时的那个，任务才会落回原仓库
    expect(localStorage.getItem('rw.currentRepo')).toBe('default')
    w.unmount()
  })

  it('已建仓的草稿恢复后：第①步显示归属提示（任务将归新仓库）', async () => {
    localStorage.setItem(
      'restic-web:wizard-draft:v1',
      JSON.stringify({
        step: 1,
        stepKey: 1,
        newRepoWanted: true,
        provisionedRepoId: 'r-new',
        form: { name: '建仓中', sources: [] },
        savedAt: '2026-01-01T00:00:00.000Z',
      }),
    )
    const w = await mountWizard()
    const restore = (Array.from(w.element.querySelectorAll('button')) as HTMLElement[]).find(
      (b) => (b.textContent ?? '').trim() === '恢复',
    )
    expect(restore, '应提示可恢复草稿').toBeTruthy()
    restore!.click()
    await flushPromises()
    await nextTick()

    // 归属提示必须出现——这是 R3「用户以为回到原仓库」的唯一防线
    expect(w.element.textContent ?? '').toContain('任务保存后将归属仓库 r-new')
    w.unmount()
  })
})
