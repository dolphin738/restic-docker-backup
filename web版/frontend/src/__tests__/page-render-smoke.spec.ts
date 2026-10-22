/**
 * 页面级渲染冒烟（真挂载，API 全 mock）——
 * 覆盖仪表盘 KPI 四卡统一骨架（2026-10-19）与仓库页「目标位置」列表
 * （对齐 Duplicati destination 模块，2026-10-19）。
 *
 * 存在理由：模板运行时错误（`:is` 函数式图标、行卡 role=button 交互、缺按需导入）
 * 只有真挂载才暴露——vue-tsc 不检查未注册组件名（naive-imports.spec.ts 注释实证），
 * 而完整 e2e 需人工登录；本规格在单测层补上「挂载无异常 + 关键结构 + 主交互」这道网。
 *
 * 布局断言边界：happy-dom 无 CSS 布局，像素等高由 SFC 固定行高（22/36/18）保证，
 * 这里只断言三行骨架的**结构与顺序**在四卡中同构。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider, NAutoComplete, NSelect } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h, type Component } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from '@/pages/dashboard/index.vue'
import RepoPage from '@/pages/repo/index.vue'
import TaskCard from '@/components/TaskCard.vue'
import TaskActionsMenu from '@/components/dashboard/TaskActionsMenu.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'
import { useUiStore } from '@/stores/ui'

/** 可变 mock 数据（vi.hoisted：工厂 hoist 后仍可被测试改写）。 */
const mocks = vi.hoisted(() => ({
  repos: [] as Array<Record<string, unknown>>,
  repoStatus: null as Record<string, unknown> | null,
  statuses: {} as Record<string, Record<string, unknown> | null>,
  backupTasks: [] as Array<Record<string, unknown>>,
}))

vi.mock('@/api/repo', () => ({
  listRepos: vi.fn(async () => mocks.repos),
  getRepo: vi.fn(async (id: string) => mocks.repos.find((r) => r.id === id) ?? mocks.repos[0] ?? null),
  getRepoStatus: vi.fn(async (id: string) => mocks.statuses[id] ?? mocks.repoStatus),
  listRcloneRemotes: vi.fn(async () => ({ conf_present: false, remotes: [] })),
  createRepo: vi.fn(async () => ({ id: 'repo-new' })),
  deleteRepo: vi.fn(async () => ({ ok: true })),
  updateRepo: vi.fn(async () => ({ ok: true })),
  testRepo: vi.fn(async () => ({ ok: true, latency_ms: 12 })),
  manualCopy: vi.fn(async () => ({ ok: true, exit_code: 0, message: '' })),
  rotateRepoKey: vi.fn(async () => ({ ok: true })),
  setRepoPassword: vi.fn(async () => ({ generated: false, password: null })),
  suggestRepoId: vi.fn(async () => ({ id: 'repo-auto' })),
}))

vi.mock('@/api/backupTasks', () => ({
  listBackupTasks: vi.fn(async () => ({ items: mocks.backupTasks, total: mocks.backupTasks.length })),
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

function mountPage(component: Component) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component }],
  })
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: {
      default: () =>
        h(NMessageProvider, null, { default: () => h(component) }),
    },
    global: {
      plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router],
    },
  })
}

