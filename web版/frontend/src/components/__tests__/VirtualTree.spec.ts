/**
 * VirtualTree 交互分工护栏（备份源/仓库路径浏览共用）。
 *
 * 锁定的语义：
 * - **行首三角形**负责展开/收起；
 * - **点目录名**：selectDir=true 时 emit select（加入已选），false 时仍是展开（旧行为）；
 * - 文件无论哪种模式点名即 emit select；
 * - selectedPath 命中的行走高亮样式。
 *
 * 用 @vue/test-utils 挂载真实组件，加载器用可控假数据（单目录树，无需网络）。
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import naive from 'naive-ui'

import VirtualTree, { type TreePage } from '@/components/VirtualTree.vue'
import enUS from '@/i18n/locales/en-US'
import { createI18n } from 'vue-i18n'

const i18n = createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': enUS } })

/** 三棵子树：/a（目录，含 /a/b 目录）、/c（文件）。 */
function loader(): Promise<TreePage> {
  return Promise.resolve({
    items: [
      { key: '/a', name: 'a', type: 'dir', path: '/a' },
      { key: '/c', name: 'c', type: 'file', path: '/c' },
    ],
    nextCursor: null,
  })
}

function childLoader(parentPath: string | undefined): Promise<TreePage> {
  if (parentPath === '/') {
    return loader()
  }
  if (parentPath === '/a') {
    return Promise.resolve({
      items: [{ key: '/a/b', name: 'b', type: 'dir', path: '/a/b' }],
      nextCursor: null,
    })
  }
  if (parentPath === '/a/b') {
    return Promise.resolve({
      items: [{ key: '/a/b/c', name: 'c', type: 'dir', path: '/a/b/c' }],
      nextCursor: null,
    })
  }
  return Promise.resolve({ items: [], nextCursor: null })
}

function mountTree(props: Record<string, unknown> = {}) {
  return mount(VirtualTree, {
    props: { rootPath: '/', loadChildren: childLoader, ...props },
    global: { plugins: [i18n, naive] },
    attachTo: document.body,
  })
}

async function flushLoads(): Promise<void> {
  for (let i = 0; i < 5; i += 1) {
    await nextTick()
    await Promise.resolve()
  }
}

function rowsOf(wrapper: ReturnType<typeof mountTree>) {
  return wrapper.findAll('.vtree__row')
}

