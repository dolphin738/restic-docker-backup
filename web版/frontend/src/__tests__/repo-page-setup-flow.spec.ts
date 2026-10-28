/**
 * 仓库页「建仓闭环」行为回归（2026-10-21）：保存 → 测试 → 设密码 → 初始化，一屏完成。
 *
 * 存在理由：此前编辑态的测试结论区只把「初始化仓库」按钮置灰并提示「密码文件缺失，
 * 请先设置仓库密码」，却**不给密码出口**——用户必须退回列表态、在行内点「设置密码」
 * 再回到编辑态，建仓流程被切成两屏。本规格锁住两件事：
 *   ① 未初始化 + 密码缺失 → 同屏出现「设置仓库密码」，且「初始化仓库」仍禁用（门禁不松）；
 *   ② 密码已配置 → 不再出现密码按钮，「初始化仓库」可用（不放过期入口）。
 *
 * 追加 2026-10-21 死循环修复回归（第二个 describe）：删除仓库不触碰磁盘数据 ⇒ 同址新建
 * 撞上打不开的旧仓库（报「密码错误」）。锁三件事：
 *   ③ 本地 FS 仓库密码错误 → 行内与编辑态都给「重建仓库」出口（原仅 default 有）；
 *   ④ 远程后端密码错误 → 不给重建按钮（无法自动清空），给手动处理提示；
 *   ⑤ 删除确认门「同时删除磁盘数据」勾选 → deleteRepo 收到 purgeDisk=true（不勾则 false）。
 *
 * 断言只走 DOM 文案（i18n zh-CN），不依赖组件内部结构。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RepoPage from '@/pages/repo/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  passfile: 'missing' as 'missing' | 'configured',
  /** 列表行数据（行内初始化/重设/重建入口的判定源）；默认仅 default 一座本地仓。 */
  repos: [
    {
      id: 'default',
      name: '默认仓库',
      type: 'local',
      url: '/repo/default',
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'missing' as 'missing' | 'configured',
      created_at: '2026-10-01T00:00:00Z',
    },
  ] as Array<Record<string, unknown>>,
  /** 行状态探测结论（rowStatusQueries 数据源）。 */
  rowStatus: { connected: false, failure_kind: 'not_initialized' } as Record<string, unknown>,
  testResult: {
    ok: false,
    latency_ms: 12,
    reason: '仓库不存在或未初始化（请检查 repo.url，或先执行 init）',
    failure_kind: 'not_initialized',
  } as Record<string, unknown>,
  /** 删除门占用明细（删除保护方案 P1）：默认「12 个快照、无镜像引用」。 */
  usage: { repo_id: 'x', mirror_refs: [], snapshot_total: 12 } as Record<string, unknown>,
}))

vi.mock('@/api/repo', () => ({
  listRepos: vi.fn(async () => mocks.repos),
  getRepo: vi.fn(async () => ({ ...mocks.repos[0], passfile_status: mocks.passfile })),
  getRepoStatus: vi.fn(async () => mocks.rowStatus),
  getRepoUsage: vi.fn(async () => mocks.usage),
  listRcloneRemotes: vi.fn(async () => ({ conf_present: false, remotes: [] })),
  listRcloneConfigs: vi.fn(async () => ({
    conf_path: "/etc/rclone/rclone.conf",
    conf_present: false,
    configs: [],
    available: true,
    reason: null,
  })),
  createRepo: vi.fn(async () => ({ id: 'repo-new' })),
  deleteRepo: vi.fn(async () => ({ ok: true })),
  updateRepo: vi.fn(async () => ({ ok: true })),
  testRepo: vi.fn(async () => mocks.testResult),
  manualCopy: vi.fn(async () => ({ ok: true, exit_code: 0, message: '' })),
  rotateRepoKey: vi.fn(async () => ({ ok: true })),
  rebuildRepo: vi.fn(async () => ({ ok: true, generated: false, password: null })),
  setRepoPassword: vi.fn(async () => ({ generated: false, password: null })),
  suggestRepoId: vi.fn(async () => ({ id: 'repo-auto' })),
}))

vi.mock('@/api/backupTasks', () => ({
  listBackupTasks: vi.fn(async () => ({ items: [], total: 0 })),
  deleteBackupTask: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/jobs', () => ({
  createJob: vi.fn(async () => ({ id: 'job-1' })),
  cancelJob: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(async () => ({ items: [] })),
  putConfig: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/api/fs', () => ({
  browseFs: vi.fn(async () => ({ path: '/', mounts: [], items: [], next_cursor: null })),
}))

