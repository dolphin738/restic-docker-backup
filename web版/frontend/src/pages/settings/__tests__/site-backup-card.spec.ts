/**
 * 设置页「全站备份 / 恢复」卡片行为回归（方案 §10.1 W5 验收：危险操作二次确认 + §5.5 提示进 UI）。
 *
 * 存在理由：本卡是**整机替换**入口，三条纪律只能靠真挂载守住——
 *   ① 预检有阻碍项时导入入口必须禁用（否则用户点了才发现 422）；
 *   ② 导入必须经 ConfirmGate 关键词门，且 acknowledge 逐字等于包外层 created_at；
 *   ③ 旧「配置迁移」包要被点名拒绝（D6 不做兼容），不能当作坏 JSON 一笔带过。
 *
 * 隔离点：只 mock @/api/siteBackup（数据层），不 mock http——本卡的契约由
 * api/__tests__/siteBackup.spec.ts 单独看守，这里只测页面接线。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SectionSiteBackup from '@/pages/settings/sections/SectionSiteBackup.vue'
import ConfirmGate from '@/components/ConfirmGate.vue'
import { i18n } from '@/i18n'
import zhCN from '@/i18n/locales/zh-CN'
import { useUiStore } from '@/stores/ui'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  inspectReport: null as Record<string, unknown> | null,
  inspectCalls: [] as Array<Record<string, unknown>>,
  importCalls: [] as Array<Record<string, unknown>>,
  importError: null as unknown,
}))

vi.mock('@/api/siteBackup', async () => {
  const actual = await vi.importActual<typeof import('@/api/siteBackup')>('@/api/siteBackup')
  return {
    ...actual,
    exportSiteBackup: vi.fn(async () => ({
      format: 'restic-web-site-backup',
      version: 1,
      created_at: '2026-11-04T02:11:07Z',
      encryption: {},
      payload: '',
    })),
    inspectSiteBackup: vi.fn(async (body: Record<string, unknown>) => {
      mocks.inspectCalls.push(body)
      return mocks.inspectReport
    }),
    importSiteBackup: vi.fn(async (body: Record<string, unknown>) => {
      mocks.importCalls.push(body)
      if (mocks.importError) {
        throw mocks.importError
      }
      return {
        applied: { repos: 2, tasks: 3, config: 10, forms: 1, passfiles: 2, rclone_conf: 1, rclone_pass: 0 },
        normalized_env_keys: [],
        warnings: [],
        rollback_dir: '20261104T021107Z-ab12cd',
        logged_out: true,
      }
    }),
  }
})

function report(blockers: string[] = [], targetOverrides: Record<string, unknown> = {}) {
  return {
    pack: {
      format: 'restic-web-site-backup',
      version: 1,
      created_at: '2026-11-04T02:11:07Z',
      schema_version: 10,
      app_version: '1.0.0',
      deploy_mode: 'docker',
      repos: 2,
      tasks: 3,
      config_keys: 10,
      passfiles: 2,
      rclone_conf_included: true,
      rclone_conf_encrypted: false,
      rclone_forms: 1,
      rclone_pass_included: false,
      warnings: [],
    },
    target: {
      repos: 1,
      repos_configured: 0,
      tasks: 0,
      config_keys: 2,
      jobs: 7,
      active_jobs: [],
      restic_available: true,
      passfile_writable: true,
      rclone_conf_writable: true,
      rclone_conf_encrypted: false,
      rollback_keep_count: 3,
      ...targetOverrides,
    },
    blockers,
  }
}

/** 选文件用的包原文：带完整明文头（`created_at` 必须非空——它是 `acknowledge` 的比对基准）。 */
function packJson(): string {
  return JSON.stringify({
    format: 'restic-web-site-backup',
    version: 1,
    created_at: '2026-11-04T02:11:07Z',
    encryption: { algo: 'PBKDF2-HMAC-SHA256', iterations: 600000 },
    payload: 'ZmFrZQ==',
  })
}

function mountCard() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/login', name: 'login', component: { template: '<div />' } }],
  })
  // pinia 单独持有并设为 active：抑制位断言要读**同一个** store 实例
  const pinia = createPinia()
  setActivePinia(pinia)
  return {
    router,
    ui: useUiStore(),
    wrapper: mount(NConfigProvider, {
      props: { themeOverrides: lightThemeOverrides },
      slots: {
        default: () => h(NMessageProvider, null, { default: () => h(SectionSiteBackup) }),
      },
      global: { plugins: [i18n, pinia, router] },
    }),
  }
}

