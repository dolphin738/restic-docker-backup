/**
 * 系统设置页「时区」卡片（E39：手动设置 + 服务器时钟 + NTP 时间同步 + 主备 NTP 服务器）行为回归。
 *
 * 覆盖：
 *   ① 当前时区 pill + 服务器实时时钟 + NTP 状态渲染；
 *   ② 时区保存走 E07 patch 通道（键 timezone）；
 *   ③ env 覆盖时下拉禁用并给出指路文案（禁止静默可改）；
 *   ④ 时间同步经确认门调用，结果（ok/reason）必须展示；
 *   ⑤ NTP 主备服务器：输入渲染 + 生效链展示 + 双键同批保存 + env 覆盖禁用。
 *
 * 布局说明（2026-10-21 改版）：设置页为「顶部分段 Tab + 单面板」——非当前分区
 * 不渲染（v-if），各用例须先点分段条切到时区分区再断言。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider, NSelect } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SettingsPage from '@/pages/settings/index.vue'
import { formatDateTime, setDisplayTimezone } from '@/utils/datetime'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  configItems: [] as Array<Record<string, unknown>>,
  savedPatches: [] as Array<Record<string, string | null>>,
  syncCalls: 0,
  systemTime: null as Record<string, unknown> | null,
}))

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(async () => ({ items: mocks.configItems, passfile_status: 'configured' })),
  putConfig: vi.fn(async (patch: Record<string, string | null>) => {
    mocks.savedPatches.push(patch)
    return { ok: true }
  }),
}))

vi.mock('@/api/settings', () => ({
  getCacheUsage: vi.fn(async () => ({ size_bytes: 0, items_count: 0, path: '', measured_at: '', truncated: false })),
  clearCache: vi.fn(async () => ({ ok: true })),
  saveDumpBehaviorConfig: vi.fn(async (patch: Record<string, string | null>) => {
    mocks.savedPatches.push(patch)
    return { ok: true }
  }),
}))

vi.mock('@/api/auth', () => ({ changePassword: vi.fn(async () => ({ ok: true })) }))

vi.mock('@/api/system', () => ({
  getSystemTime: vi.fn(async () => mocks.systemTime),
  syncSystemTime: vi.fn(async () => {
    mocks.syncCalls += 1
    return { ok: true, applied: true, offset_ms: 3200.0, server: 'pool.ntp.org', reason: null }
  }),
}))

const BASE_TIME = {
  server_time: '2026-10-20T09:00:00Z',
  timezone: 'UTC',
  local_time: '2026-10-20T09:00:00+00:00',
  utc_offset: '+00:00',
  epoch_ms: Date.parse('2026-10-20T09:00:00Z'),
  ntp_servers: ['pool.ntp.org', 'ntp.aliyun.com', 'cn.pool.ntp.org'],
  ntp: { available: true, server: 'pool.ntp.org', offset_ms: 3200.0, rtt_ms: 12.0, reason: null },
}

/** NTP 主备表单内的「保存」按钮：以主服务器输入所在 <form> 作用域定位
 * （模板中 NTP 表单先于时区表单，裸找第一个「保存」会错位）。
 *
 * 作用域必须是 wrapper 根元素而非 document：VTU mount 未传 attachTo 时组件挂在
 * 脱离文档的 div 里，document.querySelectorAll 连输入框都搜不到（确认门里的按钮
 * 能全局搜到只是因为 naive 弹窗 teleport 到了 body，不具普遍性）。 */
function ntpSaveButton(w: VueWrapper): HTMLButtonElement | undefined {
  const primary = [...w.element.querySelectorAll('input')].find((el) => el.getAttribute('placeholder') === 'pool.ntp.org')
  const form = primary?.closest('form')
  // P15 图标化后按钮带 #icon 插槽（SVG 无文本但内容节点含换行空白）→ trim 后再比对
  return form
    ? ([...form.querySelectorAll('button')].find((b) => b.textContent?.trim() === '保存') as HTMLButtonElement | undefined)
    : undefined
}

/** 切换到指定分区（2026-10-21 改版：单面板 v-if 渲染，非当前分区不在 DOM）。
 * 点顶部分段条中对应按钮——与真实交互路径一致；按钮文案经 i18n 取，
 * 避免在 spec 里硬编码标题（标题调整时本助手不必跟着改）。 */
async function showSection(w: VueWrapper, titleKey: string): Promise<void> {
  const label = i18n.global.t(titleKey)
  const btn = w.findAll('button').find((b) => b.text() === label)
  expect(btn, `分段条应包含「${label}」按钮`).toBeTruthy()
  await btn!.trigger('click')
  await flushPromises()
}

function mountPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: SettingsPage }],
  })
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(SettingsPage) }) },
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.configItems = []
  mocks.savedPatches = []
  mocks.syncCalls = 0
  mocks.systemTime = { ...BASE_TIME }
})