vi.mock('@/api/settings', () => ({
  getCacheUsage: vi.fn(async () => ({
    size_bytes: 1024,
    items_count: 3,
    path: '/cache',
    measured_at: '2026-10-19T08:00:00Z',
    truncated: false,
  })),
  clearCache: vi.fn(async () => ({ ok: true })),
}))

// init 进度卡用例需要实例化 JobProgress；真实 useJobStream 会在 jsdom 里开 SSE（无 EventSource）。
// 替身只提供渲染所需的最小只读态，不订阅任何流。
vi.mock('@/composables/useJobStream', async () => {
  const { ref } = await import('vue')
  return {
    useJobStream: () => ({
      state: ref('open'),
      lastEventId: ref(0),
      logs: ref([]),
      start: () => {},
      stop: () => {},
    }),
  }
})

function mountRepoPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: RepoPage }],
  })
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(RepoPage) }) },
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router] },
  })
}

/** 进入编辑态（点行内「管理」）并跑一次连接测试，停在失败结论上。 */
async function enterEditAndTest(w: ReturnType<typeof mountRepoPage>) {
  await w.findAll('button').find((b) => b.text() === '管理')?.trigger('click')
  await flushPromises()
  await w.findAll('button').find((b) => b.text() === '立即测试')?.trigger('click')
  await flushPromises()
}

function buttonByText(w: ReturnType<typeof mountRepoPage>, label: string) {
  return w.findAll('button').find((b) => b.text() === label)
}

/** 确认门是 teleport 到 body 的 n-modal，不在 wrapper 内 → 只能在 document 上按文案找按钮。 */
function bodyButton(label: string): HTMLButtonElement | undefined {
  return Array.from(document.body.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === label,
  )
}

async function clickBody(label: string): Promise<void> {
  bodyButton(label)?.click()
  await flushPromises()
}

/** 走完**无关键词**确认门（预览 → 二次确认）：归档分支的轻门。 */
async function passLightGate(confirmLabel = '归档'): Promise<boolean> {
  await clickBody('下一步')
  const confirmBtn = bodyButton(confirmLabel)
  if (!confirmBtn) {
    return false
  }
  confirmBtn.click()
  await flushPromises()
  return true
}

/** 走完 ConfirmGate 三步（预览 → 输入 INIT → 二次确认），返回是否成功触发确认。 */
async function passConfirmGate(keyword: string, confirmLabel = '确认执行'): Promise<boolean> {
  await clickBody('下一步')
  const input = document.body.querySelector('input')
  if (!input) {
    return false
  }
  input.value = keyword
  input.dispatchEvent(new Event('input'))
  await flushPromises()
  await clickBody('下一步')
  const confirmBtn = bodyButton(confirmLabel)
  if (!confirmBtn) {
    return false
  }
  confirmBtn.click()
  await flushPromises()
  return true
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  localStorage.clear()
  mocks.passfile = 'missing'
  mocks.rowStatus = { connected: false, failure_kind: 'not_initialized' }
  mocks.repos = [
    {
      id: 'default',
      name: '默认仓库',
      type: 'local',
      url: '/repo/default',
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: mocks.passfile,
      created_at: '2026-10-01T00:00:00Z',
    },
  ]
  mocks.testResult = {
    ok: false,
    latency_ms: 12,
    reason: '仓库不存在或未初始化（请检查 repo.url，或先执行 init）',
    failure_kind: 'not_initialized',
  }
})