/** 选文件：happy-dom 下 input.files 不可写，直接经 change 事件注入 File。 */
async function pickFile(wrapper: ReturnType<typeof mountCard>['wrapper'], text: string): Promise<void> {
  const input = wrapper.find('input[type="file"]')
  const file = new File([text], 'site.json', { type: 'application/json' })
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
  await flushPromises()
}

async function fillPasswordAndInspect(
  wrapper: ReturnType<typeof mountCard>['wrapper'],
  password = 'the-export-password',
): Promise<void> {
  // 卡上有两个口令框（导出 / 导入）：按占位符取**导入**那个，否则填的是导出口令，
  // 预检按钮仍禁用（importPassword.length < 1）→ 后续断言全落空。
  const passwordInput = wrapper
    .findAll('input')
    .find((i) => i.attributes('placeholder') === zhCN.settings.siteBackup.importPassword)
  await passwordInput!.setValue(password)
  const inspectBtn = wrapper.findAll('button').find((b) => b.text() === zhCN.settings.siteBackup.inspectBtn)
  await inspectBtn!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  localStorage.clear()
  mocks.inspectReport = report()
  mocks.inspectCalls = []
  mocks.importCalls = []
  mocks.importError = null
})

describe('全站备份 / 恢复卡：§5.5 威胁提示与操作纪律', () => {
  it('挂载无异常，且两类提示（包+口令同时到手 / 分开保管）都在界面上', async () => {
    const { wrapper } = mountCard()
    await flushPromises()

    const html = wrapper.html()
    // §5.5 威胁模型：必须明写「包 + 口令同时到手」的后果
    expect(html).toContain(zhCN.settings.siteBackup.securityNote)
    // 操作纪律：包与口令分开保管
    expect(html).toContain(zhCN.settings.siteBackup.securityDiscipline)
    expect(html).toContain(zhCN.settings.siteBackup.includeRcloneHint)
    wrapper.unmount()
  })
})

