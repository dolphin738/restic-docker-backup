/**
 * 路径地址栏（D 形态）护栏（P19 / owner 裁决「统一成 D」）。
 *
 * 锁死五件交互事实（都是演示页上逐条实测过的形态，产品代码不得漂移）：
 *  ① 面包屑分段：点段名 = 跳该层，且**不**进编辑态（段与空白是两种热区）；
 *  ② 根段旁三角 = 挂载点菜单（点根段只跳转、点三角只开菜单，两段独立热区）；
 *  ③ 末段右侧空白 = 编辑热区（整条地址 hover 出虚线框只是视觉提示，热区同源）；
 *  ④ 地址栏：Enter 提交（合法 emit / 非法内联报错并**保留**编辑态）、Esc 取消；
 *  ⑤ 失焦：合法即提交，**非法即取消并上报 invalid**（不保留编辑态 = 不劫持焦点）。
 *
 * 另锁两个边界：editable=false 退化为纯面包屑；root 非 `/` 时根段显示 root 本身。
 */

import { mount } from '@vue/test-utils'
import { NConfigProvider } from 'naive-ui'
import { h } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'

import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

const MOUNTS = ['/mnt/c', '/mnt/d', '/var/lib/docker']

function mountBar(props: Record<string, unknown> = {}) {
  return mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: {
      default: () => h(PathBreadcrumb, { path: '/mnt/d/agent', ...props }),
    },
    global: { plugins: [i18n] },
  })
}

/** 组件内查询一律走 wrapper.element（VTU 无 attachTo 时组件挂在脱离文档的 div 上）。 */
function segs(w: { element: Element }): HTMLElement[] {
  return [...w.element.querySelectorAll<HTMLElement>('.pcrumb__seg')]
}

/** 菜单项（挂载点）：走显式 Element 类型——mount() 返回的 element 在 VTU 类型里是
 *  未定型，直接带泛型调用会被 tsc 判为 untyped call。 */
function menuItems(w: { element: Element }): HTMLElement[] {
  return [...w.element.querySelectorAll<HTMLElement>('.pcrumb__menu-item')]
}

function segByText(w: { element: Element }, text: string): HTMLElement | undefined {
  return segs(w).find((el) => el.textContent?.trim() === text)
}

function tail(w: { element: Element }): HTMLElement {
  return w.element.querySelector('.pcrumb__tail') as HTMLElement
}

function caret(w: { element: Element }): HTMLElement {
  return w.element.querySelector('.pcrumb__caret') as HTMLElement
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
})

describe('PathBreadcrumb：面包屑分段与两段热区', () => {
  it('① 点段名 = 跳该层，且不进编辑态', async () => {
    const w = mountBar()
    expect(segs(w).map((el) => el.textContent?.trim())).toEqual(['/', 'mnt', 'd', 'agent'])

    await segByText(w, 'mnt')!.click()
    expect(w.findComponent(PathBreadcrumb).emitted('select')?.at(-1)).toEqual(['/mnt'])
    // 段点击带 stop，不会冒泡成「编辑」
    expect(w.find('input').exists()).toBe(false)
  })

  it('② 点根段只跳转（不开菜单）；点三角只开菜单（不跳转）', async () => {
    const w = mountBar({ mounts: MOUNTS })
    expect(w.find('.pcrumb__menu').exists()).toBe(false)

    await segByText(w, '/')!.click()
    expect(w.findComponent(PathBreadcrumb).emitted('select')?.at(-1)).toEqual(['/'])
    expect(w.find('.pcrumb__menu').exists()).toBe(false)

    await caret(w).click()
    expect(w.find('.pcrumb__menu').exists()).toBe(true)
    // 三角点击不产生跳转（菜单展开前无新 select）
    expect(w.findComponent(PathBreadcrumb).emitted('select')?.at(-1)).toEqual(['/'])
    expect(caret(w).getAttribute('aria-expanded')).toBe('true')
  })

  it('③ 挂载点菜单：点项 = 跳该挂载点并收起', async () => {
    const w = mountBar({ mounts: MOUNTS })
    await caret(w).click()
    const items = menuItems(w)
    expect(items.map((el) => el.textContent?.trim())).toEqual(MOUNTS)

    await items[1].click()
    expect(w.findComponent(PathBreadcrumb).emitted('select')?.at(-1)).toEqual(['/mnt/d'])
    expect(w.find('.pcrumb__menu').exists()).toBe(false)
  })

  it('④ 挂载点为空 → 不渲染三角（退化为纯面包屑）', () => {
    const w = mountBar()
    expect(w.find('.pcrumb__caret').exists()).toBe(false)
  })

  it('④b 键盘可达：三角聚焦后 Esc 收起菜单（不跳转、不 emit）', async () => {
    const w = mountBar({ mounts: MOUNTS })
    await caret(w).click()
    expect(w.find('.pcrumb__menu').exists()).toBe(true)

    // Esc 落在三角上即收起——否则键盘用户只能靠点外部关闭（2026-10-26 实测缺口）
    await w.find('.pcrumb__caret').trigger('keydown', { key: 'Escape' })
    expect(w.find('.pcrumb__menu').exists()).toBe(false)
    expect(w.findComponent(PathBreadcrumb).emitted('select')).toBeUndefined()
  })
})

