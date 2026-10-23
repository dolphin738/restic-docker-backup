/**
 * StatusTag 契约护栏（UI全量改造方案 §6（原《UI界面设计方案》） §6 / §10 PV-2）。
 *
 * 锁定语义：
 * - 五语义态透传 NTag type：以 `--n-text-color` 内联变量为断言锚点
 *   （NTag 的 type 不产类名，只产 --n-color/--n-text-color；textColor 为全透明度
 *   语义色，正是圆点 currentColor 的取色来源——P3b 的 cancelled / 无 type 场景复用同一组件）；
 * - 圆点 6px 契约类名 + aria-hidden（各页替换点与走查脚本的选择器锚点）；
 * - 文案由调用方传入：组件不内置词条、不做业务映射（i18n 硬编码零新增）。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { NConfigProvider } from 'naive-ui'
import { h } from 'vue'

import StatusTag, { type StatusTagKind } from '@/components/StatusTag.vue'
import { lightThemeOverrides } from '@/theme/tokens'

/** 挂 light 令牌挂载：让 NTag 的 type → 主题令牌取色链真实走一遍（而非默认 naive 色）。 */
function mountTag(kind: StatusTagKind, label = '状态') {
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(StatusTag, { kind, label }) },
  })
}

describe('StatusTag', () => {
  it('五语义态 → 语义色（--n-text-color，light 令牌实链）', () => {
    // 期望值与 theme/tokens.ts lightThemeOverrides 一致；换令牌时本用例同步
    const expected: Array<[StatusTagKind, string]> = [
      ['success', '#15803D'],
      ['error', '#B91C1C'],
      ['warning', '#B45309'],
      ['default', '#475569'], // textColor2
      ['info', '#3D5BD9'],
    ]
    for (const [kind, hex] of expected) {
      const w = mountTag(kind)
      const tag = w.find('.n-tag')
      expect((tag.element as HTMLElement).style.getPropertyValue('--n-text-color')).toBe(hex)
      w.unmount()
    }
  })

  it('圆点 6px 契约：类名 + aria-hidden（颜色走 currentColor，不在组件内落色值）', () => {
    const w = mountTag('success')
    const dot = w.find('.status-tag__dot')
    expect(dot.exists()).toBe(true)
    expect(dot.attributes('aria-hidden')).toBe('true')
    w.unmount()
  })

  it('形态与替换点一致：round + size small（小圆角全圆标签）', () => {
    const w = mountTag('info')
    expect(w.find('.n-tag').classes()).toContain('n-tag--round')
    // NTag 的 size 只产高度/字号变量、不产类名；经 --n-height 变量断言 small 档
    expect((w.find('.n-tag').element as HTMLElement).style.getPropertyValue('--n-height')).toBe('22px')
    w.unmount()
  })

  it('文案原样渲染（不做任何映射/加工）', () => {
    const w = mountTag('warning', '保留清理到期')
    expect(w.find('.n-tag').text()).toContain('保留清理到期')
    w.unmount()
  })
})
