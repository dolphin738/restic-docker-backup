/** ExcludeEditor 规格：优先级三区块（A 裁决 owner 2026-10-24 重排后的版式护栏）。
 *
 * 锁定语义：
 * - 三区块按优先级编号且 **DOM 顺序 = 编号顺序**：①匹配规则 → ②独立开关 → ③内置排除集；
 * - ③ 只在传入 builtinRules 时渲染，且**只读**（无可关闭按钮、不可编辑输入）；
 * - 快捷项（扩展名 / 名称包含）给「生成」预览（形如 任意层级同名文件 / 任意层级含名字）；
 * - ② 的三项（文件大于 / 标记文件 / 跳过缓存）是独立开关，命中即排除（与规则并行）。
 */

import { mount } from '@vue/test-utils'
import { NTooltip } from 'naive-ui'
import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ExcludeEditor from '@/components/task/ExcludeEditor.vue'
import zhCN from '@/i18n/locales/zh-CN'
import enUS from '@/i18n/locales/en-US'
import { emptyExcludeSpec, type ExcludeSpec } from '@/utils/excludeSpec'

const i18n = createI18n({
  legacy: false,
  locale: 'zh-CN',
  fallbackLocale: 'en-US',
  messages: { 'zh-CN': zhCN, 'en-US': enUS },
})

/** 经一层 wrapper 挂 ExcludeEditor（vue-i18n 插件；locale 显式设 zh-CN——
 *  WSL/CI 的 navigator.language 是 en-US，断言中文前必须显式指定）。
 *  wrapper **必须双向回写** update:modelValue：编辑器是无状态受控组件，
 *  单向断裂会让所有交互断言永远测不到变化（本规格曾踩）。 */
async function mountEditor(
  props: Partial<{ modelValue: ExcludeSpec; sourceRoot: string; builtinRules: string[] }> = {},
) {
  const w = mount(
    {
      components: { ExcludeEditor },
      setup() {
        const state = reactive({
          modelValue: props.modelValue ?? emptyExcludeSpec(),
          sourceRoot: props.sourceRoot ?? '/sources',
          builtinRules: props.builtinRules ?? [],
        })
        const onUpdate = (value: ExcludeSpec): void => {
          state.modelValue = value
        }
        return { state, onUpdate }
      },
      template:
        '<ExcludeEditor :modelValue="state.modelValue" :sourceRoot="state.sourceRoot" :builtinRules="state.builtinRules" @update:modelValue="onUpdate" />',
    },
    { global: { plugins: [i18n] } },
  )
  await nextTick()
  return w
}

function sectionTitles(w: { element: Element }): string[] {
  return [...w.element.querySelectorAll('.exclude-editor__section-title')].map(
    (el) => el.textContent ?? '',
  )
}

describe('ExcludeEditor 优先级三区块（A 裁决）', () => {
  it('三区块按优先级编号，且 DOM 顺序 = 编号顺序', async () => {
    const w = await mountEditor({
      modelValue: {
        quick: { extensions: ['log'], nameContains: ['cache'], largerThan: null, ifPresent: [], caches: false },
        rules: [],
      },
      builtinRules: ['**/lost+found'],
    })
    const titles = sectionTitles(w)
    expect(titles.length).toBe(3)
    expect(titles[0]).toContain('① 匹配规则')
    expect(titles[1]).toContain('② 独立开关')
    expect(titles[2]).toContain('③ 内置排除集')
    // wrapper 根即编辑器根（class 含 exclude-editor）；querySelector 不匹配自身，
    // 故用 classList 断言而非 element.querySelector
    expect(w.element.className).toContain('exclude-editor')
    w.unmount()
  })

  it('内置排除集只读：无可关闭按钮，渲染为只读 tag', async () => {
    const w = await mountEditor({ builtinRules: ['**/lost+found', '**/.DS_Store'] })
    const text = w.element.textContent ?? ''
    expect(text).toContain('**/lost+found')
    expect(text).toContain('③ 内置排除集（强制生效）')
    // 内置集 chips 不可关闭（naive closable 会渲染 .n-tag__close）
    const closeIcons = [...w.element.querySelectorAll('.n-tag__close')]
    expect(closeIcons.length).toBe(0)
    w.unmount()
  })

  it('无内置规则时 ③ 区块不渲染', async () => {
    const w = await mountEditor({ builtinRules: [] })
    const text = w.element.textContent ?? ''
    expect(text).not.toContain('内置排除集')
    expect(sectionTitles(w).length).toBe(2)
    w.unmount()
  })

  it('快捷项给生成预览：**/*.log 与 **/*名字*', async () => {
    const w = await mountEditor({
      modelValue: {
        quick: { extensions: ['log'], nameContains: ['cache'], largerThan: null, ifPresent: [], caches: false },
        rules: [],
      },
    })
    const text = w.element.textContent ?? ''
    expect(text).toContain('**/*.log')
    expect(text).toContain('**/*cache*')
    w.unmount()
  })

  it('② 独立开关三控件齐备且各带说明', async () => {
    const w = await mountEditor()
    const text = w.element.textContent ?? ''
    expect(text).toContain('文件大于')
    expect(text).toContain('标记文件')
    expect(text).toContain('跳过缓存目录')
    expect(text).toContain('② 独立开关（任一命中即排除）')
    w.unmount()
  })

  it('文件大于拆成数字输入 + 单位下拉', async () => {
    const w = await mountEditor()
    const number = w.element.querySelector('.exclude-editor__size-number input')
    const unit = w.element.querySelector('.exclude-editor__size-unit .n-base-selection')
    expect(number).not.toBeNull()
    expect(unit).not.toBeNull()
    w.unmount()
  })

  it('规则行负向 / 忽略大小写开关均带悬停说明（tooltip）', async () => {
    const w = await mountEditor({
      modelValue: {
        quick: { extensions: [], nameContains: [], largerThan: null, ifPresent: [], caches: false },
        rules: [{ pattern: '*.bak', negate: false, icase: false }],
      },
    })
    // n-tooltip 内容懒渲染（未悬停不进 DOM），按组件实例断言
    expect(w.findAllComponents(NTooltip).length).toBeGreaterThanOrEqual(2)
    w.unmount()
  })

  it('名称包含支持多个：输入后点加号追加 chip', async () => {
    const w = await mountEditor()
    // 不用泛型 querySelector（untyped 调用不接受类型参数，TS2347）；显式收窄为 HTMLInputElement
    const input = w.element.querySelector(
      'input[placeholder="如 cache（同名文件会一起排除）"]',
    ) as HTMLInputElement | null
    expect(input).not.toBeNull()
    input!.value = 'cache'
    input!.dispatchEvent(new Event('input'))
    await nextTick()
    // 点名称行的加号按钮（aria-label = 名称包含）
    const addBtn = [...w.element.querySelectorAll('button')].find(
      (b) => b.getAttribute('aria-label') === '名称包含',
    )
    addBtn?.click()
    await nextTick()
    // chip 真实追加（注意：不能断言全文 contains 'cache'——placeholder 本身含该词，属假断言）
    const chipTexts = [...w.element.querySelectorAll('.n-tag')].map((t) => t.textContent ?? '')
    expect(chipTexts).toContain('cache')
    // 生成预览含 任意层级含名字 的宽规则
    expect(w.element.textContent ?? '').toContain('**/*cache*')
    w.unmount()
  })
})
