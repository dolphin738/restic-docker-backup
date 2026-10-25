/**
 * 恢复选项折叠卡（P16 / UI全量改造方案 §4.9）行为护栏——复刻 Duplicati app-toggle-card。
 *
 * 存在理由：折叠卡是第二步三张分区的容器，最容易静默坏掉的是四件事——
 * ① disableToggle 恒展开却仍渲染可点击 header（假控件）；
 * ② caret 不随展开态旋转（.is-open 类丢失）；
 * ③ 收起态内容仍在视觉流里（max-height 过渡类没挂上）；
 * ④ 展开态 v-model 不回写（父级状态与 UI 漂移）。
 * vue-tsc 查不到这四类语义，故在此锁死。
 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { h } from 'vue'

import RestoreOptionCard from '@/components/restore/RestoreOptionCard.vue'
import { i18n } from '@/i18n'

function mountCard(props: { title?: string; disableToggle?: boolean; open?: boolean } = {}) {
  return mount(RestoreOptionCard, {
    props: { title: '恢复到哪', ...props },
    slots: { default: () => h('div', { class: 'card-body-slot' }, '内容') },
    global: { plugins: [i18n] },
  })
}

describe('RestoreOptionCard：折叠卡（Duplicati toggle-card 复刻）', () => {
  it('① 可折叠态：header 是 button 且 aria-expanded 反映展开态', () => {
    const w = mountCard()
    const btn = w.get('.restore-opt-card__btn')
    expect(btn.attributes('aria-expanded')).toBe('true')
    expect(w.get('.restore-opt-card__caret').classes()).toContain('is-open')
  })

  it('② 点击 header 折叠：emit update:open(false) 且本地展开态翻转，再点展开', async () => {
    const w = mountCard()
    const btn = w.get('.restore-opt-card__btn')
    expect(btn.attributes('aria-expanded')).toBe('true')

    await btn.trigger('click')
    // 标准受控语义：本地写即时生效 + emit 上交（父级回写后才由父级说了算）
    expect(w.emitted('update:open')?.[0]).toEqual([false])
    expect(w.get('.restore-opt-card__btn').attributes('aria-expanded')).toBe('false')
    expect(w.get('.restore-opt-card__caret').classes()).not.toContain('is-open')

    await w.get('.restore-opt-card__btn').trigger('click')
    expect(w.emitted('update:open')?.[1]).toEqual([true])
    expect(w.get('.restore-opt-card__btn').attributes('aria-expanded')).toBe('true')
  })

  it('③ 收起态（open=false）：caret 无 is-open + 折叠容器无 is-open', () => {
    const w = mountCard({ open: false })
    expect(w.get('.restore-opt-card__btn').attributes('aria-expanded')).toBe('false')
    expect(w.get('.restore-opt-card__caret').classes()).not.toContain('is-open')
    expect(w.get('.restore-opt-card__collapse').classes()).not.toContain('is-open')
  })

  it('④ disableToggle：恒展开 + header 退化为非按钮（无假控件、无 caret）', () => {
    const w = mountCard({ disableToggle: true, open: false })
    expect(w.find('.restore-opt-card__btn').exists()).toBe(false)
    expect(w.find('.restore-opt-card__caret').exists()).toBe(false)
    // disableToggle 优先于 open=false：折叠容器仍挂 is-open
    expect(w.get('.restore-opt-card__collapse').classes()).toContain('is-open')
  })

  it('⑤ 内容插槽原样渲染（卡片只做容器，不吞内容）', () => {
    const w = mountCard()
    expect(w.get('.card-body-slot').text()).toBe('内容')
    expect(w.get('.restore-opt-card__body').classes()).toBeTruthy()
  })

  it('⑥ 图标来自单一图标库（n-icon 包裹 svg，禁散落自绘）', () => {
    const w = mountCard()
    const icon = w.get('.restore-opt-card__caret').find('svg')
    expect(icon.exists()).toBe(true)
    expect(icon.attributes('stroke-width')).toBe('1.5')
  })
})
