/**
 * TaskRowCard 行卡片护栏（方案 §4.5.6 / §4.7 P6；形态来源 Duplicati home list 备份行）。
 *
 * 锁定语义：
 * - 形态：role=button + tabindex=0 + Enter/Space（避免 button 嵌 button 的非法 HTML——
 *   操作区由消费方经 #actions 插槽塞入，行内必有按钮）；
 * - 整行点击 = select；选中行高亮（--active）且点击空转（已是选中态）；
 * - 禁用态：失焦 + aria-disabled + 点击/键盘静默；
 * - 类型 / 快照数 chip 与副描述可缺省。
 *
 * 查询纪律：组件根即 .rw-row-card（div 根），原生 querySelector 不含自身，
 * 根断言走 w.classes()/w.attributes()/w.trigger()；后代（chip/插槽）走 w.element。
 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { h } from 'vue'

import TaskRowCard from '@/components/dashboard/TaskRowCard.vue'
import { IconFolder } from '@/components/icons/HomeIcons'

function mountRow(props: Partial<InstanceType<typeof TaskRowCard>['$props']> = {}) {
  return mount(TaskRowCard, {
    props: { name: '每日备份', typeLabel: '本地', snapshotLabel: '12 个快照', description: '/data · host-01', ...props },
  })
}

describe('TaskRowCard 行卡片', () => {
  it('名称 / 类型 chip / 快照 chip / 描述渲染', () => {
    const w = mountRow()
    expect(w.text()).toContain('每日备份')
    expect(w.text()).toContain('本地')
    expect(w.text()).toContain('12 个快照')
    expect(w.text()).toContain('/data · host-01')
    const chips = w.element.querySelectorAll('.rw-chip')
    expect(chips).toHaveLength(2)
    expect(chips[1].classList.contains('rw-chip--muted')).toBe(true)
    w.unmount()
  })

  it('整行点击 = select（role=button + tabindex=0）', async () => {
    const w = mountRow()
    expect(w.classes()).toContain('rw-row-card')
    expect(w.attributes('role')).toBe('button')
    expect(w.attributes('tabindex')).toBe('0')
    await w.trigger('click')
    expect(w.emitted('select')).toHaveLength(1)
    w.unmount()
  })

  it('Enter / Space = select', async () => {
    const w = mountRow()
    await w.trigger('keydown.enter')
    await w.trigger('keydown.space')
    expect(w.emitted('select')).toHaveLength(2)
    w.unmount()
  })

  it('选中行：--active + aria-current + 点击空转', async () => {
    const w = mountRow({ active: true })
    expect(w.classes()).toContain('rw-row-card--active')
    expect(w.attributes('aria-current')).toBe('true')
    await w.trigger('click')
    await w.trigger('keydown.enter')
    expect(w.emitted('select')).toBeUndefined()
    w.unmount()
  })

  it('禁用态：失焦 + aria-disabled + 静默', async () => {
    const w = mountRow({ disabled: true })
    expect(w.classes()).toContain('rw-row-card--disabled')
    expect(w.attributes('tabindex')).toBe('-1')
    expect(w.attributes('aria-disabled')).toBe('true')
    await w.trigger('click')
    expect(w.emitted('select')).toBeUndefined()
    w.unmount()
  })

  it('#actions 插槽渲染（操作区由消费方塞入）', () => {
    const w = mount(TaskRowCard, {
      props: { name: '行' },
      slots: { actions: () => h('button', { class: 'row-op' }, '删除') },
    })
    expect(w.element.querySelector('.rw-row-card__actions .row-op')?.textContent).toBe('删除')
    // 缺省 chip / 描述 / 图标不渲染
    expect(w.element.querySelector('.rw-chip')).toBeNull()
    expect(w.element.querySelector('.rw-row-card__desc')).toBeNull()
    expect(w.element.querySelector('.rw-row-card__icon')).toBeNull()
    w.unmount()
  })

  it('图标渲染（BackendIcons 成员）', () => {
    const w = mountRow({ icon: IconFolder })
    expect(w.element.querySelector('.rw-row-card__icon')).not.toBeNull()
    w.unmount()
  })
})