describe('全站备份 / 恢复卡：预检 → 导入三段式', () => {
  it('预检无阻碍：展示包摘要与目标现状，且「执行整机替换」可点', async () => {
    const { wrapper } = mountCard()
    await flushPromises()

    await pickFile(wrapper, packJson())
    await fillPasswordAndInspect(wrapper)

    expect(mocks.inspectCalls).toHaveLength(1)
    // 目标现状必须显示将被清空的 jobs 行数（§4.4 副作用明示）
    expect(wrapper.text()).toContain('7')
    expect(wrapper.text()).toContain(zhCN.settings.siteBackup.importReady)

    const importBtn = wrapper
      .findAll('button')
      .find((b) => b.text() === zhCN.settings.siteBackup.importBtn)
    expect(importBtn).toBeTruthy()
    expect(importBtn!.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('预检有阻碍项：逐条展示且导入入口禁用（不得让用户点到 422）', async () => {
    mocks.inspectReport = report(['仓库密码文件目录不可写（挂载只读或无权限）'])
    const { wrapper } = mountCard()
    await flushPromises()

    await pickFile(wrapper, packJson())
    await fillPasswordAndInspect(wrapper)

    expect(wrapper.text()).toContain('仓库密码文件目录不可写（挂载只读或无权限）')
    expect(wrapper.text()).toContain(zhCN.settings.siteBackup.blockers)
    const importBtn = wrapper
      .findAll('button')
      .find((b) => b.text() === zhCN.settings.siteBackup.importBtn)
    expect(importBtn!.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('旧「配置迁移」包：点名拒绝，不发预检请求（D6 不做兼容）', async () => {
    const { wrapper } = mountCard()
    await flushPromises()

    await pickFile(wrapper, JSON.stringify({ format: 'restic-web-config-export' }))
    await flushPromises()

    expect(mocks.inspectCalls).toHaveLength(0)
    expect(wrapper.text()).not.toContain('site.json')
    wrapper.unmount()
  })
})

describe('全站备份 / 恢复卡：危险操作二次确认（ConfirmGate）', () => {
  it('点「执行整机替换」只开门，不发请求；确认后请求带 mode=replace 与逐字 acknowledge', async () => {
    const { wrapper } = mountCard()
    await flushPromises()

    await pickFile(wrapper, packJson())
    await fillPasswordAndInspect(wrapper)

    const importBtn = wrapper
      .findAll('button')
      .find((b) => b.text() === zhCN.settings.siteBackup.importBtn)
    await importBtn!.trigger('click')
    await flushPromises()

    // 只开门：请求尚未发出（ConfirmentGate 是人工确认的第一道）
    expect(mocks.importCalls).toHaveLength(0)
    const gate = wrapper.findComponent(ConfirmGate)
    expect(gate.exists()).toBe(true)
    expect(gate.props('keyword')).toBe('REPLACE')

    gate.vm.$emit('confirm')
    await flushPromises()
    await flushPromises()

    expect(mocks.importCalls).toHaveLength(1)
    const body = mocks.importCalls[0]!
    expect(body['mode']).toBe('replace')
    // §4.2 / §4.7：逐字等于**包外层** created_at
    expect(body['acknowledge']).toBe('2026-11-04T02:11:07Z')
    wrapper.unmount()
  })

  it('导入成功：展示各段计数与回滚点，并给「前往登录」（sessions 已被清空）', async () => {
    const { wrapper } = mountCard()
    await flushPromises()

    await pickFile(wrapper, packJson())
    await fillPasswordAndInspect(wrapper)
    const importBtn = wrapper
      .findAll('button')
      .find((b) => b.text() === zhCN.settings.siteBackup.importBtn)
    await importBtn!.trigger('click')
    await flushPromises()
    wrapper.findComponent(ConfirmGate).vm.$emit('confirm')
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain(zhCN.settings.siteBackup.doneTitle)
    expect(wrapper.text()).toContain('20261104T021107Z-ab12cd')
    expect(wrapper.text()).toContain(zhCN.settings.siteBackup.relogin)
    const relogin = wrapper
      .findAll('button')
      .find((b) => b.text() === zhCN.settings.siteBackup.reloginBtn)
    expect(relogin).toBeTruthy()
    wrapper.unmount()
  })
})

/**
 * 真机验收缺陷的回归护栏（Playwright 稳定复现 3 次）：导入成功后服务端清空 sessions，
 * 顶栏两个 15s 轮询拿到的 401 会在约 7.7s 后触发 clearMe + 跳 /login，把刚渲染的
 * 导入报告（含回滚点目录名——误导入后唯一回退依据）冲掉。
 *
 * 修法两层：① 导入成功即置 ui.suppressAuthRedirect（http 层 401 只清态不跳转）；
 * ② 用户显式点「前往登录」时先清标志再跳，或组件卸载时兜底清。
 */
describe('全站备份 / 恢复卡：导入后的鉴权跳转抑制（报告不被 401 冲掉）', () => {
  /** 走到「导入成功」这一态（复用前面用例的完整交互路径）。 */
  async function reachImported(mounted: ReturnType<typeof mountCard>) {
    await pickFile(mounted.wrapper, packJson())
    await fillPasswordAndInspect(mounted.wrapper)
    const importBtn = mounted.wrapper
      .findAll('button')
      .find((b) => b.text() === zhCN.settings.siteBackup.importBtn)
    await importBtn!.trigger('click')
    await flushPromises()
    mounted.wrapper.findComponent(ConfirmGate).vm.$emit('confirm')
    await flushPromises()
    await flushPromises()
  }

  it('导入成功且 logged_out：抑制位被置起（顶栏轮询的 401 此后不再跳转）', async () => {
    const mounted = mountCard()
    await flushPromises()
    expect(mounted.ui.suppressAuthRedirect).toBe(false)

    await reachImported(mounted)

    expect(mounted.ui.suppressAuthRedirect).toBe(true)
    // 会话态已清（界面不得继续显示「已登录」）——与抑制位成对
    expect(mounted.ui.me).toBeNull()
    mounted.wrapper.unmount()
  })

  it('点「前往登录」：抑制位已清再跳转（离开本页后 401 恢复自动跳转）', async () => {
    const mounted = mountCard()
    await flushPromises()
    await reachImported(mounted)
    expect(mounted.ui.suppressAuthRedirect).toBe(true)

    const relogin = mounted.wrapper
      .findAll('button')
      .find((b) => b.text() === zhCN.settings.siteBackup.reloginBtn)
    await relogin!.trigger('click')
    await flushPromises()

    expect(mounted.ui.suppressAuthRedirect).toBe(false)
    expect(mounted.router.currentRoute.value.name).toBe('login')
    mounted.wrapper.unmount()
  })

  it('不点按钮直接离开（组件卸载）：抑制位兜底清除，不污染其他页面', async () => {
    const mounted = mountCard()
    await flushPromises()
    await reachImported(mounted)
    expect(mounted.ui.suppressAuthRedirect).toBe(true)

    mounted.wrapper.unmount()

    expect(mounted.ui.suppressAuthRedirect).toBe(false)
  })
})
