/**
 * 筛选条（P6 形态层补员）行为护栏：外壳 + 控件插槽 + 应用/重置。
 *
 * 存在理由：任务历史页与快照页的筛选版式曾各自手写一份（S-4 违例 + 一次
 * 「max-width 被按钮挤成 0 宽」的真实回归），收敛为单一组件后由本规格守住：
 * ① 插槽内容原样渲染；② 应用/重置只 emit、不自带查询语义；
 * ③ loading 透传到应用按钮；④ 按钮文案走 common 双语键。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { NButton, NInput, NIcon } from 'naive-ui'
import { beforeEach, describe, expect, it } from 'vitest'
import { h } from 'vue'

import FilterBar from '@/components/FilterBar.vue'
import { i18n } from '@/i18n'

function mountBar(props: { loading?: boolean } = {}) {
  return mount(FilterBar, {
    props,
    slots: { default: () => h(NInput, { placeholder: '字段' }) },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
})

describe('FilterBar：外壳与按钮', () => {
  it('插槽控件原样渲染，应用/重置只 emit 不查询', async () => {
    const w = mountBar()
    await flushPromises()
    expect(w.find('input[placeholder="字段"]').exists()).toBe(true)

    const buttons = w.findAllComponents(NButton)
    expect(buttons.length).toBe(2)
    await buttons[0].vm.$emit('click')
    await buttons[1].vm.$emit('click')
    expect(w.emitted('apply')).toHaveLength(1)
    expect(w.emitted('reset')).toHaveLength(1)
    w.unmount()
  })

  it('loading 透传到应用按钮（primary），重置为弱化按钮', async () => {
    const w = mountBar({ loading: true })
    await flushPromises()
    const buttons = w.findAllComponents(NButton)
    expect(buttons[0].props('loading')).toBe(true)
    expect(buttons[0].props('type')).toBe('primary')
    expect(buttons[1].props('quaternary')).toBe(true)
    w.unmount()
  })

  it('应用按钮带漏斗图标（P15：small 及以上用 #icon 插槽）', async () => {
    const w = mountBar()
    await flushPromises()
    const apply = w.findAllComponents(NButton)[0]
    expect(apply.findComponent(NIcon).exists()).toBe(true)
    w.unmount()
  })

  it('按钮文案走 common 双语键', async () => {
    const w = mountBar()
    await flushPromises()
    expect(w.text()).toContain('应用筛选')
    expect(w.text()).toContain('重置')
    w.unmount()
  })
})