describe('VirtualTree 展开与选中分工', () => {
  it('点行首三角形：展开子目录且不触发 select', async () => {
    const wrapper = mountTree()
    await flushLoads()
    expect(rowsOf(wrapper)).toHaveLength(2)

    await wrapper.find('.vtree__caret').trigger('click')
    await flushLoads()
    expect(rowsOf(wrapper)).toHaveLength(3) // /a、/a/b、/c
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('点三角形再点一次：收起（不触发 select）', async () => {
    const wrapper = mountTree()
    await flushLoads()
    const caret = wrapper.find('.vtree__caret')
    await caret.trigger('click')
    await flushLoads()
    expect(rowsOf(wrapper)).toHaveLength(3)
    await caret.trigger('click')
    await flushLoads()
    expect(rowsOf(wrapper)).toHaveLength(2)
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('整行可点：行级 click 覆盖名称文字与行尾空白（同一热区）', async () => {
    const wrapper = mountTree({ selectDir: true })
    await flushLoads()
    // 点行尾空白区域（flex 剩余空间，无子元素）也应触发 select
    const row = rowsOf(wrapper)[0]
    await row.trigger('click', { clientX: 9999 })
    const emits = wrapper.emitted('select')
    expect(emits).toHaveLength(1)
    expect((emits![0][0] as { path: string }).path).toBe('/a')
  })

  it('默认模式：点目录名 = 展开（旧行为，不 emit select）', async () => {
    const wrapper = mountTree()
    await flushLoads()
    await rowsOf(wrapper)[0].find('.vtree__name').trigger('click')
    await flushLoads()
    expect(rowsOf(wrapper)).toHaveLength(3)
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('selectDir：点目录名 = emit select（供备份源页加入已选）', async () => {
    const wrapper = mountTree({ selectDir: true })
    await flushLoads()
    await rowsOf(wrapper)[0].find('.vtree__name').trigger('click')
    await flushLoads()
    const emits = wrapper.emitted('select')
    expect(emits).toHaveLength(1)
    expect((emits![0][0] as { path: string }).path).toBe('/a')
  })

  it('selectDir：点文件名同样 emit select', async () => {
    const wrapper = mountTree({ selectDir: true })
    await flushLoads()
    await rowsOf(wrapper)[1].find('.vtree__name').trigger('click')
    await flushLoads()
    const emits = wrapper.emitted('select')
    expect((emits![0][0] as { path: string }).path).toBe('/c')
  })

  it('selectedPath 命中的行走高亮样式', async () => {
    const wrapper = mountTree({ selectDir: true, selectedPath: '/a' })
    await flushLoads()
    expect(rowsOf(wrapper)[0].classes()).toContain('vtree__row--selected')
    expect(rowsOf(wrapper)[1].classes()).not.toContain('vtree__row--selected')
  })

  it('加载失败：错误区展示且不再保留旧行', async () => {
    const failing = vi.fn(() => Promise.reject(new Error('无权限读取目录：/x')))
    const wrapper = mountTree({ loadChildren: failing })
    await flushLoads()
    expect(wrapper.find('.vtree__error').exists()).toBe(true)
    expect(wrapper.text()).toContain('无权限读取目录')
  })

  it('auto 模式：视口高度 = 内容行数 × 行高（无 height 时不走死高）', async () => {
    const wrapper = mountTree() // 2 行（/a、/c）
    await flushLoads()
    const style = wrapper.find('.vtree__viewport').attributes('style') ?? ''
    expect(style).toContain('height: 72px') // 2 × 36（VIRTUAL_TREE_ROW_HEIGHT）
  })

  it('auto 模式：maxHeight 封顶 → 内容超高时内部滚动', async () => {
    const wrapper = mountTree({ maxHeight: 36 })
    await flushLoads()
    const style = wrapper.find('.vtree__viewport').attributes('style') ?? ''
    expect(style).toContain('height: 36px') // 2 行内容 72 > 36 → 封顶
  })

  it('auto 模式：minHeight 保底 → 行数少时视口不塌陷', async () => {
    const wrapper = mountTree({ minHeight: 360 })
    await flushLoads()
    const style = wrapper.find('.vtree__viewport').attributes('style') ?? ''
    expect(style).toContain('height: 360px') // 2 行内容 72 < 360 → 保底
  })

  it('固定 height 模式优先（仓库/快照/恢复树不受内容驱动影响）', async () => {
    const wrapper = mountTree({ height: 520, minHeight: 360 })
    await flushLoads()
    expect(wrapper.find('.vtree__viewport').attributes('style')).toContain('height: 520px')
  })
})

describe('VirtualTree 高亮与排除视觉（名称点击统一切换，owner 2026-10-24）', () => {
  it('highlightPaths 前缀命中 → 源目录及其子孙行蓝底高亮', async () => {
    const wrapper = mountTree({ highlightPaths: new Set(['/a']) })
    await flushLoads()
    expect(rowsOf(wrapper)[0].classes()).toContain('vtree__row--highlight')
    // 展开 /a → 子孙行同样高亮（「文件夹及其所有子目录和文件均显示为选中状态」）
    await rowsOf(wrapper)[0].find('.vtree__caret').trigger('click')
    await flushLoads()
    expect(rowsOf(wrapper)[1].classes()).toContain('vtree__row--highlight')
  })

  it('excludedPaths 命中 → 行置灰（.vtree__row--excluded）', async () => {
    const wrapper = mountTree({ excludedPaths: new Set(['/a']) })
    await flushLoads()
    expect(rowsOf(wrapper)[0].classes()).toContain('vtree__row--excluded')
  })

  it('excludedPaths 的后代同样置灰（前缀匹配，无需展开加载单独规则）', async () => {
    const wrapper = mountTree({ excludedPaths: new Set(['/a']) })
    await flushLoads()
    await rowsOf(wrapper)[0].find('.vtree__caret').trigger('click')
    await flushLoads()
    expect(rowsOf(wrapper)[1].classes()).toContain('vtree__row--excluded')
  })

  it('挂载点条目渲染徽章', async () => {
    const wrapper = mount(VirtualTree, {
      props: {
        rootPath: '/',
        loadChildren: () =>
          Promise.resolve({
            items: [{ key: '/m', name: 'm', type: 'dir', path: '/m', is_mountpoint: true }],
            nextCursor: null,
          }),
      },
      global: { plugins: [i18n, naive] },
      attachTo: document.body,
    })
    await flushLoads()
    expect(wrapper.find('.vtree__mount').exists()).toBe(true)
  })
})
