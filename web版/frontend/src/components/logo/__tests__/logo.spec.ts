/**
 * 护栏：品牌 Logo 内联 SVG 组件（LogoMark / LogoHorizontal）。
 *
 * 盯三件容易静默漂移的事：
 * ① 形状完整性：内联组件必须 1:1 复刻 public/logo.svg 与 public/logo-horizontal.svg 的
 *    图形元素（矩形 + 路径数量、关键 path 的 d 值、横向组合的文字与 transform），
 *    否则显示效果与原图不一致；
 * ② 无障碍：role="img" + aria-label 跟随 alt prop，<title> 同步；
 * ③ 导出：从 @/components/logo 可取到两个组件。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { LogoHorizontal, LogoMark } from '../index'

const SHIELD_PATH =
  'M120 34 L188 60 V122 C188 166 160 196 120 214 C80 196 52 166 52 122 V60 Z'

describe('LogoMark（方形徽标）', () => {
  it('复刻 public/logo.svg 的图形结构', () => {
    const wrapper = mount(LogoMark)
    const svg = wrapper.find('svg')
    expect(svg.exists()).toBe(true)
    // 原图 viewBox / 尺寸
    expect(svg.attributes('viewBox')).toBe('0 0 240 240')
    expect(svg.attributes('width')).toBe('240')
    expect(svg.attributes('height')).toBe('240')
    // 1 矩形 + 12 路径，与原文件一致
    expect(wrapper.findAll('rect')).toHaveLength(1)
    expect(wrapper.findAll('path')).toHaveLength(12)
    // 关键护盾描边路径存在
    const ds = wrapper.findAll('path').map((p) => p.attributes('d'))
    expect(ds).toContain(SHIELD_PATH)
  })

  it('无障碍属性跟随 alt prop', () => {
    const wrapper = mount(LogoMark, { props: { alt: '备份控制台' } })
    const svg = wrapper.find('svg')
    expect(svg.attributes('role')).toBe('img')
    expect(svg.attributes('aria-label')).toBe('备份控制台')
    expect(wrapper.find('title').text()).toBe('备份控制台')
  })

  it('默认 alt 为 Restic Web', () => {
    const wrapper = mount(LogoMark)
    expect(wrapper.find('svg').attributes('aria-label')).toBe('Restic Web')
  })
})

describe('LogoHorizontal（横向组合）', () => {
  it('复刻 public/logo-horizontal.svg 的图形结构', () => {
    const wrapper = mount(LogoHorizontal)
    const svg = wrapper.find('svg')
    expect(svg.exists()).toBe(true)
    expect(svg.attributes('viewBox')).toBe('0 0 520 128')
    expect(svg.attributes('width')).toBe('520')
    expect(svg.attributes('height')).toBe('128')
    // 徽标组：缩放 + 平移
    const g = wrapper.find('g')
    expect(g.exists()).toBe(true)
    expect(g.attributes('transform')).toContain('scale(0.4)')
    expect(g.attributes('transform')).toContain('translate(16,16)')
    // 组内复刻方形徽标（1 矩形 + 12 路径）
    expect(g.findAll('rect')).toHaveLength(1)
    expect(g.findAll('path')).toHaveLength(12)
    const ds = g.findAll('path').map((p) => p.attributes('d'))
    expect(ds).toContain(SHIELD_PATH)
    // 文字标记
    const text = wrapper.find('text')
    expect(text.exists()).toBe(true)
    expect(text.text()).toBe('Restic Web')
    expect(text.attributes('fill')).toBe('currentColor')
  })

  it('无障碍属性跟随 alt prop', () => {
    const wrapper = mount(LogoHorizontal, { props: { alt: 'Restic Web 控制台' } })
    const svg = wrapper.find('svg')
    expect(svg.attributes('role')).toBe('img')
    expect(svg.attributes('aria-label')).toBe('Restic Web 控制台')
  })
})

describe('统一导出', () => {
  it('从 @/components/logo 导出两个组件', () => {
    expect(LogoMark).toBeDefined()
    expect(LogoHorizontal).toBeDefined()
  })
})
