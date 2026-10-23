/**
 * 步骤 2「备份源」左树右栏版式与可编辑行护栏（2026-10-24）。
 *
 * 存在理由：单栏改双栏 + 已选路径从 tag 群改成「只读行 + 铅笔编辑 + 垃圾桶删除」，
 * 三处语义必须锁死，否则回归会静默退回旧形态：
 *   ① 左栏树、右栏两张卡（路径 / 排除）同屏存在，不再挤在一列；
 *   ② 路径行默认只读（不可就地改坏），铅笔才进编辑态，编辑态给保存/取消；
 *   ③ 编辑提交与删除走 update:sources / update:excludes，非法路径（非绝对、含 ".."）一律拒收。
 *
 * 断言走 DOM 结构类与 emitted，不依赖组件内部变量名。
 */

import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { h, nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SourcesPicker from '@/components/task/SourcesPicker.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

vi.mock('@/api/backupTasks', () => ({
  browseSources: vi.fn(async () => ({ path: '/', items: [], next_cursor: null })),
}))

vi.mock('@/api/exclusions', () => ({
  getExclusions: vi.fn(async () => ({ display_items: ['/sources/proc', '/sources/tmp'] })),
}))

/** 目录树依赖真实滚动与分页游标，与本规格无关 → 存根为占位 div（保留左栏结构断言）。 */
const treeStub = { template: '<div class="vtree-stub" />' }

async function mountPicker(sources: string[] = ['/sources/data'], excludes = ['*.tmp']) {
  const wrapper = mount(
    h(
      NConfigProvider,
      { themeOverrides: lightThemeOverrides },
      {
        default: () =>
          h(NMessageProvider, null, {
            default: () => h(SourcesPicker, { sources, excludes }),
          }),
      },
    ),
    {
      global: {
        plugins: [createPinia(), i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]],
        stubs: { VirtualTree: treeStub },
      },
    },
  )
  await nextTick()
  return wrapper
}

/** 按 aria-label 取按钮（图标按钮无文案，label 是唯一稳定锚点）。 */
function buttonByLabel(root: Element, label: string): HTMLButtonElement | undefined {
  return [...root.querySelectorAll('button')].find(
    (b) => b.getAttribute('aria-label') === label,
  ) as HTMLButtonElement | undefined
}

/** 取首个行内输入框（路径行 / 编辑行 / 添加行共用同一类）。 */
function rowInput(root: Element): HTMLInputElement {
  return root.querySelector('.sources-picker__row input') as HTMLInputElement
}

describe('SourcesPicker 左树右栏与可编辑行', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'zh-CN'
  })

  it('左栏树 + 右栏两张卡（路径 / 排除）同屏', async () => {
    const w = await mountPicker()
    expect(w.element.querySelector('.sources-picker__left .vtree-stub')).not.toBeNull()
    const cards = w.element.querySelectorAll('.sources-picker__right .sources-picker__card')
    expect(cards.length).toBe(2)
    expect(w.element.textContent ?? '').toContain('路径')
    expect(w.element.textContent ?? '').toContain('排除')
    w.unmount()
  })

  it('路径行默认只读：值在、输入框 readonly、带铅笔与垃圾桶', async () => {
    const w = await mountPicker()
    const input = rowInput(w.element)
    expect(input.value).toBe('/sources/data')
    expect(input.readOnly).toBe(true)
    expect(buttonByLabel(w.element, '编辑路径')).toBeDefined()
    expect(buttonByLabel(w.element, '移除路径')).toBeDefined()
    w.unmount()
  })

  it('铅笔 → 进入编辑态（保存/取消出现），取消后回到只读且原值不变', async () => {
    const w = await mountPicker()
    buttonByLabel(w.element, '编辑路径')!.click()
    await nextTick()
    expect(rowInput(w.element).readOnly).toBe(false)
    expect(buttonByLabel(w.element, '保存')).toBeDefined()
    expect(buttonByLabel(w.element, '取消')).toBeDefined()

    buttonByLabel(w.element, '取消')!.click()
    await nextTick()
    expect(rowInput(w.element).readOnly).toBe(true)
    expect(rowInput(w.element).value).toBe('/sources/data')
    expect(w.findComponent(SourcesPicker).emitted('update:sources')).toBeUndefined()
    w.unmount()
  })

  it('编辑提交 → update:sources 收到改名后的完整列表（其余项保序不动）', async () => {
    const w = await mountPicker(['/sources/data', '/sources/srv'])
    const editButtons = [...w.element.querySelectorAll('button')].filter(
      (b) => b.getAttribute('aria-label') === '编辑路径',
    )
    editButtons[0]!.click()
    await nextTick()
    const input = rowInput(w.element)
    input.value = '/sources/data2'
    input.dispatchEvent(new Event('input'))
    await nextTick()
    buttonByLabel(w.element, '保存')!.click()
    await nextTick()
    expect(w.findComponent(SourcesPicker).emitted('update:sources')?.[0]).toEqual([
      ['/sources/data2', '/sources/srv'],
    ])
    w.unmount()
  })

  it('垃圾桶 → update:sources 移除该项', async () => {
    const w = await mountPicker(['/sources/data', '/sources/srv'])
    const trashButtons = [...w.element.querySelectorAll('button')].filter(
      (b) => b.getAttribute('aria-label') === '移除路径',
    )
    trashButtons[1]!.click()
    await nextTick()
    expect(w.findComponent(SourcesPicker).emitted('update:sources')?.[0]).toEqual([['/sources/data']])
    w.unmount()
  })

  it('直接添加路径：合法 → 追加；非绝对 / 含 ".." → 不 emit', async () => {
    const w = await mountPicker([])
    const input = rowInput(w.element)
    input.value = '/sources/new'
    input.dispatchEvent(new Event('input'))
    await nextTick()
    buttonByLabel(w.element, '加入')!.click()
    await nextTick()
    expect(w.findComponent(SourcesPicker).emitted('update:sources')?.[0]).toEqual([['/sources/new']])

    input.value = 'relative/path'
    input.dispatchEvent(new Event('input'))
    await nextTick()
    buttonByLabel(w.element, '加入')!.click()
    await nextTick()
    expect(w.findComponent(SourcesPicker).emitted('update:sources')?.length).toBe(1) // 非法路径未再 emit
    w.unmount()
  })

  it('排除卡片渲染批次②编辑器（快捷卡 + 规则列表），加规则走 update:excludes', async () => {
    const w = await mountPicker(['/sources/data'], ['*.tmp'])
    const text = w.element.textContent ?? ''
    expect(text).toContain('快捷排除')
    expect(text).toContain('排除规则')
    // 规则输入框：已有 '*.tmp' 一行 + 末尾「添加」行
    const inputs = [
      ...w.element.querySelectorAll('.exclude-editor__rule-input input'),
    ] as HTMLInputElement[]
    expect(inputs.length).toBeGreaterThanOrEqual(2)
    const addInput = inputs[inputs.length - 1]!
    addInput.value = '*.log'
    addInput.dispatchEvent(new Event('input'))
    await nextTick()
    buttonByLabel(w.element, '添加规则')!.click()
    await nextTick()
    const emitted = w.findComponent(SourcesPicker).emitted('update:excludes')
    const last = emitted?.[emitted.length - 1]?.[0] as string[]
    expect(last).toContain('*.log')
    w.unmount()
  })
})
