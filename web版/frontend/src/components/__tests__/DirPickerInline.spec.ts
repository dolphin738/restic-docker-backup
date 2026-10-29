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
 * - disabled → 树隐去、输入框禁用（env 覆盖时地址不可改）；
 * - **新建文件夹**（2026-10-29）：单级创建 → 选中新目录 → 刷新父层即时展示；
 *   名称空 / 含 `/` 走客户端拦截（不发请求）；API 失败时弹窗保持打开可改名的重试。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent, h, nextTick } from 'vue'
import { NMessageProvider } from 'naive-ui'

import DirPickerInline from '@/components/DirPickerInline.vue'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import VirtualTree from '@/components/VirtualTree.vue'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'
import { browseFs, mkdirFs } from '@/api/fs'

vi.mock('@/api/fs', () => ({ browseFs: vi.fn(), mkdirFs: vi.fn() }))

const PAGE_EMPTY = { path: '/', items: [], mounts: ['/repo', '/sources'], next_cursor: null }

function mountHost(path = '/repo/restic') {
  const i18n = createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN, 'en-US': enUS } })
  // naive 的 useMessage 要求外层 NMessageProvider（否则组件 setup 直接抛错）；
  // 宿主壳显式声明 props 并透传，才能让 setProps 直接驱动内层组件。
  const Host = defineComponent({
    props: {
      path: { type: String, default: '' },
      hint: { type: String, default: '' },
      valueLabel: { type: String, default: '' },
      valueHint: { type: String, default: '' },
      placeholder: { type: String, default: '' },
      disabled: { type: Boolean, default: false },
    },
    setup(hostProps) {
      return () =>
        h(NMessageProvider, null, {
          default: () =>
            h(DirPickerInline, {
              path: hostProps.path,
              hint: hostProps.hint,
              valueLabel: hostProps.valueLabel,
              valueHint: hostProps.valueHint,
              placeholder: hostProps.placeholder,
              disabled: hostProps.disabled,
            }),
        })
    },
  })
  return mount(Host, {
    props: { path, hint: 'hint', valueLabel: 'label', valueHint: 'vh', placeholder: 'ph' },
    global: { plugins: [i18n] },
  })
}

type Host = ReturnType<typeof mountHost>

/** 外层只是 MessageProvider 宿主壳，业务断言一律落在内层 DirPickerInline 上。 */
function pickerOf(host: Host) {
  return host.findComponent(DirPickerInline)
}

/** 弹窗经 naive teleport 到 body：VTU 无 attachTo 时文档里只剩传送过去的内容，
 *  故弹窗内元素按 document.body 查（与 RcloneConfigPanel.spec 同口径）。 */
function bodyInput(): HTMLInputElement {
  const inputs = document.body.querySelectorAll('input')
  return inputs[inputs.length - 1] as HTMLInputElement
}

function bodyButton(label: string): HTMLButtonElement | undefined {
  return [...document.body.querySelectorAll('button')].find(
    (el) => (el.textContent ?? '').includes(label),
  ) as HTMLButtonElement | undefined
}

async function openCreate(host: Host, name: string): Promise<void> {
  const trigger = [...pickerOf(host).element.querySelectorAll('button')].find((el) =>
    (el.textContent ?? '').includes('新建文件夹'),
  ) as HTMLButtonElement | undefined
  expect(trigger).toBeTruthy()
  trigger!.click()
  await nextTick()
  await nextTick()
  const input = bodyInput()
  input.value = name
  input.dispatchEvent(new Event('input'))
  await nextTick()
}

async function clickCreate(): Promise<void> {
  const btn = bodyButton('创建')
  expect(btn).toBeTruthy()
  btn!.click()
  await nextTick()
  await nextTick()
  await nextTick()
}