describe('PathBreadcrumb：地址栏编辑（Enter / Esc / 失焦）', () => {
  it('⑤ 点末段右侧空白 → 变地址栏（预填当前路径）', async () => {
    const w = mountBar()
    await tail(w).click()
    expect(w.find('input').exists()).toBe(true)
    expect((w.find('input').element as HTMLInputElement).value).toBe('/mnt/d/agent')
  })

  it('⑥ Enter 提交合法值（去尾斜杠归一化）并退出编辑', async () => {
    const w = mountBar()
    await tail(w).click()
    await w.get('input').setValue('/srv/data/')
    await w.get('input').trigger('keydown', { key: 'Enter' })
    expect(w.findComponent(PathBreadcrumb).emitted('select')?.at(-1)).toEqual(['/srv/data'])
    expect(w.find('input').exists()).toBe(false)
  })

  it('⑦ Enter 非法值 → 内联报错 + 不 emit + **保留**编辑态（显式提交可继续改）', async () => {
    const w = mountBar()
    await tail(w).click()

    await w.get('input').setValue('mnt/d')
    await w.get('input').trigger('keydown', { key: 'Enter' })
    expect(w.text()).toContain('必须是绝对路径（以 / 开头）。')

    await w.get('input').setValue('/mnt/../etc')
    await w.get('input').trigger('keydown', { key: 'Enter' })
    expect(w.text()).toContain('路径不允许包含 .. 段。')

    expect(w.findComponent(PathBreadcrumb).emitted('select')).toBeUndefined()
    expect(w.find('input').exists()).toBe(true)
  })

  it('⑧ 业务校验走 validate（越界口径由调用方注入，组件不猜）', async () => {
    const w = mountBar({ validate: (v: string) => (v.startsWith('/mnt') ? '' : '越界测试文案') })
    await tail(w).click()
    await w.get('input').setValue('/etc')
    await w.get('input').trigger('keydown', { key: 'Enter' })
    expect(w.text()).toContain('越界测试文案')
    expect(w.findComponent(PathBreadcrumb).emitted('select')).toBeUndefined()
  })

  it('⑨ Esc 取消：退出编辑且不 emit', async () => {
    const w = mountBar()
    await tail(w).click()
    await w.get('input').setValue('/srv')
    await w.get('input').trigger('keydown', { key: 'Escape' })
    expect(w.find('input').exists()).toBe(false)
    expect(w.findComponent(PathBreadcrumb).emitted('select')).toBeUndefined()
  })

  it('⑩ 失焦：合法即提交', async () => {
    const w = mountBar()
    await tail(w).click()
    await w.get('input').setValue('/srv/data')
    await w.get('input').trigger('blur')
    expect(w.findComponent(PathBreadcrumb).emitted('select')?.at(-1)).toEqual(['/srv/data'])
    expect(w.find('input').exists()).toBe(false)
  })

  it('⑪ 失焦：非法即取消 + 上报 invalid（不保留编辑态 = 不劫持焦点）', async () => {
    const w = mountBar()
    await tail(w).click()
    await w.get('input').setValue('srv/data')
    await w.get('input').trigger('blur')
    expect(w.findComponent(PathBreadcrumb).emitted('invalid')?.at(-1)).toEqual([
      '必须是绝对路径（以 / 开头）。',
    ])
    expect(w.findComponent(PathBreadcrumb).emitted('select')).toBeUndefined()
    expect(w.find('input').exists()).toBe(false)
  })

  it('⑫ editable=false → 纯面包屑（无地址栏、无 hover 编辑热区）', async () => {
    const w = mountBar({ editable: false })
    expect(w.find('.pcrumb__bar--addr').exists()).toBe(false)
    await tail(w).click()
    expect(w.find('input').exists()).toBe(false)
  })
})

describe('PathBreadcrumb：root 与越界显示', () => {
  it('⑬ root 非 /：根段显示 root 本身，其后按相对段切分', () => {
    const w = mountBar({ path: '/restore/mydir', root: '/restore' })
    expect(segs(w).map((el) => el.textContent?.trim())).toEqual(['/restore', 'mydir'])
  })

  it('⑭ 路径在 root 之外：按绝对路径完整显示（可读，越界由 validate 拦）', () => {
    const w = mountBar({ path: '/etc/nginx', root: '/restore' })
    expect(segs(w).map((el) => el.textContent?.trim())).toEqual(['/restore', 'etc', 'nginx'])
  })
})