describe('仓库页：建仓闭环（设密码 → 初始化）', () => {
  it('未初始化且密码缺失：同屏给「设置仓库密码」，初始化仍禁用', async () => {
    const w = mountRepoPage()
    await flushPromises()
    await enterEditAndTest(w)

    expect(buttonByText(w, '设置仓库密码')).toBeDefined()
    const initBtn = buttonByText(w, '初始化仓库')
    expect(initBtn).toBeDefined()
    expect(initBtn?.attributes('disabled')).toBeDefined() // 门禁不松：先设密码
    // 注：禁用原因挂在 n-tooltip 上，hover 才渲染，故不在此断言文案（只断言按钮态）
    w.unmount()
  })

  it('密码已配置：不再出现密码按钮，初始化可用', async () => {
    mocks.passfile = 'configured'
    const w = mountRepoPage()
    await flushPromises()
    await enterEditAndTest(w)

    expect(buttonByText(w, '设置仓库密码')).toBeUndefined()
    const initBtn = buttonByText(w, '初始化仓库')
    expect(initBtn?.attributes('disabled')).toBeUndefined()
    w.unmount()
  })

  it('失败结论非「未初始化」时不给初始化入口（避免误建仓库）', async () => {
    mocks.testResult = {
      ok: false,
      latency_ms: 8,
      reason: '连接被拒绝',
      failure_kind: 'network',
    }
    const w = mountRepoPage()
    await flushPromises()
    await enterEditAndTest(w)

    expect(buttonByText(w, '初始化仓库')).toBeUndefined()
    // 密码缺失的出口仍在（密码状态行），不逼用户退回列表态
    expect(buttonByText(w, '设置仓库密码')).toBeDefined()
    w.unmount()
  })

  it('编辑态提交初始化后就地显示进度卡，且不带百分比条（init 无进度事件）', async () => {
    mocks.passfile = 'configured'
    const w = mountRepoPage()
    await flushPromises()
    await enterEditAndTest(w)

    // 提交前：没有进度卡
    expect(w.element.querySelectorAll('.job-progress').length).toBe(0)

    buttonByText(w, '初始化仓库')?.trigger('click')
    await flushPromises()
    expect(await passConfirmGate('INIT')).toBe(true)

    // 关键回归：编辑态整页替换了列表，行内进度卡不渲染 → 编辑态必须自带一份，
    // 否则点完初始化只剩「已提交」提示，进度/日志全丢且 done 回调永不触发。
    const cards = w.element.querySelectorAll('.job-progress')
    expect(cards.length).toBe(1)
    expect(w.text()).toContain('初始化仓库任务')
    // restic init 走 json_output=False，无 progress 事件 → 不显示 0% 的假进度条
    expect(w.element.querySelectorAll('.job-progress__bar').length).toBe(0)
    w.unmount()
  })

  it('取消初始化确认门后按钮不再转圈（未提交作业则清目标标记）', async () => {
    mocks.passfile = 'configured'
    const w = mountRepoPage()
    await flushPromises()
    await enterEditAndTest(w)

    await buttonByText(w, '初始化仓库')?.trigger('click')
    await flushPromises()
    // 门打开期间按钮挂 loading（initTargetId 已绑）；关掉门必须回到常态——
    // 否则未提交也永久转圈，按钮看上去「一直在初始化」。
    const spinning = () =>
      buttonByText(w, '初始化仓库')?.element.querySelectorAll('.n-base-loading').length ?? 0
    expect(spinning()).toBe(1)
    await clickBody('取消')
    await flushPromises()
    expect(spinning()).toBe(0)
    w.unmount()
  })
})