describe('系统设置页：时区卡片', () => {
  it('渲染当前时区、服务器时钟与 NTP 状态', async () => {
    mocks.configItems = [{ key: 'timezone', value: 'Asia/Shanghai', has_value: true, source: 'db' }]
    mocks.systemTime = { ...BASE_TIME, timezone: 'Asia/Shanghai', utc_offset: '+08:00' }
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')

    const card = w.find('#sec-timezone')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('Asia/Shanghai')
    expect(card.text()).toContain('服务器时间')
    expect(card.text()).toContain('已对时')
    expect(card.text()).toContain('+3200 ms')
    w.unmount()
  })

  it('选择新时区后保存，patch 只含 timezone 键', async () => {
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')
    const card = w.find('#sec-timezone')

    const tzSelect = w
      .findAllComponents(NSelect)
      .find((s) => (s.props('options') as Array<{ value: string }>).some((o) => o.value === 'Asia/Shanghai'))
    expect(tzSelect).toBeTruthy()
    await tzSelect!.vm.$emit('update:value', 'Asia/Tokyo')
    await flushPromises()

    // 卡片内有两个「保存」（NTP 表单在前、时区表单在后）：时区保存取最后一个
    const saveButtons = card.findAll('button').filter((b) => b.text() === '保存')
    await saveButtons[saveButtons.length - 1]?.trigger('click')
    await flushPromises()

    expect(mocks.savedPatches).toHaveLength(1)
    expect(mocks.savedPatches[0]).toEqual({ timezone: 'Asia/Tokyo' })
    w.unmount()
  })

  it('E39 时区统一：保存成功后全站展示时区即时切换（无需刷新页面）', async () => {
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')
    const card = w.find('#sec-timezone')

    // 前置：Asia/Seoul 注入后 UTC 串渲染为 +9 时间（证明注入生效）
    setDisplayTimezone('Asia/Seoul')
    expect(formatDateTime('2026-10-25T07:40:02Z')).toBe('2026-10-25 16:40:02')

    const tzSelect = w
      .findAllComponents(NSelect)
      .find((s) => (s.props('options') as Array<{ value: string }>).some((o) => o.value === 'Asia/Tokyo'))
    await tzSelect!.vm.$emit('update:value', 'Asia/Tokyo')
    await flushPromises()

    // 卡片内有两个「保存」（NTP 表单在前、时区表单在后）：时区保存取最后一个
    const saveButtons = card.findAll('button').filter((b) => b.text() === '保存')
    await saveButtons[saveButtons.length - 1]?.trigger('click')
    await flushPromises()

    expect(mocks.savedPatches[0]).toEqual({ timezone: 'Asia/Tokyo' })
    // 保存成功即把展示时区切成 Tokyo（+9）。Tokyo 与 Seoul 同偏移，故用跨日串断言
    // 偏移量本身：23:30Z → 次日 08:30；若仍是浏览器本地（测试机 +8）则为 07:30。
    expect(formatDateTime('2026-10-25T23:30:00Z')).toBe('2026-10-26 08:30:00')
    setDisplayTimezone(null)
    w.unmount()
  })

  it('时区由 env 覆盖时下拉禁用并提示改环境变量', async () => {
    mocks.configItems = [{ key: 'timezone', value: 'Asia/Shanghai', has_value: true, source: 'env' }]
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')

    const card = w.find('#sec-timezone')
    expect(card.text()).toContain('由环境变量控制')
    const tzSelect = w
      .findAllComponents(NSelect)
      .find((s) => (s.props('options') as Array<{ value: string }>).some((o) => o.value === 'Asia/Shanghai'))
    expect(tzSelect?.props('disabled')).toBe(true)
    w.unmount()
  })

  it('NTP 不可达时展示原因（禁止静默）', async () => {
    mocks.systemTime = {
      ...BASE_TIME,
      ntp: { available: false, server: null, offset_ms: null, rtt_ms: null, reason: 'ntp.unreachable' },
    }
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')
    const card = w.find('#sec-timezone')
    expect(card.text()).toContain('NTP 不可达')
    expect(card.text()).toContain('NTP 服务器不可达')
    w.unmount()
  })

  it('时间同步经确认门执行并展示结果', async () => {
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')
    const card = w.find('#sec-timezone')

    await card.findAll('button').find((b) => b.text() === '立即同步时间')?.trigger('click')
    await flushPromises()
    const bodyButtons = () => [...document.querySelectorAll('button')]
    await bodyButtons().find((b) => b.textContent === '下一步')?.click()
    await flushPromises()
    await bodyButtons().find((b) => b.textContent === '立即同步时间')?.click()
    await flushPromises()

    expect(mocks.syncCalls).toBe(1)
    expect(card.text()).toContain('同步完成')
    expect(card.text()).toContain('+3200 ms')
    w.unmount()
  })

  // ---------------------------------------------------------- NTP 主备服务器（E39）

  it('渲染主备输入框与服务端生效探测链', async () => {
    mocks.systemTime = { ...BASE_TIME, ntp_servers: ['ntp.example.com', 'ntp2.example.com:123'] }
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')

    const card = w.find('#sec-timezone')
    expect(card.text()).toContain('NTP 对时服务器')
    expect(card.text()).toContain('主 NTP 服务器')
    expect(card.text()).toContain('备用 NTP 服务器')
    expect(card.text()).toContain('生效探测链：ntp.example.com → ntp2.example.com:123')
    // 生效链来自服务端响应（未配置时即内置默认链），不是前端硬编码
    expect(card.text()).not.toContain('pool.ntp.org → ntp.aliyun.com → cn.pool.ntp.org')
    w.unmount()
  })

  it('保存 NTP 主备：双键同批 patch（空串=清除回落默认链）', async () => {
    mocks.configItems = [{ key: 'time_sync.ntp_primary', value: 'ntp.example.com', has_value: true, source: 'db' }]
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')

    const primary = w.findAll('input').find((i) => i.attributes('placeholder') === 'pool.ntp.org')
    const backup = w.findAll('input').find((i) => i.attributes('placeholder') === 'ntp.aliyun.com')
    expect(primary).toBeTruthy()
    expect(backup).toBeTruthy()
    // 已存主地址回填输入框
    expect((primary!.element as HTMLInputElement).value).toBe('ntp.example.com')

    await primary!.setValue('ntp.new.example.com')
    await backup!.setValue('ntp2.example.com:123')
    await flushPromises()

    ntpSaveButton(w)?.click()
    await flushPromises()

    expect(mocks.savedPatches).toEqual([
      { 'time_sync.ntp_primary': 'ntp.new.example.com', 'time_sync.ntp_backup': 'ntp2.example.com:123' },
    ])
    w.unmount()
  })

  it('NTP 键由 env 覆盖时输入禁用并提示改环境变量', async () => {
    mocks.configItems = [
      { key: 'time_sync.ntp_primary', value: 'ntp.example.com', has_value: true, source: 'env' },
      { key: 'time_sync.ntp_backup', value: 'ntp2.example.com', has_value: true, source: 'env' },
    ]
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.timezone.title')

    const card = w.find('#sec-timezone')
    const primary = w.findAll('input').find((i) => i.attributes('placeholder') === 'pool.ntp.org')
    expect((primary!.element as HTMLInputElement).disabled).toBe(true)
    const save = ntpSaveButton(w)
    expect(save?.disabled).toBe(true)
    expect(card.text()).toContain('由环境变量控制')
    w.unmount()
  })
})

