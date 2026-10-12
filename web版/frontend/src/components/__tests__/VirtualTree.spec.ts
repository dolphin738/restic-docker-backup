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
  return Promise.resolve({
    items: [{ key: '/a/b', name: 'b', type: 'dir', path: '/a/b' }],
    nextCursor: null,
  })
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
})
