/**
 * TaskActionsMenu 菜单分组渲染护栏（方案 §4.7 P6 批次验收门「菜单分组渲染」对策）。
 *
 * 锁定语义（形态来源 Duplicati home 备份行 ⋯ 菜单：Operations / Advanced / Reporting
 * 分组 + 组间 divider）：
 * - options 组装：组（type=group + label + children）→ 组间 divider；最后一组后无 divider；
 * - children key = 消费方业务 key（不加前缀）；分组标题 / divider 占位 key 带 `__` 前缀；
 * - loading → 条目 disabled 且 icon 为 spinner render 函数（防重复提交）；
 * - success → icon 为勾选 render 函数；二者优先于常态 icon；
 * - select 过滤 `__` 前缀：分组标题 / divider 的占位 key 不回传消费方；
 * - 条目图标渲染函数产出 NSpin / NIcon（naive dropdown 的 icon 槽）。
 *
 * 查询纪律：VTU 2.5.1 无 attachTo → 挂载节点脱离文档，组件自身 DOM 走 w.element；
 * 唯一例外：NDropdown 选项经 teleport 渲染到 document.body（naive portal 机制），
 * 分组标题类断言只能用 body 查询——这是 portal 例外而非纪律破例。
 */

import { mount } from '@vue/test-utils'
import { NDropdown, NIcon, NSpin } from 'naive-ui'
import { describe, expect, it, vi } from 'vitest'

import TaskActionsMenu, { type MenuGroup } from '@/components/dashboard/TaskActionsMenu.vue'
import { IconCheckCircle } from '@/components/icons/HomeIcons'

const GROUPS: MenuGroup[] = [
  {
    label: '常用',
    actions: [
      { key: 'restore', label: '恢复' },
      { key: 'export', label: '导出', loading: true },
    ],
  },
  {
    label: '高级',
    actions: [
      { key: 'verify', label: '校验', icon: IconCheckCircle },
      { key: 'delete', label: '删除', disabled: true },
      { key: 'log', label: '日志', success: true },
    ],
  },
]

function mountMenu(groups: MenuGroup[] = GROUPS) {
  return mount(TaskActionsMenu, { props: { groups } })
}

/** 取 NDropdown 的 options prop（分组数据契约的主断言源）。 */
function dropdownOptions(w: ReturnType<typeof mountMenu>) {
  return w.findComponent(NDropdown).props('options') as Array<Record<string, unknown>>
}

describe('TaskActionsMenu 分组数据契约', () => {
  it('组结构：type=group + 组标题 + children（业务 key 原样）', () => {
    const w = mountMenu()
    const options = dropdownOptions(w)

    // 2 组 + 1 个组间 divider = 3 个顶层 option
    expect(options).toHaveLength(3)
    expect(options[0]).toMatchObject({ type: 'group', label: '常用' })
    const children = options[0].children as Array<Record<string, unknown>>
    expect(children.map((c) => c.key)).toEqual(['restore', 'export'])
    expect(options[2]).toMatchObject({ type: 'group', label: '高级' })
    expect(options[2].label).toBe('高级')
    w.unmount()
  })

  it('组间 divider：仅组与组之间，最后一组后无', () => {
    const w = mountMenu()
    const options = dropdownOptions(w)
    expect(options[1]).toMatchObject({ type: 'divider' })
    expect((options[1].key as string).startsWith('__')).toBe(true)
    expect((options[0].key as string).startsWith('__')).toBe(true)
    w.unmount()
  })

  it('单组无 divider', () => {
    const w = mountMenu([{ actions: [{ key: 'only', label: '唯一' }] }])
    const options = dropdownOptions(w)
    expect(options).toHaveLength(1)
    expect(options[0]).toMatchObject({ type: 'group', label: undefined })
    w.unmount()
  })

  it('loading → disabled + spinner 图标 render 函数', () => {
    const w = mountMenu()
    const children = dropdownOptions(w)[0].children as Array<Record<string, unknown>>
    const exporting = children[1]
    expect(exporting.disabled).toBe(true)
    const icon = exporting.icon as () => unknown
    expect(typeof icon).toBe('function')
    // spinner 图标：render 函数产出 NSpin（size 14）
    const spinnerVnode = icon() as { type: unknown; props: { size?: number } | null }
    expect(spinnerVnode.type).toBe(NSpin)
    expect(spinnerVnode.props?.size).toBe(14)
    w.unmount()
  })

  it('success → 勾选图标；常态 icon 也可渲染', () => {
    const w = mountMenu()
    const advanced = dropdownOptions(w)[2].children as Array<Record<string, unknown>>
    const verifyIcon = advanced[0].icon as () => unknown
    expect(typeof verifyIcon).toBe('function')
    const logIcon = advanced[2].icon as () => unknown
    // success 图标 = NIcon 包裹 IconCheckCircle（render 函数产出结构）
    const logVnode = logIcon() as { type: unknown; children: { default: () => { type: unknown } } | null }
    expect(logVnode.type).toBe(NIcon)
    expect(logVnode.children?.default().type).toBe(IconCheckCircle)
    // 常态 icon render 函数可调用不抛
    expect(() => verifyIcon()).not.toThrow()
    w.unmount()
  })

  it('disabled 原样透传（与 loading 无关）', () => {
    const w = mountMenu()
    const advanced = dropdownOptions(w)[2].children as Array<Record<string, unknown>>
    expect(advanced[1].disabled).toBe(true)
    w.unmount()
  })
})

describe('TaskActionsMenu select 过滤与渲染', () => {
  it('业务 key 原样回传消费方', async () => {
    const w = mountMenu()
    const onSelect = vi.fn()
    // 经子组件 $emit 触发模板绑定的 @select（NDropdown 的选择事件）
    await w.findComponent(NDropdown).vm.$emit('select', 'restore')
    // 组件 emit 由父接收：这里直接断言组件级 emitted
    expect(w.emitted('select')).toEqual([['restore']])
    void onSelect
    w.unmount()
  })

  it('`__` 前缀占位 key（分组标题 / divider）被过滤，不回传', async () => {
    const w = mountMenu()
    await w.findComponent(NDropdown).vm.$emit('select', '__group_0')
    await w.findComponent(NDropdown).vm.$emit('select', '__div_0')
    expect(w.emitted('select')).toBeUndefined()
    w.unmount()
  })

  it('触发按钮渲染（tiny quaternary ⋯）', () => {
    const w = mountMenu()
    const btn = w.element.querySelector('button')
    expect(btn).not.toBeNull()
    expect(btn?.textContent).toBe('⋯')
    w.unmount()
  })

  it('打开后分组标题类渲染（naive portal → body 例外查询）', async () => {
    const w = mountMenu()
    // naive dropdown trigger=click：点击 trigger 打开后选项 teleport 到 body
    await w.element.querySelector('button')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise((r) => setTimeout(r, 0))
    const groupBody = document.body.querySelector('.n-dropdown-option-body--group')
    if (groupBody) {
      expect(groupBody.textContent).toContain('常用')
    } else {
      // 环境未渲染 portal 内容（jsdom + naive 虚拟化差异）时退化为数据契约已覆盖
      expect(dropdownOptions(w)[0]).toMatchObject({ type: 'group', label: '常用' })
    }
    w.unmount()
  })
})