describe('仓库页：死循环修复（重建入口放开 + 删除磁盘清理）', () => {
  /** 行内密码错误态：密码文件在 + 探测结论密码错误（删除仓库不触碰磁盘 ⇒ 同址新建的典型现场）。 */
  function usePasswordWrongRow() {
    mocks.passfile = 'configured'
    mocks.rowStatus = { connected: false, failure_kind: 'password' }
    // 行级判定读的是各行自带的 passfile_status（与 getRepo 的实时口径保持一致）
    mocks.repos = mocks.repos.map((r) => ({ ...r, passfile_status: 'configured' }))
  }

  it('本地 FS 仓库密码错误：行内给「重建仓库」，过确认门后调用 rebuildRepo', async () => {
    usePasswordWrongRow()
    const w = mountRepoPage()
    await flushPromises()

    const rebuildBtn = buttonByText(w, '重建仓库')
    expect(rebuildBtn).toBeDefined()
    await rebuildBtn?.trigger('click')
    await flushPromises()

    // 三门确认门（关键词 REBUILD）：清空全部快照的不可逆动作，门槛不降
    expect(await passConfirmGate('REBUILD')).toBe(true)
    const repoApi = await import('@/api/repo')
    expect(repoApi.rebuildRepo).toBeCalledWith('default', {})
    w.unmount()
  })

  it('远程后端密码错误：不给「重建仓库」（无法自动清空），给手动处理提示', async () => {
    usePasswordWrongRow()
    mocks.repos = [
      { ...mocks.repos[0], passfile_status: 'configured' },
      {
        id: 'r-s3',
        name: 'S3 仓库',
        type: 's3',
        url: 's3:s3.example.com/bucket/restic',
        host_tag: null,
        url_source: 'db',
        host_tag_source: 'db',
        passfile_status: 'configured',
        created_at: '2026-10-01T00:00:00Z',
      },
    ]
    const w = mountRepoPage()
    await flushPromises()

    // 全页只有 default（本地 FS）一枚重建按钮；S3 行只有提示文案
    const rebuildButtons = w.findAll('button').filter((b) => b.text() === '重建仓库')
    expect(rebuildButtons.length).toBe(1)
    expect(w.text()).toContain('远程后端无法自动清空')
    w.unmount()
  })

  it('编辑态密码错误：本地仓库同屏给「重建仓库」出口', async () => {
    usePasswordWrongRow()
    mocks.testResult = {
      ok: false,
      latency_ms: 9,
      reason: '仓库密码错误（请检查仓库密码文件挂载）',
      failure_kind: 'password',
    }
    const w = mountRepoPage()
    await flushPromises()
    await enterEditAndTest(w)

    // 不逼用户退回列表态：重设 + 重建两个出口都在编辑态内联测试区
    expect(buttonByText(w, '重设密码')).toBeDefined()
    expect(buttonByText(w, '重建仓库')).toBeDefined()
    w.unmount()
  })

  it('删除门勾选「同时删除磁盘数据」：deleteRepo 收到 purgeDisk=true', async () => {
    mocks.repos = [
      { ...mocks.repos[0], passfile_status: 'configured' },
      {
        id: 'r2',
        name: '二号仓库',
        type: 'local',
        url: '/repo/r2',
        host_tag: null,
        url_source: 'db',
        host_tag_source: 'db',
        passfile_status: 'configured',
        created_at: '2026-10-01T00:00:00Z',
      },
    ]
    const w = mountRepoPage()
    await flushPromises()

    // default 行删除禁用 → 点非默认行的「删除」
    const deleteBtn = w
      .findAll('button')
      .find((b) => b.text() === '删除' && b.attributes('disabled') === undefined)
    expect(deleteBtn).toBeDefined()
    await deleteBtn?.trigger('click')
    await flushPromises()

    // 勾选出现在预览步（第 1 步）：本地 FS 才显示。naive-ui checkbox 是 div[role=checkbox]，
    // 没有 input 元素——点它本身，勾中态以 n-checkbox--checked 类标记（VTU 挂在脱离文档的
    // div 上，确认门却 teleport 到 body，故只能在 document 上找；按文案定位防误中）。
    const checkbox = Array.from(document.body.querySelectorAll('.n-checkbox')).find((el) =>
      el.textContent?.includes('同时销毁磁盘数据'),
    ) as HTMLElement | null
    expect(checkbox).not.toBeNull()
    checkbox?.click()
    await flushPromises()
    expect(checkbox?.classList.contains('n-checkbox--checked')).toBe(true)

    // 勾选销毁 → 重门，关键词 = 仓库 id（不再是固定 DELETE）
    expect(await passConfirmGate('r2', '销毁并删除')).toBe(true)
    const repoApi = await import('@/api/repo')
    expect(repoApi.deleteRepo).toBeCalledWith('r2', { purgeDisk: true })
    w.unmount()
  })

  it('删除门不勾选：deleteRepo 收到 purgeDisk=false（磁盘数据保留）', async () => {
    mocks.repos = [
      { ...mocks.repos[0], passfile_status: 'configured' },
      {
        id: 'r3',
        name: '三号仓库',
        type: 'local',
        url: '/repo/r3',
        host_tag: null,
        url_source: 'db',
        host_tag_source: 'db',
        passfile_status: 'configured',
        created_at: '2026-10-01T00:00:00Z',
      },
    ]
    const w = mountRepoPage()
    await flushPromises()

    const deleteBtn = w
      .findAll('button')
      .find((b) => b.text() === '删除' && b.attributes('disabled') === undefined)
    await deleteBtn?.trigger('click')
    await flushPromises()

    // 不勾 = 归档（可逆）→ 轻门，无需输入关键词
    expect(await passLightGate()).toBe(true)
    const repoApi = await import('@/api/repo')
    expect(repoApi.deleteRepo).toBeCalledWith('r3', { purgeDisk: false })
    w.unmount()
  })
})

