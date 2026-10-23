/**
 * ActionCard 行动卡护栏（方案 §4.5.6 / §4.7 P6；形态来源 Duplicati destinations add-new 卡）。
 *
 * 锁定语义：
 * - 形态：NCard + role=button + tabindex=0 + Enter/Space（整卡可点）；
 * - 整卡点击与右按钮点击同一动作（@click.stop 防双触发），不引入第二语义；
 * - 禁用态：整卡失焦（tabindex=-1）、aria-disabled、点击/键盘/按钮全部静默；
 * - 图标 / 描述可缺省（缺省不渲染对应节点）。
 *
 * 查询纪律：组件根即 .rw-action-card（NCard 根），原生 querySelector 不含自身，
 * 根断言走 w.classes()/w.attributes()/w.trigger()；后代（按钮/图标）走 w.element。
 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ActionCard from '@/components/dashboard/ActionCard.vue'
import { IconPlusCircle } from '@/components/icons/icons'

function mountCard(props: Partial<InstanceType<typeof ActionCard>['$props']> = {}) {
  return mount(ActionCard, {
    props: { title: '新建仓库', description: '配置一个新的存储位置。', ...props },
  })
}

describe('ActionCard 行动卡', () => {
  it('标题 / 描述 / 图标渲染', () => {
    const w = mountCard({ icon: IconPlusCircle })
    expect(w.text()).toContain('新建仓库')
    expect(w.text()).toContain('配置一个新的存储位置。')
    expect(w.element.querySelector('.rw-action-card__icon')).not.toBeNull()
    w.unmount()
  })

  it('缺省图标 / 描述时不渲染对应节点（右按钮仍可缺省）', () => {
    const w = mountCard({ description: undefined })
    expect(w.element.querySelector('.rw-action-card__icon')).toBeNull()
    expect(w.element.querySelector('.rw-action-card__desc')).toBeNull()
    expect(w.element.querySelector('.rw-action-card__action')).toBeNull()
    w.unmount()
  })

  it('整卡点击 = click 主动作（role=button + tabindex=0）', async () => {
    const w = mountCard()
    expect(w.classes()).toContain('rw-action-card')
    expect(w.attributes('role')).toBe('button')
    expect(w.attributes('tabindex')).toBe('0')
    await w.trigger('click')
    expect(w.emitted('click')).toHaveLength(1)
    w.unmount()
  })

  it('Enter / Space 键盘 = click 主动作', async () => {
    const w = mountCard()
    await w.trigger('keydown.enter')
    expect(w.emitted('click')).toHaveLength(1)
    await w.trigger('keydown.space')
    expect(w.emitted('click')).toHaveLength(2)
    w.unmount()
  })

  it('右按钮同一动作且不双触发（@click.stop）', async () => {
    const w = mountCard({ actionLabel: '新建' })
    const btn = w.element.querySelector('.rw-action-card__action') as HTMLElement
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(w.emitted('click')).toHaveLength(1)
    w.unmount()
  })

  it('禁用态：失焦 + aria-disabled + 点击/键盘/按钮全静默', async () => {
    const w = mountCard({ actionLabel: '新建', disabled: true })
    expect(w.classes()).toContain('rw-action-card--disabled')
    expect(w.attributes('tabindex')).toBe('-1')
    expect(w.attributes('aria-disabled')).toBe('true')

    await w.trigger('click')
    await w.trigger('keydown.enter')
    ;(w.element.querySelector('.rw-action-card__action') as HTMLElement).dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    )
    expect(w.emitted('click')).toBeUndefined()
    // 按钮自身 disabled（naive 按钮渲染原生属性）
    expect((w.element.querySelector('.rw-action-card__action') as HTMLButtonElement).disabled).toBe(true)
    w.unmount()
  })
})