beforeEach(() => {
  // 固定中文：happy-dom 的 navigator.language 为 en-US，断言文案以 zh-CN 为准
  i18n.global.locale.value = 'zh-CN'
  localStorage.clear()
  // 注：ui store 的当前仓库跨 mount 保留（模块级单例），切换过的用例须自行复位（见「按行寻址」用例）
  mocks.repos = [
    {
      id: 'default',
      name: '默认仓库',
      type: 'local',
      url: '/repo/default',
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'configured',
      created_at: '2026-10-01T00:00:00Z',
    },
    {
      id: 'repo-2',
      name: '异地 S3',
      type: 's3',
      url: 's3:s3.amazonaws.com/mybucket/path',
      host_tag: 'nas-01',
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'missing',
      created_at: '2026-10-02T00:00:00Z',
    },
    {
      id: 'repo-3',
      name: 'B2 冷备',
      type: 'b2',
      url: 'b2:acct:/backup',
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'configured',
      created_at: '2026-10-03T00:00:00Z',
    },
    {
      id: 'repo-4',
      name: 'SFTP 远端',
      type: 'sftp',
      url: 'sftp:restic@10.0.0.2:/srv/repo',
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'configured',
      created_at: '2026-10-04T00:00:00Z',
    },
  ]
  mocks.repoStatus = {
    connected: true,
    configured: true,
    locked: false,
    last_error: null,
    capacity: { size: 2_147_483_648, snaps: 42 },
    failure_kind: null,
  }
  mocks.statuses = {}
  mocks.backupTasks = [
    {
      id: 1,
      name: '每日全量',
      enabled: true,
      tag: 'task-1',
      cron: '0 3 * * *',
      // 后端 _to_card 派生状态只有 paused/running/pending/last_result/never_run，无 idle
      // （backup_tasks.py:538-545）；沿用 idle 会触发 [intlify] task.status.idle 缺键噪音。
      status: 'success',
      last_run_at: '2026-10-19T03:00:00Z',
      last_result: 'success',
      next_run_at: '2026-10-20T03:00:00Z',
    },
  ]
})

describe('仪表盘 KPI 四卡统一骨架', () => {
  it('挂载无异常；四卡均为「标签行/主行/辅出行」三行同构', async () => {
    const w = mountPage(DashboardPage)
    await flushPromises()

    const cards = w.findAll('.kpi')
    expect(cards.length).toBe(4)
    for (const card of cards) {
      const rows = card.findAll('.kpi__row')
      expect(rows.map((r) => r.classes().join(' '))).toEqual([
        'kpi__row kpi__row--label',
        'kpi__row kpi__row--main',
        'kpi__row kpi__row--sub',
      ])
    }
    w.unmount()
  })

  it('①卡主行为状态标签、辅出行含容量文案（刷新按钮在标签行内）', async () => {
    const w = mountPage(DashboardPage)
    await flushPromises()

    const first = w.findAll('.kpi')[0]
    expect(first.findAll('.kpi__row--label .n-button').length).toBe(1) // 刷新
    expect(first.findAll('.kpi__row--main .status-tag').length).toBe(2) // 连接态 + 锁态
    expect(first.find('.kpi__row--sub').text()).toContain('仓库容量')
    w.unmount()
  })
})

/**
 * D8 三点菜单接线（防「死键」回归）——
 * 存在理由：D8 首批只在 TaskCard 装了菜单，dashboard 未监听 @restore/@verify，
 * 菜单可点但无效果；details 视图操作列更是停留在按钮组占位（原注释自承）。
 * 这两条用例直接驱动菜单 select 并断言落到 createJob('check')，锁死两端接线。
 */
describe('仪表盘 D8 三点菜单接线（防死键回归）', () => {
  it('details 视图：每行操作列渲染三点菜单；select verify → createJob(check, {taskId})', async () => {
    localStorage.setItem('dashboard:viewMode', 'details')
    const { createJob } = await import('@/api/jobs')
    vi.mocked(createJob).mockClear()
    const w = mountPage(DashboardPage)
    await flushPromises()

    // 表格渲染 + 操作列已换成三点菜单（非原按钮组占位）
    expect(w.find('.n-data-table').exists()).toBe(true)
    const menus = w.findAllComponents(TaskActionsMenu)
    expect(menus.length).toBe(1)

    menus[0]!.vm.$emit('select', 'verify')
    await flushPromises()
    expect(vi.mocked(createJob)).toHaveBeenCalledWith('check', { taskId: 1 })

    w.unmount()
    localStorage.removeItem('dashboard:viewMode')
  })

  it('list 视图：任务卡三点菜单 select verify → createJob(check, {taskId})', async () => {
    const { createJob } = await import('@/api/jobs')
    vi.mocked(createJob).mockClear()
    const w = mountPage(DashboardPage)
    await flushPromises()

    const card = w.findComponent(TaskCard)
    expect(card.exists()).toBe(true)
    card.findComponent(TaskActionsMenu).vm.$emit('select', 'verify')
    await flushPromises()
    expect(vi.mocked(createJob)).toHaveBeenCalledWith('check', { taskId: 1 })

    w.unmount()
  })
})

