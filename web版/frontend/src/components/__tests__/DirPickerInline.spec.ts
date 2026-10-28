/**
 * DirPickerInline 契约护栏（容器视角目录选择器，repo 页与 rclone crypt 本地目录共用）。
 *
 * 锁定语义：
 * - **挂载点独立预取**：E33 只在根请求回 `mounts`，组件必须自己发一次 `('/' , null, 1)`
 *   （否则地址栏三角恒不渲染——repo 页曾实拍此缺陷）；
 * - 点目录名 = 选中（emit update:path）+ **地址栏跟随选中路径**，但**不改 browsePath**
 *   （导航归地址栏；owner 2026-10-29：地址栏与树根自此分离，勿合并回单一状态）；
 * - 手输路径同样走 update:path（目录可尚不存在）；
 * - 外部赋值 → 树重定位到**父目录**；自己 emit 出去的值回流时**不重定位**（防树跳）；
 * - disabled → 树隐去、输入框禁用（env 覆盖时地址不可改）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'

import DirPickerInline from '@/components/DirPickerInline.vue'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import VirtualTree from '@/components/VirtualTree.vue'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'
import { browseFs } from '@/api/fs'

vi.mock('@/api/fs', () => ({ browseFs: vi.fn() }))

const PAGE_EMPTY = { path: '/', items: [], mounts: ['/repo', '/sources'], next_cursor: null }

function mountPicker(path = '/repo/restic') {
  const i18n = createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN, 'en-US': enUS } })
  return mount(DirPickerInline, {
    props: { path, hint: 'hint', valueLabel: 'label', valueHint: 'vh', placeholder: 'ph' },
    global: { plugins: [i18n] },
  })
}

describe('DirPickerInline', () => {
  beforeEach(() => {
    vi.mocked(browseFs).mockReset()
    vi.mocked(browseFs).mockResolvedValue(PAGE_EMPTY as never)
  })

  it('挂载时独立预取容器根以拿挂载点（不靠树的响应）', async () => {
    mountPicker()
    await nextTick()
    await nextTick()
    const rootCall = vi.mocked(browseFs).mock.calls.find((c) => c[0] === '/' && c[2] === 1)
    expect(rootCall).toBeTruthy()
  })

  it('树起点 = 所填路径的父目录（停在目标目录里会摊开内部结构）', () => {
    const w = mountPicker('/repo/restic')
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/repo')
    // 地址栏显示**选中值本身**（owner 2026-10-29），不是树所在的父目录
    expect(w.findComponent(PathBreadcrumb).props('path')).toBe('/repo/restic')
  })

  it('点目录名发出 update:path，且不改树位置', async () => {
    const w = mountPicker('/repo/restic')
    await nextTick()
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/srv', name: 'srv', type: 'dir', path: '/srv' })
    await nextTick()
    expect(w.emitted('update:path')?.at(-1)).toEqual(['/srv'])
    // 树仍停在 /repo（导航交给地址栏）
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/repo')
  })

  it('点目录名 → 地址栏跟着变成选中路径（owner 2026-10-29，树不动）', async () => {
    const w = mountPicker('/repo/restic')
    await nextTick()
    expect(w.findComponent(PathBreadcrumb).props('path')).toBe('/repo/restic')
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/srv', name: 'srv', type: 'dir', path: '/srv' })
    await nextTick()
    // 地址栏 = 选中项；树根仍是浏览位置（两者分离是本次裁决要点）
    expect(w.findComponent(PathBreadcrumb).props('path')).toBe('/srv')
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/repo')
  })

  it('点文件不选中（只认目录）', async () => {
    const w = mountPicker('/repo/restic')
    await nextTick()
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/a.txt', name: 'a.txt', type: 'file', path: '/a.txt' })
    await nextTick()
    expect(w.emitted('update:path')).toBeUndefined()
  })

  it('手输路径发出 update:path（目录可尚不存在）', async () => {
    const w = mountPicker('')
    const input = w.find('input')
    await input.setValue('/new/dir')
    expect(w.emitted('update:path')?.at(-1)).toEqual(['/new/dir'])
  })

  it('外部赋值 → 树重定位到父目录；自己 emit 回流 → 不重定位', async () => {
    const w = mountPicker('/repo/restic')
    await w.setProps({ path: '/mnt/disk/backup' })
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/mnt/disk')

    // 模拟「点树选中后父组件把同值回灌」：不应把树拉回父目录
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/srv/data', name: 'data', type: 'dir', path: '/srv/data' })
    await nextTick()
    await w.setProps({ path: '/srv/data' })
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/mnt/disk')
  })

  it('disabled：树隐去、输入框禁用（env 覆盖时地址不可改）', async () => {
    const w = mountPicker('/repo/restic')
    await w.setProps({ disabled: true })
    expect(w.findComponent(VirtualTree).exists()).toBe(false)
    expect(w.find('input').attributes('disabled')).toBeDefined()
  })
})
