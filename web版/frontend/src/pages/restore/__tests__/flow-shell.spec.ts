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

import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import RestoreProgressRing from '@/components/restore/RestoreProgressRing.vue'
import WizardStepper from '@/components/WizardStepper.vue'
import WizardPage from '@/pages/restore/flow/index.vue'
import { i18n } from '@/i18n'
import { useRuntimeStore } from '@/stores/runtime'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  snapshots: [] as Array<Record<string, unknown>>,
  searchHits: [] as Array<Record<string, unknown>>,
  searchTotal: 0,
  logContent: '' as string,
}))

vi.mock('@/api/snapshots', () => ({
  listSnapshots: vi.fn(async () => ({ items: mocks.snapshots })),
  listSnapshotFiles: vi.fn(async () => ({ items: [], next_cursor: null })),
  searchSnapshotFiles: vi.fn(async () => ({
    snapshot_id: 'snap-1',
    query: '',
    items: mocks.searchHits,
    total: mocks.searchTotal,
    next_cursor: null,
  })),
}))

vi.mock('@/api/restore', () => ({
  INPLACE_TARGET_ROOT: '/sources',
  RESTORE_TARGET_ROOT: '/restore',
  previewRestore: vi.fn(async () => ({ items: [], total: 0, truncated: false })),
  restoreSnapshot: vi.fn(async () => ({
    job_id: 'job-1',
    restore_target: '/restore/2026-10-01_000000',
  })),
}))

// P11：日志卡复用既有 getJobLog（V-4：不新造数据源），这里只需把它 mock 掉
vi.mock('@/api/jobs', () => ({
  getJobLog: vi.fn(async () => ({
    id: 'job-1',
    log_path: '/data/logs/job-1.log',
    tail_bytes: 100,
    content: mocks.logContent,
    truncated: false,
  })),
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
  mocks.searchHits = []
  mocks.searchTotal = 0
})

/** 假 EventSource：`useJobStream` 会 `new EventSource(...)` 建 SSE，测试环境不联网，
 *  用哑实现替掉（不产生真实连接，也不影响 JobProgress 挂载）。 */
class FakeEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  readyState = 0
  onmessage: unknown = null
  onerror: unknown = null
  constructor(url: string) {
    void url
  }
  addEventListener(): void {}
  removeEventListener(): void {}
  close(): void {
    this.readyState = 2
  }
}

describe('WizardStepper（P9 可跳步步骤条 / P20 全站统一步骤条）', () => {
  function mountStepper(current: number, enabled?: Record<number, boolean>) {
    return mount(WizardStepper, {
      props: { steps: STEPS, current, enabled, ariaLabel: '恢复向导步骤' },
    })
  }

  it('① 当前步标 aria-current，已过步标 is-done', () => {
    const w = mountStepper(2)
    const btns = w.findAll('.wizard-stepper__btn')
    expect(btns[1].attributes('aria-current')).toBe('step')
    expect(btns[0].classes()).toContain('is-done')
    expect(btns[2].classes()).not.toContain('is-done')
  })

  it('② V-2：未过步骤 inactive —— 鼠标不可点且键盘不可达', async () => {
    const w = mountStepper(1, { 1: true, 2: false, 3: false })
    const btns = w.findAll('.wizard-stepper__btn')
    expect(btns[1].classes()).toContain('is-inactive')
    expect(btns[1].attributes('tabindex')).toBe('-1')
    expect(btns[1].attributes('aria-disabled')).toBe('true')
    await btns[1].trigger('click')
    expect(w.emitted('select')).toBeUndefined()
  })

  it('③ 可跳步骤点击即 emit select', async () => {
    const w = mountStepper(1, { 1: true, 2: true, 3: false })
    await w.findAll('.wizard-stepper__btn')[1].trigger('click')
    expect(w.emitted('select')?.[0]).toEqual([2])
  })
})

/** 经 router-view 渲染的宿主：onBeforeRouteLeave 必须挂在有 active route record 的
 *  组件上才生效——直接 mount 组件会报「No active route record」且**守卫不注册**，
 *  离开确认用例会假失败（真实运行时组件由 router-view 渲染，无此问题）。 */
const RouterHost = { template: '<router-view />' }

/** 挂载向导：宿主路由注册三个步骤 name（契约与真实路由表一致）。 */
async function mountWizard(initialPath: string) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const pinia = createPinia()
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
        plugins: [i18n, pinia, [VueQueryPlugin, { queryClient }], router],
      },
    })
    return { wrapper, router, pinia }
  }

