/** WizardStepper 护栏（P20 全站统一步骤条）。
 *
 * 覆盖三类契约：
 *   ① 序号徽标按**数组顺序**连续编号（ConfirmGate 无关键词时 key 是 1/3，序号仍须 1/2）；
 *   ② 三态（current / done / inactive）与 V-2 跳步校验（鼠标键盘双向不可达）；
 *   ③ 根元素是 <nav> 且 attribute 透传生效（任务向导的 tabindex / @keydown 靠它落到 nav）。
 *
 * 对比度本身由 `.e2e/p20-stepper-contrast.cjs` 真机量（jsdom 不跑 CSS 布局与主题变量），
 * 令牌侧由 `theme/__tests__/tokens.spec.ts` 的「P20 步骤条对比度令牌」机械断言守住。
 */

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import WizardStepper from '@/components/WizardStepper.vue'

function mountStepper(
  steps: Array<{ key: number; title: string; description?: string }>,
  current: number,
  enabled?: Record<number, boolean>,
  attrs: Record<string, unknown> = {},
) {
  return mount(WizardStepper, {
    props: { steps, current, enabled, ariaLabel: '向导步骤' },
    attrs,
  })
}

describe('WizardStepper 序号与文案', () => {
  it('① 序号按数组顺序连续编号（key 跳号不影响序号）', () => {
    const w = mountStepper(
      [
        { key: 1, title: '预览' },
        { key: 3, title: '二次确认' },
      ],
      1,
    )
    const idx = w.findAll('.wizard-stepper__index').map((el) => el.text())
    expect(idx).toEqual(['1', '2'])
  })

  it('② description 有则渲染、无则不渲染（恢复向导不出现空节点）', () => {
    const withDesc = mountStepper([{ key: 1, title: '保存密码', description: '仅此一次展示' }], 1)
    expect(withDesc.find('.wizard-stepper__desc').text()).toBe('仅此一次展示')

    const noDesc = mountStepper([{ key: 1, title: '选择文件' }], 1)
    expect(noDesc.find('.wizard-stepper__desc').exists()).toBe(false)
  })

  it('③ 根元素是 nav 且 aria-label / tabindex 透传落到它身上', () => {
    const w = mountStepper([{ key: 1, title: 'A' }], 1, undefined, { tabindex: '0' })
    expect(w.element.tagName).toBe('NAV')
    expect(w.attributes('aria-label')).toBe('向导步骤')
    expect(w.attributes('tabindex')).toBe('0')
  })

  it('④ @keydown 透传到根元素（任务向导数字键跳步依赖此）', async () => {
    const onKeydown = vi.fn()
    const w = mountStepper([{ key: 1, title: 'A' }], 1, undefined, { onKeydown })
    await w.trigger('keydown', { key: 'ArrowRight' })
    expect(onKeydown).toHaveBeenCalledTimes(1)
  })
})

describe('WizardStepper 三态与跳步校验（V-2）', () => {
  const STEPS = [
    { key: 1, title: '选择文件' },
    { key: 2, title: '恢复选项' },
    { key: 3, title: '执行与结果' },
  ]

  it('⑤ 当前步 aria-current=step，已过步 is-done，未到步两者皆无', () => {
    const w = mountStepper(STEPS, 2)
    const btns = w.findAll('.wizard-stepper__btn')
    expect(btns[1].attributes('aria-current')).toBe('step')
    expect(btns[0].classes()).toContain('is-done')
    expect(btns[2].classes()).not.toContain('is-done')
  })

  it('⑥ inactive 步骤鼠标与键盘双向不可达（点击不 emit）', async () => {
    const w = mountStepper(STEPS, 1, { 1: true, 2: false, 3: false })
    const btns = w.findAll('.wizard-stepper__btn')
    expect(btns[1].classes()).toContain('is-inactive')
    expect(btns[1].attributes('tabindex')).toBe('-1')
    expect(btns[1].attributes('aria-disabled')).toBe('true')
    await btns[1].trigger('click')
    expect(w.emitted('select')).toBeUndefined()
  })

  it('⑦ 可跳步骤点击即 emit select（key 原样，非序号）', async () => {
    const w = mountStepper(STEPS, 1, { 1: true, 2: true, 3: false })
    await w.findAll('.wizard-stepper__btn')[1].trigger('click')
    expect(w.emitted('select')?.[0]).toEqual([2])
  })

  it('⑧ enabled 缺省 = 全可跳（任务向导/首启引导无跳步门禁）', async () => {
    const w = mountStepper(STEPS, 1)
    expect(w.findAll('.wizard-stepper__btn').every((b) => !b.classes().includes('is-inactive'))).toBe(true)
    await w.findAll('.wizard-stepper__btn')[2].trigger('click')
    expect(w.emitted('select')?.[0]).toEqual([3])
  })
})