describe('DirPickerInline', () => {
  beforeEach(() => {
    vi.mocked(browseFs).mockReset()
    vi.mocked(browseFs).mockResolvedValue(PAGE_EMPTY as never)
    vi.mocked(mkdirFs).mockReset()
    document.body.innerHTML = ''
  })

  it('挂载时独立预取容器根以拿挂载点（不靠树的响应）', async () => {
    mountHost()
    await nextTick()
    await nextTick()
    const rootCall = vi.mocked(browseFs).mock.calls.find((c) => c[0] === '/' && c[2] === 1)
    expect(rootCall).toBeTruthy()
  })

  it('树起点 = 所填路径的父目录（停在目标目录里会摊开内部结构）', () => {
    const w = mountHost('/repo/restic')
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/repo')
    // 地址栏显示**选中值本身**（owner 2026-10-29），不是树所在的父目录
    expect(w.findComponent(PathBreadcrumb).props('path')).toBe('/repo/restic')
  })

  it('点目录名发出 update:path，且不改树位置', async () => {
    const w = mountHost('/repo/restic')
    await nextTick()
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/srv', name: 'srv', type: 'dir', path: '/srv' })
    await nextTick()
    expect(pickerOf(w).emitted('update:path')?.at(-1)).toEqual(['/srv'])
    // 树仍停在 /repo（导航交给地址栏）
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/repo')
  })

  it('点目录名 → 地址栏跟着变成选中路径（owner 2026-10-29，树不动）', async () => {
    const w = mountHost('/repo/restic')
    await nextTick()
    expect(w.findComponent(PathBreadcrumb).props('path')).toBe('/repo/restic')
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/srv', name: 'srv', type: 'dir', path: '/srv' })
    await nextTick()
    // 地址栏 = 选中项；树根仍是浏览位置（两者分离是本次裁决要点）
    expect(w.findComponent(PathBreadcrumb).props('path')).toBe('/srv')
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/repo')
  })

  it('点文件不选中（只认目录）', async () => {
    const w = mountHost('/repo/restic')
    await nextTick()
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/a.txt', name: 'a.txt', type: 'file', path: '/a.txt' })
    await nextTick()
    expect(pickerOf(w).emitted('update:path')).toBeUndefined()
  })

  it('手输路径发出 update:path（目录可尚不存在）', async () => {
    const w = mountHost('')
    const input = pickerOf(w).find('input')
    await input.setValue('/new/dir')
    expect(pickerOf(w).emitted('update:path')?.at(-1)).toEqual(['/new/dir'])
  })

  it('外部赋值 → 树重定位到父目录；自己 emit 回流 → 不重定位', async () => {
    const w = mountHost('/repo/restic')
    await w.setProps({ path: '/mnt/disk/backup' })
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/mnt/disk')

    // 模拟「点树选中后父组件把同值回灌」：不应把树拉回父目录
    w.findComponent(VirtualTree).vm.$emit('select', { key: '/srv/data', name: 'data', type: 'dir', path: '/srv/data' })
    await nextTick()
    await w.setProps({ path: '/srv/data' })
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/mnt/disk')
  })

  it('disabled：树隐去、输入框禁用（env 覆盖时地址不可改）', async () => {
    const w = mountHost('/repo/restic')
    await w.setProps({ disabled: true })
    expect(w.findComponent(VirtualTree).exists()).toBe(false)
    expect(pickerOf(w).find('input').attributes('disabled')).toBeDefined()
  })

  // ---------------------------------------------------------------- 新建文件夹

  it('新建文件夹：按选中路径拼绝对路径 → 创建 → 选中新目录', async () => {
    vi.mocked(mkdirFs).mockResolvedValue({ path: '/repo/restic/newone', created: true })
    const w = mountHost('/repo/restic')
    await nextTick()
    await openCreate(w, 'newone')
    await clickCreate()

    expect(vi.mocked(mkdirFs)).toHaveBeenCalledWith('/repo/restic/newone')
    expect(pickerOf(w).emitted('update:path')?.at(-1)).toEqual(['/repo/restic/newone'])
    // 地址栏跟到新目录（与点树选中的口径一致）
    expect(w.findComponent(PathBreadcrumb).props('path')).toBe('/repo/restic/newone')
    // 树根不因创建而跳转（仍是选中项的父目录）
    expect(w.findComponent(VirtualTree).props('rootPath')).toBe('/repo')
  })

  it('在根下创建：不再补一个前导斜杠（//x 会被服务端规范化掉，但契约要精确）', async () => {
    vi.mocked(mkdirFs).mockResolvedValue({ path: '/top', created: true })
    const w = mountHost('/')
    await nextTick()
    await openCreate(w, 'top')
    await clickCreate()
    expect(vi.mocked(mkdirFs)).toHaveBeenCalledWith('/top')
  })

  it('已存在的目录：created=false 仍选中（幂等，不当错误处理）', async () => {
    vi.mocked(mkdirFs).mockResolvedValue({ path: '/repo/exists', created: false })
    const w = mountHost('/repo')
    await nextTick()
    await openCreate(w, 'exists')
    await clickCreate()
    expect(pickerOf(w).emitted('update:path')?.at(-1)).toEqual(['/repo/exists'])
  })

  it('名称为空 → 客户端拦截，不发请求（空名会被服务端 422）', async () => {
    const w = mountHost('/repo')
    await nextTick()
    await openCreate(w, '   ')
    await clickCreate()
    expect(vi.mocked(mkdirFs)).not.toHaveBeenCalled()
    // 弹窗内就地提示，不弹全局 toast（全站一个提示出口）
    expect(document.body.textContent ?? '').toContain('请输入文件夹名称')
  })

  it('名称含 / → 客户端拦截（单级语义：父目录必须已存在）', async () => {
    const w = mountHost('/repo')
    await nextTick()
    await openCreate(w, 'a/b')
    await clickCreate()
    expect(vi.mocked(mkdirFs)).not.toHaveBeenCalled()
  })

  it('创建失败 → 弹窗保持打开可改名重试，且不误报选中路径', async () => {
    vi.mocked(mkdirFs).mockRejectedValue(new Error('父目录不存在'))
    const w = mountHost('/repo')
    await nextTick()
    await openCreate(w, 'x')
    await clickCreate()
    expect(pickerOf(w).emitted('update:path')).toBeUndefined()
    // 弹窗仍在（名字输入框还在），用户可直接修改后重试
    expect(bodyInput()).toBeTruthy()
  })
})