describe('仓库页「目标位置」列表（对齐 Duplicati）', () => {
  it('挂载无异常；列表态三卡顺序 = 仓库状态（上）→ 仓库列表（中）→ 仓库维护（下）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    const text = w.text()
    expect(text.indexOf('仓库状态')).toBeLessThan(text.indexOf('仓库列表'))
    expect(text.indexOf('仓库列表')).toBeLessThan(text.indexOf('仓库维护'))
    // 仓库行卡 + 类型图标（:is 函数式 SVG 真渲染）
    const rows = w.findAll('.repo__dest')
    expect(rows.length).toBe(4)
    for (const row of rows) {
      expect(row.find('.repo__dest-icon svg').exists()).toBe(true)
    }
    // 维护区动作行恒定 6 行（初始化已上移到仓库行内；同类 vnode 克隆缺陷若波及列表态会在此暴露）
    expect(w.findAll('.repo__maint-row').length).toBe(6)
    // 行卡化后 ID 不可再丢：ID 是凭据键 cred.{id}.* 与仓库寻址的锚点（改版前表格有 ID 列）
    expect(rows[0].find('.repo__dest-id').text()).toBe('#default')
    expect(rows[1].find('.repo__dest-id').text()).toBe('#repo-2')
    w.unmount()
  })

  it('编辑态卡头显示「仓库 ID：#id」（新建态为可填 ID，不显示该标签）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    const manageBtn = w.findAll('.repo__dest')[1].findAll('button').find((b) => b.text() === '管理')
    await manageBtn!.trigger('click')
    await flushPromises()
    expect(w.find('.repo__id-chip').text()).toContain('仓库 ID：#repo-2')
    // ui store 是模块级单例：本例把当前仓库切到了 repo-2，须还原（否则泄漏到下例）
    useUiStore().setCurrentRepoId('default')
    w.unmount()
  })

  it('仓库行内「初始化仓库」：未初始化才显示（已初始化/无地址隐藏），密码缺失时禁用', async () => {
    const notInit = { connected: false, configured: true, locked: false, last_error: null, capacity: null }
    const inited = { connected: true, configured: true, locked: false, last_error: null, capacity: null }
    mocks.statuses = {
      'repo-2': notInit, // 未初始化 + 密码缺失（repo-2 passfile_status=missing）
      'repo-3': notInit, // 未初始化 + 已设密码 → 可点
      'repo-4': inited, // 已初始化 → 隐藏
    }
    const w = mountPage(RepoPage)
    await flushPromises()

    const initBtns = (idx: number) =>
      w.findAll('.repo__dest')[idx].findAll('button').filter((b) => b.text() === '初始化仓库')
    // default：沿用全局 status（connected=true）→ 隐藏
    expect(initBtns(0).length).toBe(0)
    // repo-3：未初始化 + 密码已配置 → 显示且可点
    expect(initBtns(2).length).toBe(1)
    expect(initBtns(2)[0].attributes('disabled')).toBeUndefined()
    // repo-4：已初始化 → 隐藏（用户要求：初始化过就不再显示）
    expect(initBtns(3).length).toBe(0)
    // repo-2：未初始化但密码缺失 → 显示但禁用（门禁与维护区原口径一致）
    expect(initBtns(1).length).toBe(1)
    expect(initBtns(1)[0].attributes('disabled')).toBeDefined()
    w.unmount()
  })

  it('密码错误的仓库：行内给「重设密码」（force 覆盖），且不再给「初始化」', async () => {
    mocks.statuses = {
      'repo-3': {
        connected: false,
        configured: true,
        locked: false,
        last_error: '仓库密码错误（请检查仓库密码文件挂载）',
        capacity: null,
        failure_kind: 'password',
      },
    }
    const w = mountPage(RepoPage)
    await flushPromises()
    const ui = useUiStore()

    const row = w.findAll('.repo__dest')[2]
    const labels = row.findAll('button').map((b) => b.text())
    // 密码错误 ≠ 未初始化：init 入口必须消失（否则用户会拿 init 去砸一个打不开的仓库）
    expect(labels).not.toContain('初始化仓库')
    expect(labels).toContain('重设密码')

    // 与行内初始化同口径：先切当前仓库，再开弹窗
    await row.findAll('button').find((b) => b.text() === '重设密码')!.trigger('click')
    await flushPromises()
    expect(ui.currentRepoId).toBe('repo-3')

    ui.setCurrentRepoId('default')
    w.unmount()
  })

  it('仓库行内初始化按行寻址：点击先切当前仓库，再开确认门', async () => {
    mocks.statuses = {
      'repo-3': {
        connected: false,
        configured: true,
        locked: false,
        last_error: null,
        capacity: null,
        failure_kind: 'not_initialized',
      },
    }
    const w = mountPage(RepoPage)
    await flushPromises()
    const ui = useUiStore()

    const row = w.findAll('.repo__dest')[2] // repo-3（非当前仓库）
    await row.findAll('button').find((b) => b.text() === '初始化仓库')!.trigger('click')
    await flushPromises()

    // init 作业按当前仓库寻址：必须先切过去，否则会在错误仓库上建库
    expect(ui.currentRepoId).toBe('repo-3')
    // ui store 跨用例保留：还原，避免污染后续用例
    ui.setCurrentRepoId('default')
    w.unmount()
  })

  it('密码入口下放到仓库行：密码缺失行有「设置仓库密码」，状态卡不再重复放按钮', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    // 默认仓库密码已配置 → 状态行只有标签 + 容量，且不再有密码按钮（已下放到列表行）
    expect(w.text()).toContain('仓库容量')
    expect(w.find('.repo__status-action').exists()).toBe(false)

    // repo-2 密码缺失 → 该行内出现「设置仓库密码」（与「初始化」同位置、逐行判定）
    const row = w.findAll('.repo__dest')[1]
    const setBtn = row.findAll('button').find((b) => b.text() === '设置仓库密码')
    expect(setBtn).toBeTruthy()

    // 点击：先把该仓库设为当前，再打开密码弹窗（与行内初始化同口径）
    const ui = useUiStore()
    await setBtn!.trigger('click')
    await flushPromises()
    expect(ui.currentRepoId).toBe('repo-2')
    ui.setCurrentRepoId('default')
    w.unmount()
  })

  it('默认仓库为当前行（高亮类）；其删除按钮禁用', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    const rows = w.findAll('.repo__dest')
    expect(rows[0].classes()).toContain('repo__dest--current')
    // 行内动作现有「管理 / 删除」两枚按钮（对齐 Duplicati manage-actions）；定位删除按钮断言禁用
    const delBtn = rows[0].findAll('button').find((b) => b.text() === '删除')
    expect(delBtn).toBeTruthy()
    expect(delBtn!.attributes('disabled')).toBeDefined()
    w.unmount()
  })

  it('行点击 = 设为当前（Duplicati 选中目标位置语义）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()
    const ui = useUiStore()
    expect(ui.currentRepoId).toBe('default')

    await w.findAll('.repo__dest')[1].trigger('click')
    expect(ui.currentRepoId).toBe('repo-2')

    // 当前行再点空转（不抛错、不切换）
    await w.findAll('.repo__dest')[1].trigger('click')
    expect(ui.currentRepoId).toBe('repo-2')
    w.unmount()
  })

  it('编辑态：管理 s3 仓库 → 渲染该类型字段（服务器地址 + Bucket + 文件夹路径）且 URL 解析回填、凭据组为该类型', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    const row = w.findAll('.repo__dest')[1] // repo-2 = s3:s3.amazonaws.com/mybucket/path
    const manageBtn = row.findAll('button').find((b) => b.text() === '管理')
    await manageBtn!.trigger('click')
    await flushPromises()

    expect(w.text()).toContain('服务器地址')
    expect(w.text()).toContain('Bucket 名称')
    expect(w.text()).toContain('文件夹路径')
    // 类型下拉必须自动选中被管理仓库的类型（不是靠 formType 兜底才渲染对的字段）
    expect(w.findComponent(NSelect).props('value')).toBe('s3')
    // s3:s3.amazonaws.com/mybucket/path → endpoint/bucket/path 三段解析回填（restic 斜杠方言）
    const values = w.findAll('input').map((i) => (i.element as HTMLInputElement).value)
    expect(values).toContain('s3.amazonaws.com')
    expect(values).toContain('mybucket')
    expect(values).toContain('path')
    // 凭据组按表单所选类型渲染（曾误绑已存仓库类型 → 新建/切类型时配置项全不对）
    expect(w.text()).toContain('AWS Access Key ID')
    // compose/parse 严格互逆 → 打开即非脏：主按钮为「保存配置」而非「测试并保存」
    const footerBtn = w.findAll('.repo__form-footer button').find((b) => b.text() === '保存配置')
    expect(footerBtn).toBeTruthy()
    expect(w.text()).not.toContain('测试并保存')
    w.unmount()
  })

  it('s3 URL 方言回归：restic 斜杠各形态解析回填；canonical 形态组译往返非脏', async () => {
    // 形态逐一核验自 restic 0.19.1 s3 ParseConfig（2026-10-19）：
    //   s3:host[:port]/bucket[/prefix] 裸 endpoint（https）
    //   s3://host/bucket[/prefix]      与上一行同代码路径，语义等价
    //   s3:http(s)://host[:port]/bucket[/prefix]  endpoint 带协议头
    // stable = 组译 URL 与已存字符串一致（非脏）；s3:// 形态会归一成 s3:host 规范写法，
    // 语义等价但字符串变 → 脏（保存即规范化，属预期）。
    const cases = [
      { url: 's3:s3.amazonaws.com/mybucket/backups', endpoint: 's3.amazonaws.com', path: 'backups', stable: true },
      { url: 's3:s3.amazonaws.com/mybucket', endpoint: 's3.amazonaws.com', path: '', stable: true },
      { url: 's3://s3.amazonaws.com/mybucket/backups', endpoint: 's3.amazonaws.com', path: 'backups', stable: false },
      { url: 's3:https://oss-cn-hangzhou.aliyuncs.com/mybucket/backups', endpoint: 'https://oss-cn-hangzhou.aliyuncs.com', path: 'backups', stable: true },
      { url: 's3:http://127.0.0.1:9000/mybucket', endpoint: 'http://127.0.0.1:9000', path: '', stable: true },
    ]
    mocks.repos = cases.map((c, i) => ({
      id: `s3-${i}`,
      name: `S3 ${i}`,
      type: 's3',
      url: c.url,
      host_tag: null,
      url_source: 'db',
      host_tag_source: 'db',
      passfile_status: 'configured',
      created_at: '2026-10-02T00:00:00Z',
    }))

    const w = mountPage(RepoPage)
    await flushPromises()

    for (const [i, c] of cases.entries()) {
      const manageBtn = w.findAll('.repo__dest')[i].findAll('button').find((b) => b.text() === '管理')
      await manageBtn!.trigger('click')
      await flushPromises()
      // 均正确解析出 endpoint/bucket[/path]——不落 raw 原文回退
      expect(w.text()).not.toContain('无法解析已保存的地址')
      const values = w.findAll('input').map((x) => (x.element as HTMLInputElement).value)
      expect(values).toContain(c.endpoint)
      expect(values).toContain('mybucket')
      if (c.path) {
        expect(values).toContain(c.path)
      }
      // compose/parse 互逆性经主按钮文案观测：非脏 = 「保存配置」；脏 = 「测试并保存」
      expect(w.text().includes('测试并保存')).toBe(!c.stable)
      await w.find('.repo__edit-header button').trigger('click')
      await flushPromises()
    }
    w.unmount()
  })

  it('s3 旧冒号方言（restic 解析不了）落 raw 原文回退，不静默错填 Bucket', async () => {
    // 改版前 compose 产出 s3:region:bucket:/path，restic 会把 region:bucket: 当成
    // endpoint，minio-go 域名校验直接拒绝——此类存量 URL 解析必须失败并回退原文，
    // 绝不允许把 backups（路径段）错填进 Bucket。
    mocks.repos = [
      {
        id: 'default',
        name: '默认仓库',
        type: 'local',
        url: '/repo/default',
        host_tag: null,
        url_source: 'db',
        host_tag_source: 'db',
        passfile_status: 'configured',
        created_at: '2026-10-01T00:00:00Z',
      },
      {
        id: 'repo-legacy',
        name: '旧方言 S3',
        type: 's3',
        url: 's3:cn-north-1:mybucket:/backups',
        host_tag: null,
        url_source: 'db',
        host_tag_source: 'db',
        passfile_status: 'configured',
        created_at: '2026-10-02T00:00:00Z',
      },
    ]
    const w = mountPage(RepoPage)
    await flushPromises()

    const manageBtn = w.findAll('.repo__dest')[1].findAll('button').find((b) => b.text() === '管理')
    await manageBtn!.trigger('click')
    await flushPromises()

    expect(w.text()).toContain('无法解析已保存的地址')
    const values = w.findAll('input').map((x) => (x.element as HTMLInputElement).value)
    expect(values).toContain('s3:cn-north-1:mybucket:/backups') // 原文回显可编辑
    expect(values).not.toContain('backups') // Bucket 不错填成路径段
    expect(values).not.toContain('mybucket')
    w.unmount()
  })

  it('编辑态切换类型：地址字段与凭据组跟随下拉所选类型（formType 绑定回归）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    // 管理默认仓库（local，无凭据项）
    const manageBtn = w.findAll('.repo__dest')[0].findAll('button').find((b) => b.text() === '管理')
    await manageBtn!.trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('后端凭据')

    // 经 n-select 的 update:value 切到 s3（happy-dom 点不开下拉浮层，直接派发组件事件）
    await w.findComponent(NSelect).vm.$emit('update:value', 's3')
    await flushPromises()
    expect(w.text()).toContain('Bucket 名称')
    expect(w.text()).toContain('AWS Access Key ID')
    w.unmount()
  })

  it('新建态：不渲染测试区/凭据区/密码状态（无已存仓库可测可配）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    // P6：新建入口 = Duplicati add-new 行动卡（div role=button + 「新增仓库」文案，
    // 非 button 元素——整卡可点形态，契约变更自 2026-10-22 按钮改卡）
    const newCard = w.find('.rw-action-card')
    expect(newCard.text()).toContain('新增仓库')
    await newCard.trigger('click')
    await flushPromises()

    // 类型下拉占位符（未选类型时不渲染地址区）
    expect(w.html()).toContain('请选择存储类型')
    // 未选类型：地址/凭据区不渲染（对齐 Duplicati editBaseUrl=null 分支）
    expect(w.text()).not.toContain('文件路径')
    // 无已存仓库：测试区 / 凭据区 / 密码文件状态一律不渲染
    expect(w.text()).not.toContain('请先测试连接')
    expect(w.text()).not.toContain('后端凭据')
    expect(w.text()).not.toContain('密码文件挂载状态')
    w.unmount()
  })

  it('新建态选 s3：凭据区直接渲染（Secret Access Key 可当场填写，随创建落盘）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    await w.find('.rw-action-card').trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('AWS Secret Access Key') // 未选类型时不渲染

    // 选 s3 → 凭据区当场出现（2026-10-19 用户报告：新建选 S3 连 Secret Access Key 都没有）
    await w.findComponent(NSelect).vm.$emit('update:value', 's3')
    await flushPromises()
    expect(w.text()).toContain('AWS Access Key ID')
    expect(w.text()).toContain('AWS Secret Access Key')
    // s3 专属必填字段：服务器地址（endpoint，进 URL 首段；restic 斜杠方言）
    expect(w.text()).toContain('服务器地址')
    // 已退役的死字段不再出现：URL 区域段（改版前 s3:region:bucket:/path 冒号方言）与
    // AWS_ENDPOINT_URL 凭据（restic 0.19.1 不读该环境变量，minio-go v7.1.0 零引用）
    expect(w.text()).not.toContain('区域（可选）')
    expect(w.text()).not.toContain('AWS 兼容端点')
    // 新建态凭据为纯输入（无「已配置」状态标签——itemOf 读当前仓库命名空间，新建态无意义）
    expect(w.text()).not.toContain('已配置')
    w.unmount()
  })

  it('常用值下拉聚焦即展开：get-show 恒真（naive 默认仅输入后才展开）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    // 新建态选 s3：地址区 Bucket + 服务器地址 + 凭据区 AWS 默认区域 = 3 个 auto-complete
    // （改版前还有 URL 区域段与 AWS_ENDPOINT_URL 两个，已随冒号方言一同退役）
    await w.find('.rw-action-card').trigger('click')
    await flushPromises()
    await w.findComponent(NSelect).vm.$emit('update:value', 's3')
    await flushPromises()

    const autos = w.findAllComponents(NAutoComplete)
    expect(autos.length).toBe(3)
    for (const auto of autos) {
      const getShow = auto.props('getShow') as ((v: string) => boolean) | undefined
      expect(typeof getShow).toBe('function')
      // 空输入（仅聚焦未输入）也必须返回 true——否则点击输入框不弹常用值
      expect(getShow!('')).toBe(true)
      // 建议值列表非空（空列表时 naive 本来就不弹，属预期）
      expect((auto.props('options') as unknown[]).length).toBeGreaterThan(0)
    }
    w.unmount()
  })

  it('新建态选 rclone：预设快捷卡恢复（改版前弹窗填入项），点击填入远端名', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    await w.find('.rw-action-card').trigger('click')
    await flushPromises()
    await w.findComponent(NSelect).vm.$emit('update:value', 'rclone')
    await flushPromises()

    // 四张预设卡（OneDrive / Google Drive / S3 兼容 / 其它）
    expect(w.text()).toContain('OneDrive')
    expect(w.text()).toContain('Google Drive')
    expect(w.text()).toContain('S3 兼容远端')
    await w.findAll('.repo__dest-card').find((c) => c.text() === 'OneDrive')!.trigger('click')
    // 点卡 = 填入远端名字段（URL 仍由字段组译）
    const remoteInput = w.findAll('input').find((i) => (i.element as HTMLInputElement).value === 'onedrive')
    expect(remoteInput).toBeTruthy()
    w.unmount()
  })

  it('按类型字段：管理 b2/sftp 仓库 → 各自字段集渲染且 URL 解析回填', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    // b2:acct:/backup → Bucket=acct、路径=backup
    let manageBtn = w.findAll('.repo__dest')[2].findAll('button').find((b) => b.text() === '管理')
    await manageBtn!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Bucket 名称')
    expect(w.findAll('input').some((i) => (i.element as HTMLInputElement).value === 'acct')).toBe(true)

    // 返回列表 → 管理 sftp:restic@10.0.0.2:/srv/repo → 服务器/用户名回填
    await w.find('.repo__edit-header button').trigger('click')
    await flushPromises()
    manageBtn = w.findAll('.repo__dest')[3].findAll('button').find((b) => b.text() === '管理')
    await manageBtn!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain('服务器')
    expect(w.findAll('input').some((i) => (i.element as HTMLInputElement).value === '10.0.0.2')).toBe(true)
    expect(w.findAll('input').some((i) => (i.element as HTMLInputElement).value === 'restic')).toBe(true)
    w.unmount()
  })

  it('新建态「创建并测试」：先建后测（E1 约束），未初始化结论就地给初始化入口', async () => {
    const { createRepo, testRepo } = await import('@/api/repo')
    // 后端 failure_reason 第三档文案：仓库不存在或未初始化
    vi.mocked(testRepo).mockResolvedValueOnce({
      ok: false,
      latency_ms: 3,
      reason: '仓库不存在或未初始化（请检查 repo.url，或先执行 init）',
      failure_kind: 'not_initialized',
    })
    const w = mountPage(RepoPage)
    await flushPromises()

    await w.find('.rw-action-card').trigger('click')
    await flushPromises()
    await w.findComponent(NSelect).vm.$emit('update:value', 'local')
    await flushPromises()

    // 填必填项：仓库名称 + 本地路径（缺任一 onCreate 会提前 return，测不到后段）
    const nameInput = w
      .findAll('input')
      .find((i) => (i.element as HTMLInputElement).placeholder.includes('仓库名称'))
    await nameInput!.setValue('新仓库')
    const pathInput = w
      .findAll('input')
      .find((i) => (i.element as HTMLInputElement).placeholder.includes('/repo/your-repo'))
    await pathInput!.setValue('/repo/new')

    const btn = w.findAll('.repo__form-footer button').find((b) => b.text() === '创建并测试')
    expect(btn).toBeTruthy()
    await btn!.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(createRepo).toHaveBeenCalled()
    // 测试对象是刚建出的仓库 id（既不是旧当前仓库，也不是空 id）
    expect(testRepo).toHaveBeenCalledWith('repo-new')
    // 留在编辑态：结论区可见，未初始化档位就地给初始化入口（不必回列表找行内按钮）
    expect(w.find('.repo__form-footer').exists()).toBe(true)
    const initBlock = w.find('.repo__test-init')
    expect(initBlock.exists()).toBe(true)
    expect(initBlock.text()).toContain('初始化仓库')
    vi.mocked(testRepo).mockClear()
    vi.mocked(createRepo).mockClear()
    w.unmount()
  })

  it('回归：反复进入/退出新建态不累积底部按钮（应对「每点一次新建多一对按钮」报告）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()

    for (let i = 0; i < 3; i++) {
      // P6：新建入口为 add-new 行动卡（div role=button），非 button 元素
      const newCard = w.find('.rw-action-card')
      expect(newCard.exists()).toBe(true)
      await newCard.trigger('click')
      await flushPromises()
      // 每轮恰好一个底栏、两枚按钮（取消 + 主按钮「创建并测试」）——累积即回归
      expect(w.findAll('.repo__form-footer').length).toBe(1)
      expect(w.findAll('.repo__form-footer button').length).toBe(2)
      await w.find('.repo__edit-header button').trigger('click')
      await flushPromises()
    }
    w.unmount()
  })

  it('编辑态：取消返回列表态（列表恢复渲染）', async () => {
    const w = mountPage(RepoPage)
    await flushPromises()
    const manageBtn = w.findAll('.repo__dest')[1].findAll('button').find((b) => b.text() === '管理')
    await manageBtn!.trigger('click')
    await flushPromises()
    expect(w.text()).not.toContain('仓库维护')

    await w.find('.repo__edit-header button').trigger('click')
    expect(w.text()).toContain('仓库维护')
    w.unmount()
  })
})