describe('恢复向导外壳（P9 步骤 ↔ 路由 + 离开确认）', () => {
  it('④ 进入 options 路由 → 步骤条当前步为第 2 步', async () => {
    const { wrapper } = await mountWizard('/restore/snap-1/options')
    await flushPromises()
    expect(wrapper.findAll('.wizard-stepper__btn')[1].classes()).toContain('is-current')
  })

  it('⑤ 点第 1 步跳回 → URL 同步到 select-files', async () => {
    const { wrapper, router } = await mountWizard('/restore/snap-1/options')
    await flushPromises()
    await wrapper.findAll('.wizard-stepper__btn')[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('restore-select-files')
    expect(router.currentRoute.value.params.id).toBe('snap-1')
  })

  it('⑥ 未提交作业时第 3 步 inactive（不能跳到进度页）', async () => {
    const { wrapper } = await mountWizard('/restore/snap-1/select-files')
    await flushPromises()
    expect(wrapper.findAll('.wizard-stepper__btn')[2].classes()).toContain('is-inactive')
  })

  it('⑦ 跳步不弹离开确认（内部步骤切换不算离开，防误报回归）', async () => {
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)
    try {
      const { wrapper, router } = await mountWizard('/restore/snap-1/options')
      await flushPromises()
      await wrapper.findAll('.wizard-stepper__btn')[0].trigger('click')
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

describe('P10 文件搜索框（R6 / B3 前端契约）', () => {
  /** B3 契约：debounce ≥300ms 且关键词 ≥2 字符才发请求——后端虽有 30s 缓存，
   *  前端仍须防逐键触发。以下三例锁死该契约（含「未到门槛不发」的反向断言）。 */
  const SEARCH_DEBOUNCE_WAIT = 360

  it('B3-① 关键词 < 2 字符：不发请求', async () => {
    const { searchSnapshotFiles } = await import('@/api/snapshots')
    vi.mocked(searchSnapshotFiles).mockClear()
    const { wrapper } = await mountWizard('/restore/snap-1/select-files')
    await flushPromises()
    await wrapper.find('.restore__search input').setValue('a')
    await new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_WAIT))
    await flushPromises()
    expect(searchSnapshotFiles).not.toHaveBeenCalled()
  })

  it('B3-② 关键词 ≥2 字符：debounce 未到不发，过了才发', async () => {
    const { searchSnapshotFiles } = await import('@/api/snapshots')
    vi.mocked(searchSnapshotFiles).mockClear()
    const { wrapper } = await mountWizard('/restore/snap-1/select-files')
    await flushPromises()
    await wrapper.find('.restore__search input').setValue('etc')
    // debounce 未到：不应发请求
    await flushPromises()
    expect(searchSnapshotFiles).not.toHaveBeenCalled()
    await new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_WAIT))
    await flushPromises()
    expect(searchSnapshotFiles).toHaveBeenCalledWith('snap-1', 'etc')
  })

  it('B3-③ 命中总数展示 + 点命中项加入待恢复清单', async () => {
    mocks.searchHits = [
      { name: 'a.txt', type: 'file', path: '/data/a.txt', display_path: '/data/a.txt', size: 1 },
    ]
    mocks.searchTotal = 3
    const { wrapper } = await mountWizard('/restore/snap-1/select-files')
    await flushPromises()
    await wrapper.find('.restore__search input').setValue('data')
    await new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_WAIT))
    await flushPromises()
    // total 是命中总数（后端给），不是本页条数
    expect(wrapper.text()).toContain('命中 3 个结果')
    await wrapper.find('.restore__hit').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已选 1 个文件/目录')
  })
})

describe('P11 完成态圆环（R9）', () => {
  it('① percent=100 → 满环（stroke-dashoffset=0，无缺口）', () => {
    const w = mount(RestoreProgressRing, { props: { percent: 100, status: 'success', label: '完成' } })
    expect(Number(w.find('.ring__value').attributes('stroke-dashoffset'))).toBe(0)
  })

  it('② 失败态取 error 语义色 + 中心文案透传', () => {
    const w = mount(RestoreProgressRing, { props: { percent: 100, status: 'error', label: '失败' } })
    expect(w.find('.ring__value').classes()).toContain('is-error')
    expect(w.text()).toContain('失败')
  })

  it('③ 越界 percent 归一化（150 → 满环，不产生负偏移反向弧）', () => {
    const w = mount(RestoreProgressRing, { props: { percent: 150, status: 'info' } })
    expect(Number(w.find('.ring__value').attributes('stroke-dashoffset'))).toBe(0)
  })
})

