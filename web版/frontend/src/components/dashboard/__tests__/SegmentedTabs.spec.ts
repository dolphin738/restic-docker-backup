/**
 * SegmentedTabs 分段选中态护栏（方案 §4.7 P6 批次验收门「分段选中态」对策）。
 *
 * 锁定语义（P6 从设置页私有分段条提升为共用组件的契约）：
 * - 选中态 = NButton primary（n-button--primary-type）+ aria-pressed=true；
 *   次态 = default/secondary + aria-pressed=false；
 * - 点击次态发射 update:modelValue + change（双发射，v-model 与显式监听各取所需）；
 * - 已选中项重复点击空转（不制造无意义流转）；
 * - 未知 id 载荷防御：经深链路径注入的未知 id 被忽略（保持调用方 modelValue）；
 * - hashPrefix 深链：#<prefix>-<id> 直达（replaceState 不触发路由），
 *   无 hash / 未知 id 不主动补写地址栏（与原设置页私有实现逐字等价）；
 * - 外部改 modelValue 同样同步地址栏（watch 双向一致）。
 *
 * 查询纪律：VTU 2.5.1 无 attachTo → 挂载节点脱离文档；组件自身根元素断言走
 * w.classes()/w.attributes()/w.trigger()（VTU 的 find/trigger 含根匹配，原生
 * querySelector 不含自身）；禁 document 全局搜（本组件不涉 portal）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { NButton } from 'naive-ui'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SegmentedTabs, { type SegmentedItem } from '@/components/dashboard/SegmentedTabs.vue'

const ITEMS: SegmentedItem[] = [
  { id: 'dump', label: '备份' },
  { id: 'behavior', label: '行为' },
]

let replaceStateSpy: ReturnType<typeof vi.spyOn> | null = null

afterEach(() => {
  replaceStateSpy?.mockRestore()
  replaceStateSpy = null
})

/** 在 spy 创建前把地址栏设到目标 hash（spy 就位后不再碰 replaceState）。 */
function setHash(hash: string): void {
  window.history.replaceState(null, '', hash)
}

function armReplaceStateSpy(): void {
  replaceStateSpy = vi.spyOn(window.history, 'replaceState')
}

function mountTabs(modelValue = 'dump', hashPrefix?: string) {
  return mount(SegmentedTabs, {
    props: { items: ITEMS, modelValue, hashPrefix },
  })
}

describe('SegmentedTabs 分段选中态', () => {
  it('选中项 primary（n-button--primary-type）+ aria-pressed=true；次态 default + false', () => {
    const w = mountTabs('dump')
    const buttons = w.findAllComponents(NButton)
    expect(buttons).toHaveLength(2)

    const active = buttons[0]
    expect(active.classes()).toContain('n-button--primary-type')
    expect(active.attributes('aria-pressed')).toBe('true')
    // 次态：default 类型 + secondary 弱化
    expect(buttons[1].classes()).toContain('n-button--default-type')
    expect(buttons[1].attributes('aria-pressed')).toBe('false')
    w.unmount()
  })

  it('点击次态发射 update:modelValue + change（双发射）', async () => {
    const w = mountTabs('dump')
    await w.findAllComponents(NButton)[1].trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([['behavior']])
    expect(w.emitted('change')).toEqual([['behavior']])
    w.unmount()
  })

  it('已选中项重复点击空转（不发射）', async () => {
    const w = mountTabs('dump')
    await w.findAllComponents(NButton)[0].trigger('click')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    expect(w.emitted('change')).toBeUndefined()
    w.unmount()
  })

  it('容器 role=group + 文案渲染', () => {
    const w = mountTabs('behavior', 'sec')
    expect(w.attributes('role')).toBe('group')
    expect(w.text()).toContain('备份')
    expect(w.text()).toContain('行为')
    w.unmount()
  })
})

describe('SegmentedTabs 深链（hashPrefix）', () => {
  it('#<prefix>-<id> 直达：合法 hash 挂载即落到对应分段', async () => {
    setHash('/#sec-behavior')
    const w = mountTabs('dump', 'sec')
    await flushPromises()
    expect(w.emitted('update:modelValue')).toEqual([['behavior']])
    w.unmount()
  })

  it('未知 id 载荷防御：hash 不在 items 内被忽略（保持调用方初值、不补写地址栏）', async () => {
    setHash('/#sec-unknown')
    armReplaceStateSpy() // spy 就位后才挂载：此后任何 replaceState 都归组件
    const w = mountTabs('dump', 'sec')
    await flushPromises()
    expect(w.emitted('update:modelValue')).toBeUndefined()
    expect(replaceStateSpy).not.toHaveBeenCalled()
    w.unmount()
  })

  it('无 hash 挂载不补写；外部改 modelValue 时 watch 同步地址栏', async () => {
    setHash('/')
    armReplaceStateSpy()
    const w = mountTabs('dump', 'sec')
    await flushPromises()
    expect(replaceStateSpy).not.toHaveBeenCalled()

    await w.setProps({ modelValue: 'behavior' })
    await flushPromises()
    expect(replaceStateSpy).toHaveBeenCalledWith(null, '', '#sec-behavior')
    w.unmount()
  })

  it('未给 hashPrefix 时不碰地址栏（纯受控组件）', async () => {
    setHash('/#sec-behavior')
    armReplaceStateSpy()
    const w = mountTabs('dump')
    await flushPromises()
    expect(w.emitted('update:modelValue')).toBeUndefined()
    expect(replaceStateSpy).not.toHaveBeenCalled()
    w.unmount()
  })
})
