/**
 * 通知设置页（A 方案 2026-10-20 重构）行为回归。
 *
 * 覆盖三条主链路（真挂载 + API 全 mock）：
 *   ① 渠道类型下拉切换 → 只渲染该类型的配置项（仓库页「先选类型」范式同构）；
 *   ② 已保存渠道列表按状态渲染（状态 pill + 脱敏摘要，URL/密码绝不出现在摘要里）；
 *   ③ 保存只提交该渠道的键（E23 三态语义的 UI 侧投影）；
 *   ④ 清除走确认门，patch 逐键置空（不可撤销操作有门）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider, NSelect } from 'naive-ui'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import NotificationsPage from '@/pages/notifications/index.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

/** 可变 mock 数据 + 调用记录（vi.hoisted：工厂 hoist 后仍可被测试改写）。 */
const mocks = vi.hoisted(() => ({
  notify: null as Record<string, unknown> | null,
  patches: [] as Array<Record<string, unknown>>,
}))

vi.mock('@/api/notify', () => ({
  getNotifyConfig: vi.fn(async () => mocks.notify),
  updateNotifyConfig: vi.fn(async (patch: Record<string, unknown>) => {
    mocks.patches.push(patch)
    return { ok: true }
  }),
  testNotify: vi.fn(async () => ({ ok: true, latency_ms: 8, channels: [], reason: null })),
}))

const BASE_CONFIG = {
  healthchecks_url_set: false,
  webhook_url_set: false,
  webhook_template: '',
  meow_nickname: '',
  on_success: false,
  skipped_threshold: 100,
  webhook_redact: false,
  meow_redact: true,
  mail_host: '',
  mail_port: 587,
  mail_username: '',
  mail_password_set: false,
  mail_from: '',
  mail_to: '',
  mail_security: 'starttls',
  mail_redact: false,
}

function mountPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: NotificationsPage }],
  })
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(NotificationsPage) }) },
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }], router] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.notify = { ...BASE_CONFIG }
  mocks.patches = []
})

describe('通知设置页：渠道类型下拉', () => {
  it('未选类型时不渲染任何渠道字段', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.find('input[placeholder="https://hc-ping.com/<uuid>"]').exists()).toBe(false)
    expect(w.find('textarea').exists()).toBe(false)
    w.unmount()
  })

  it('选 Webhook 只出 Webhook 字段（不出 Healthchecks / MeoW / 邮件字段）', async () => {
    const w = mountPage()
    await flushPromises()
    await w.findComponent(NSelect).vm.$emit('update:value', 'webhook')
    await flushPromises()

    expect(w.find('input[placeholder="自建 webhook 完整 URL"]').exists()).toBe(true)
    expect(w.find('textarea[placeholder*="占位"]').exists()).toBe(true)
    // 其它渠道字段不得出现
    expect(w.find('input[placeholder="https://hc-ping.com/<uuid>"]').exists()).toBe(false)
    expect(w.find('input[placeholder="MeoW App 中的设备昵称"]').exists()).toBe(false)
    expect(w.find('input[placeholder="如 smtp.example.com"]').exists()).toBe(false)
    w.unmount()
  })

  it('选 SMTP 邮件出邮件字段组', async () => {
    const w = mountPage()
    await flushPromises()
    await w.findComponent(NSelect).vm.$emit('update:value', 'mail')
    await flushPromises()
    expect(w.find('input[placeholder="如 smtp.example.com"]').exists()).toBe(true)
    expect(w.find('input[placeholder="登录账号（通常是邮箱地址）"]').exists()).toBe(true)
    w.unmount()
  })
})

describe('通知设置页：已保存渠道列表', () => {
  it('按 *_set / 昵称渲染状态 pill 与摘要（敏感值不回显）', async () => {
    mocks.notify = {
      ...BASE_CONFIG,
      healthchecks_url_set: true,
      webhook_url_set: true,
      webhook_template: '',
      meow_nickname: 'nas',
      mail_host: 'smtp.example.com',
      mail_from: 'ops@example.com',
      mail_password_set: true,
    }
    const w = mountPage()
    await flushPromises()

    const text = w.text()
    expect(text).toContain('Healthchecks（心跳监控）')
    expect(text).toContain('已配置')
    // 摘要脱敏：绝不出现真实地址原文；昵称/主机属非敏感摘要，允许展示
    expect(text).toContain('设备昵称：nas')
    expect(text).toContain('smtp.example.com ← ops@example.com')
    expect(text).toContain('默认模板')
    expect(w.find('input[placeholder="https://hc-ping.com/<uuid>"]').exists()).toBe(false)
    w.unmount()
  })

  it('MeoW 未配置时该行显示未配置 pill', async () => {
    mocks.notify = { ...BASE_CONFIG, healthchecks_url_set: true }
    const w = mountPage()
    await flushPromises()
    const rows = w.text()
    expect(rows).toContain('未配置')
    expect(rows).toContain('已配置')
    w.unmount()
  })

  it('全未配置时给出警示且测试按钮禁用', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.text()).toContain('尚未配置任何通知渠道')
    const testBtn = w.findAll('button').find((b) => b.text() === '发送测试消息')
    expect(testBtn?.attributes('disabled')).toBeDefined()
    w.unmount()
  })
})

describe('通知设置页：保存与清除', () => {
  it('保存 Webhook 只提交 Webhook 相关键（不夹带其它渠道）', async () => {
    const w = mountPage()
    await flushPromises()
    await w.findComponent(NSelect).vm.$emit('update:value', 'webhook')
    await flushPromises()

    const urlInput = w.find('input[placeholder="自建 webhook 完整 URL"]')
    await urlInput.setValue('https://hook.example/a')
    await w.findAll('button').find((b) => b.text() === '保存')?.trigger('click')
    await flushPromises()

    expect(mocks.patches).toHaveLength(1)
    const patch = mocks.patches[0]
    expect(patch).toMatchObject({ webhook_url: 'https://hook.example/a' })
    expect(patch).not.toHaveProperty('healthchecks_url')
    expect(patch).not.toHaveProperty('meow_nickname')
    expect(patch).not.toHaveProperty('mail_host')
    w.unmount()
  })

  it('清除渠道经确认门后逐键置空', async () => {
    mocks.notify = { ...BASE_CONFIG, healthchecks_url_set: true }
    const w = mountPage()
    await flushPromises()

    const clearBtn = w.findAll('button').find((b) => b.text() === '清除')
    await clearBtn?.trigger('click')
    await flushPromises()
    // 确认门经 NModal teleport 到 body：按钮查询须走 document，不走 wrapper 树
    const bodyButtons = () => [...document.querySelectorAll('button')]
    // 第一步：预览 → 下一步
    await bodyButtons().find((b) => b.textContent === '下一步')?.click()
    await flushPromises()
    // 第三步：确认执行（confirmLabel 覆盖为「清除」）
    await bodyButtons().find((b) => b.textContent === '清除')?.click()
    await flushPromises()

    expect(mocks.patches).toHaveLength(1)
    expect(mocks.patches[0]).toEqual({ healthchecks_url: '' })
    w.unmount()
  })
})