describe('P11 进度页三卡（R8 / SSE 终态）', () => {
  it('④ 走到步骤 3 并触发 done → 三卡齐备 + 圆环出现 + 日志卡走既有 getJobLog', async () => {
    mocks.logContent = 'restoring /data ...\ndone'
    // 快照 paths 为空 → 原位不可用 → 自动回落时间戳流 → 步骤 2 的门无关键词阻塞
    mocks.snapshots = [
      { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: [], size: 1024 },
    ]
    const { getJobLog } = await import('@/api/jobs')
    vi.mocked(getJobLog).mockClear()
    vi.stubGlobal('EventSource', FakeEventSource)

    try {
      const { wrapper } = await mountWizard('/restore/snap-1/select-files')
      await flushPromises()

      // 步骤 1 → 步骤 2（select-files 页脚「下一步」）
      // 步骤 2 → 开门（P16：页脚改 Duplicati 式两端对齐，右按钮文案「提交」）
      const clickByText = async (text: string): Promise<void> => {
        const btn = wrapper.findAll('button').find((b) => b.text().includes(text))
        expect(btn, `未找到按钮：${text}`).toBeTruthy()
        await btn!.trigger('click')
        await flushPromises()
      }
      await clickByText('下一步')
      await clickByText('提交')
      wrapper.findComponent(ConfirmGate).vm.$emit('confirm')
      await flushPromises()

      // 步骤 3：驱动终态（等价于后端 SSE 推送 done）
      wrapper.findComponent(JobProgress).vm.$emit('done', { files_done: 3, summary: null })
      await flushPromises()

      expect(wrapper.text()).toContain('恢复进度')
      expect(wrapper.text()).toContain('结果摘要')
      expect(wrapper.text()).toContain('任务日志')
      expect(wrapper.find('.ring').exists()).toBe(true)
      // V-4：日志复用既有 getJobLog，未新造数据源
      expect(getJobLog).toHaveBeenCalledWith('job-1')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('P16 恢复选项状态与提交前门（§4.9 V-6/V-7）', () => {
  it('⑨ 跨 OS 确认：快照 paths 含 Windows 分隔符 + 原位置流 → confirm 拦截，取消留步 1', async () => {
    mocks.snapshots = [
      { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: ['/data', 'D:\\backup'], size: 1024 },
    ]
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)
    try {
      const { wrapper } = await mountWizard('/restore/snap-1/select-files')
      await flushPromises()
      await wrapper.findAll('button').find((b) => b.text().includes('下一步'))!.trigger('click')
      await flushPromises()
      // 取消 → 不进门，仍停在步骤 1
      expect(confirmSpy).toHaveBeenCalled()
      expect(wrapper.findAll('.wizard-stepper__btn')[1].classes()).not.toContain('is-current')

      confirmSpy.mockReturnValue(true)
      await wrapper.findAll('button').find((b) => b.text().includes('下一步'))!.trigger('click')
      await flushPromises()
      expect(wrapper.findAll('.wizard-stepper__btn')[1].classes()).toContain('is-current')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('⑩ 模式切换 effect：选择位置 → customTarget 复位 restoreRoot，提交链落到 /restore', async () => {
    mocks.snapshots = [
      { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: ['/data'], size: 1024 },
    ]
    const { restoreSnapshot } = await import('@/api/restore')
    vi.mocked(restoreSnapshot).mockClear()
    // 确认门通过后进步骤 3，Progress 挂载会建 SSE——stub 掉（与 ④ 同口径）
    vi.stubGlobal('EventSource', FakeEventSource)
    try {
      const { wrapper } = await mountWizard('/restore/snap-1/select-files')
      await flushPromises()

      await wrapper.findAll('button').find((b) => b.text().includes('下一步'))!.trigger('click')
      await flushPromises()
      // 进入步骤 2（原位置默认）
      expect(wrapper.findAll('.wizard-stepper__btn')[1].classes()).toContain('is-current')

      // 点「选择位置」单选卡 → 父级 restoreFrom 切换 → effect 把 customTarget 复位到 restoreRoot
      const radios = wrapper.findAll('[role="radio"]')
      await radios[1].trigger('click')
      await flushPromises()

      await wrapper.findAll('button').find((b) => b.text().includes('提交'))!.trigger('click')
      await flushPromises()
      wrapper.findComponent(ConfirmGate).vm.$emit('confirm')
      await flushPromises()

      expect(vi.mocked(restoreSnapshot).mock.calls[0]?.[1]).toBe('/restore')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('⑪ host 形态：原位目标换算恒等（不再叠 /sources 前缀，Q1=A/Q2 实拍缺陷回归）', async () => {
    mocks.snapshots = [
      { id: 'snap-1', time: '2026-10-01T00:00:00Z', host: 'host-a', tags: [], paths: ['/srv/data'], size: 1024 },
    ]
    const { restoreSnapshot } = await import('@/api/restore')
    vi.mocked(restoreSnapshot).mockClear()
    vi.stubGlobal('EventSource', FakeEventSource)
    try {
      const { wrapper, pinia } = await mountWizard('/restore/snap-1/select-files')
      // host 形态：runtime 下发 inplace_root=/（展示形态已含主机前缀 → 换算恒等）
      const runtime = useRuntimeStore(pinia)
      runtime.deployMode = 'host'
      runtime.browseRoot = '/'
      runtime.restoreRoot = '/'
      runtime.inplaceRoot = '/'
      await flushPromises()

      await wrapper.findAll('button').find((b) => b.text().includes('下一步'))!.trigger('click')
      await flushPromises()
      await wrapper.findAll('button').find((b) => b.text().includes('提交'))!.trigger('click')
      await flushPromises()
      wrapper.findComponent(ConfirmGate).vm.$emit('confirm')
      await flushPromises()

      // 旧实现会拼出 /sources/srv/data（后端 403）；正确值为展示形态本身
      expect(vi.mocked(restoreSnapshot).mock.calls[0]?.[1]).toBe('/srv/data')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
