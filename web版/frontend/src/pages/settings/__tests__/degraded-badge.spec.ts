/**
 * 凭据「失效」三态渲染护栏（第 5 批，方案 §8.4 / §9.4）。
 *
 * 锁定语义：密钥源失效的键必须显示「失效」（warning 色）+ hover 原因，**不得**显示
 * 「未配置」——后者会让用户去重录凭据，而真正的问题是口令文件。
 * 三态判定优先级：degraded → has_value → 未配置。
 *
 * 两个分区（dump 密码 / metrics 令牌）都通读 ConfigItem.degraded，此处分别挂载断言。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { h } from 'vue'
import { createI18n } from 'vue-i18n'

import SectionDump from '@/pages/settings/sections/SectionDump.vue'
import SectionMetrics from '@/pages/settings/sections/SectionMetrics.vue'
import type { ConfigItem, ConfigResponse } from '@/api/types'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'
import * as configApi from '@/api/config'

/** 构造一条 ConfigItem（三态：degraded / set / unset 由入参决定）。 */
function item(key: string, opts: { set?: boolean; degraded?: boolean; reason?: string | null } = {}): ConfigItem {
  return {
    key,
    value: null,
    has_value: opts.set ?? false,
    source: 'ui',
    sensitive: true,
    degraded: opts.degraded ?? false,
    degraded_reason: opts.reason ?? null,
  }
}

function stubConfig(items: ConfigItem[]): void {
  const body: ConfigResponse = { items, passfile_status: 'configured' }
  vi.spyOn(configApi, 'getConfig').mockResolvedValue(body)
}

function mountSection(component: unknown) {
  const i18n = createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN, 'en-US': enUS } })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return mount(NConfigProvider, {
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }]] },
    slots: { default: () => h(NMessageProvider, null, { default: () => h(component as never) }) },
  })
}

/** 徽标文案（StatusTag 渲染的 n-tag）——只取标签文本，避免整页文案干扰。 */
function badgeTexts(w: ReturnType<typeof mountSection>): string[] {
  return [...w.element.querySelectorAll('.status-tag')].map((el) => el.textContent?.trim() ?? '')
}

describe('凭据「失效」三态（degraded 渲染护栏）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('dump 口令：degraded 时显示「失效」而不是「未配置」', async () => {
    stubConfig([
      item('dump.password', { degraded: true, reason: '密钥源 default 的口令文件缺失或不可读，该键无法解密' }),
    ])
    const w = mountSection(SectionDump)
    await flushPromises()
    const texts = badgeTexts(w)
    expect(texts).toContain(zhCN.settings.dump.passwordDegraded)
    expect(texts).not.toContain(zhCN.settings.dump.passwordUnset)
    w.unmount()
  })

  it('dump 口令：未失效且未配置 → 「未配置」；已配置 → 「已配置」', async () => {
    stubConfig([item('dump.password', { set: false })])
    let w = mountSection(SectionDump)
    await flushPromises()
    expect(badgeTexts(w)).toContain(zhCN.settings.dump.passwordUnset)
    w.unmount()

    stubConfig([item('dump.password', { set: true })])
    w = mountSection(SectionDump)
    await flushPromises()
    expect(badgeTexts(w)).toContain(zhCN.settings.dump.passwordSet)
    w.unmount()
  })

  it('metrics 令牌：degraded 时显示「失效」而不是「未设置」', async () => {
    stubConfig([item('metrics.token', { degraded: true, reason: '密钥源 default 的口令文件与密文不匹配，该键无法解密' })])
    const w = mountSection(SectionMetrics)
    await flushPromises()
    const texts = badgeTexts(w)
    expect(texts).toContain(zhCN.settings.metrics.tokenDegraded)
    expect(texts).not.toContain(zhCN.settings.metrics.tokenUnset)
    w.unmount()
  })

  it('degraded 优先于 has_value：即使 has_value 意外为 true 也不显示「已设置」', async () => {
    stubConfig([item('metrics.token', { set: true, degraded: true, reason: '原因' })])
    const w = mountSection(SectionMetrics)
    await flushPromises()
    const texts = badgeTexts(w)
    expect(texts).toContain(zhCN.settings.metrics.tokenDegraded)
    expect(texts).not.toContain(zhCN.settings.metrics.tokenSet)
    w.unmount()
  })

  it('中英文案键齐备：三个新键在 en-US 与 zh-CN 同名存在且非空', () => {
    expect(zhCN.repo.credDegraded).toBeTruthy()
    expect(enUS.repo.credDegraded).toBeTruthy()
    expect(zhCN.settings.dump.passwordDegraded).toBeTruthy()
    expect(enUS.settings.dump.passwordDegraded).toBeTruthy()
    expect(zhCN.settings.metrics.tokenDegraded).toBeTruthy()
    expect(enUS.settings.metrics.tokenDegraded).toBeTruthy()
  })
})