describe('系统设置页：系统域（log.level / db.busy_timeout_ms）', () => {
  it('渲染当前日志级别与锁等待默认值', async () => {
    mocks.configItems = [
      { key: 'log.level', value: 'INFO', has_value: true, source: 'db' },
      { key: 'db.busy_timeout_ms', value: '5000', has_value: true, source: 'db' },
    ]
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.system.title')

    const card = w.find('#sec-system')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('日志级别')
    expect(card.text()).toContain('INFO')
    expect(card.text()).toContain('SQLite 锁等待')
    w.unmount()
  })

  it('改日志级别并保存，patch 含两个键', async () => {
    mocks.configItems = [
      { key: 'log.level', value: 'INFO', has_value: true, source: 'db' },
      { key: 'db.busy_timeout_ms', value: '5000', has_value: true, source: 'db' },
    ]
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.system.title')
    const card = w.find('#sec-system')

    const levelSelect = w
      .findAllComponents(NSelect)
      .find((s) => (s.props('options') as Array<{ value: string }>).some((o) => o.value === 'DEBUG'))
    expect(levelSelect).toBeTruthy()
    await levelSelect!.vm.$emit('update:value', 'DEBUG')
    await flushPromises()

    const saveButtons = card.findAll('button').filter((b) => b.text().trim() === '保存')
    await saveButtons[saveButtons.length - 1]?.trigger('click')
    await flushPromises()

    expect(mocks.savedPatches).toHaveLength(1)
    expect(mocks.savedPatches[0]).toEqual({ 'log.level': 'DEBUG', 'db.busy_timeout_ms': '5000' })
    w.unmount()
  })

  it('键由 env 覆盖时输入禁用并提示改 compose', async () => {
    mocks.configItems = [
      { key: 'log.level', value: 'WARNING', has_value: true, source: 'env' },
      { key: 'db.busy_timeout_ms', value: '3000', has_value: true, source: 'env' },
    ]
    const w = mountPage()
    await flushPromises()
    await showSection(w, 'settings.system.title')

    const card = w.find('#sec-system')
    expect(card.text()).toContain('由 compose 环境变量覆盖')
    // P15 图标化后按钮 text() 带空白（#icon 插槽的换行节点）→ trim 后再比对
    const saveButtons = card.findAll('button').filter((b) => b.text().trim() === '保存')
    expect(saveButtons.length).toBeGreaterThan(0)
    expect(saveButtons[saveButtons.length - 1]?.attributes('disabled')).toBeDefined()
    w.unmount()
  })
})