/**
 * 删除门「后果可视化」（删除保护方案 P1，裁决 5/6）：
 * 打开删除确认门时按仓库拉一次占用明细，把「快照总数 / 被镜像引用」摊给用户看。
 * 锁三件事：
 *   ⑥ 快照数取**总条数**（后端 tally.total），显示为「该仓库内快照数：12」；
 *   ⑦ 探测不可用（null）显示「不可知」而**不是 0**——0 会让用户误判「从没备过」；
 *   ⑧ 被别仓任务当镜像目的地时列出任务名并明示「删除会被拒绝」。
 */
describe('仓库删除门：占用统计与提示', () => {
  function twoRepos(): void {
    mocks.repos = [
      { ...mocks.repos[0], passfile_status: 'configured' },
      {
        id: 'rd',
        name: '待删仓库',
        type: 'local',
        url: '/repo/rd',
        host_tag: null,
        url_source: 'db',
        host_tag_source: 'db',
        passfile_status: 'configured',
        created_at: '2026-10-01T00:00:00Z',
      },
    ]
  }

  async function openDeleteGate(): Promise<ReturnType<typeof mountRepoPage>> {
    const w = mountRepoPage()
    await flushPromises()
    const btn = w
      .findAll('button')
      .find((b) => b.text() === '删除' && b.attributes('disabled') === undefined)
    await btn?.trigger('click')
    await flushPromises()
    return w
  }

  it('展示快照总条数（非 tag 求和）', async () => {
    twoRepos()
    mocks.usage = { repo_id: 'rd', mirror_refs: [], snapshot_total: 12 }
    const w = await openDeleteGate()
    expect(document.body.textContent).toContain('该仓库内快照数：12')
    w.unmount()
  })

  it('快照数不可知 → 显示「不可知」而不是 0', async () => {
    twoRepos()
    mocks.usage = { repo_id: 'rd', mirror_refs: [], snapshot_total: null }
    const w = await openDeleteGate()
    expect(document.body.textContent).toContain('快照数：不可知')
    expect(document.body.textContent).not.toContain('该仓库内快照数：0')
    w.unmount()
  })

  it('被镜像引用 → 列出任务名并提示删除会被拒绝', async () => {
    twoRepos()
    mocks.usage = {
      repo_id: 'rd',
      mirror_refs: [{ id: 7, name: '财务数据备份' }],
      snapshot_total: 3,
    }
    const w = await openDeleteGate()
    const text = document.body.textContent ?? ''
    expect(text).toContain('仍被 1 个任务作为镜像目的地使用')
    expect(text).toContain('财务数据备份')
    expect(text).toContain('删除会被拒绝')
    w.unmount()
  })
})

/**
 * 仓库不可达告警（删除保护方案 P1-3）：列表级横幅 + 行级「不可达」标签。
 * 目的：让「快照暂时打不开」在仓库页就可见，不必点进快照页才吃 500。
 * 关键口径：**探测结论缺失不误报**（加载中 / 请求失败 → 不标红）。
 */
describe('仓库不可达告警', () => {
  function twoRepos(): void {
    mocks.repos = [
      { ...mocks.repos[0], passfile_status: 'configured' },
      {
        id: 'ru',
        name: '远端仓库',
        type: 'local',
        url: '/repo/ru',
        host_tag: null,
        url_source: 'db',
        host_tag_source: 'db',
        passfile_status: 'configured',
        created_at: '2026-10-01T00:00:00Z',
      },
    ]
  }

  it('探测为不可达 → 横幅列出数量与仓库名，行内出现「不可达」标签', async () => {
    twoRepos()
    mocks.rowStatus = { connected: false, failure_kind: 'network' }
    const w = mountRepoPage()
    await flushPromises()

    // 横幅不是 teleport 内容（挂在列表卡内），故读 wrapper 而非 document.body
    const banner = w.find('.repo__unreachable')
    expect(banner.exists()).toBe(true)
    const text = banner.text()
    expect(text).toContain('2 个仓库当前不可达')
    expect(text).toContain('默认仓库')
    expect(text).toContain('远端仓库')
    // 行级标签（两行各一个）
    expect(w.text()).toContain('不可达')
    w.unmount()
  })

  it('探测为已连接 → 不出现横幅（不误报）', async () => {
    twoRepos()
    mocks.rowStatus = { connected: true, failure_kind: null }
    const w = mountRepoPage()
    await flushPromises()

    expect(w.find('.repo__unreachable').exists()).toBe(false)
    expect(w.text()).not.toContain('个仓库当前不可达')
    w.unmount()
  })
})
