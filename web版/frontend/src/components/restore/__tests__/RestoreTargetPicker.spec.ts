/**
 * 恢复目标路径选择器护栏（P16 / owner 裁决 D2 树双模式；P19 快捷路径统一 D 形态）。
 *
 * 存在理由：D2 选择器是第二步「选择位置」流的核心交互。P19 起「浏览树 / 手动输入」
 * 二分段合流为**地址栏**（点空白或末段就地编辑），锁死五件事：
 *  ① 地址栏常驻（面包屑根段 = restoreRoot 单一事实源）+ 树渲染，未编辑时无输入框；
 *  ② 点末段右侧空白 → 就地变地址栏（D 形态热区）；
 *  ③ 地址栏提交合法路径 → emit（父级 customTarget 是唯一提交值）；
 *  ④ 越界（根外 / 非绝对 / ..）与受保护区域内联报错且**不 emit**——与后端
 *     restore_allowed_prefixes 同口径的第一道（V-6）；
 *  ⑤ 浏览树点目录 emit 容器形态路径，受保护目录不选中不下钻（host deny）。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RestoreTargetPicker from '@/components/restore/RestoreTargetPicker.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'
import { useRuntimeStore } from '@/stores/runtime'

const mocks = vi.hoisted(() => ({
  browse: vi.fn(),
}))

vi.mock('@/api/fs', () => ({
  browseFs: mocks.browse,
}))

function mountPicker(
  modelValue = '/restore',
  setup?: (runtime: ReturnType<typeof useRuntimeStore>) => void,
) {
  const pinia = createPinia()
  if (setup) {
    setup(useRuntimeStore(pinia))
  }
  const wrapper = mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: {
      default: () =>
        h(NMessageProvider, null, {
          default: () => h(RestoreTargetPicker, { modelValue }),
        }),
    },
    global: { plugins: [i18n, pinia] },
  })
  return { wrapper, picker: wrapper.findComponent(RestoreTargetPicker), pinia }
}

/** 组件内查询一律走 wrapper.element（VTU 无 attachTo 时挂在脱离文档的 div 上）。 */
function q(w: { element: Element }, sel: string): HTMLElement | null {
  return w.element.querySelector<HTMLElement>(sel)
}

async function openEditor(w: { element: Element }): Promise<void> {
  q(w, '.pcrumb__tail')!.click()
  await flushPromises()
}

async function submit(w: { element: Element }, value: string): Promise<void> {
  const input = q(w, 'input') as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await flushPromises()
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await flushPromises()
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
  mocks.browse.mockReset()
  // 挂载时拉 mounts 快跳（一次性 '/' 请求）；树根本层由 VirtualTree 自拉
  mocks.browse.mockImplementation(async (path: string) => ({
    path,
    items:
      path === '/restore'
        ? [{ name: 'mydir', type: 'dir', path: '/restore/mydir', is_mountpoint: false }]
        : [],
    mounts: path === '/' ? ['/restore'] : [],
    next_cursor: null,
  }))
})

describe('RestoreTargetPicker：地址栏（D 形态）+ 越界校验', () => {
  it('① 默认渲染地址栏 + 树，未编辑时无输入框；挂载点收在三角菜单里', async () => {
    const { wrapper } = mountPicker()
    await flushPromises()
    expect(wrapper.find('.pcrumb').exists()).toBe(true)
    expect(wrapper.find('.vtree').exists()).toBe(true)
    expect(wrapper.find('input').exists()).toBe(false)

    // 快跳不再平铺：默认看不到挂载点，点三角才展开
    expect(wrapper.find('.pcrumb__menu').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('/restore/mydir')
    q(wrapper, '.pcrumb__caret')!.click()
    await flushPromises()
    expect(
      [...wrapper.element.querySelectorAll('.pcrumb__menu-item')].map((el) => el.textContent?.trim()),
    ).toEqual(['/restore'])
  })

  it('② 点末段右侧空白 → 就地变地址栏（预填当前浏览层）', async () => {
    const { wrapper } = mountPicker()
    await flushPromises()
    await openEditor(wrapper)
    expect(wrapper.find('input').exists()).toBe(true)
    expect((q(wrapper, 'input') as HTMLInputElement).value).toBe('/restore')
  })

  it('③ 合法路径提交 → emit（父级 customTarget 单一来源）', async () => {
    const { wrapper, picker } = mountPicker()
    await flushPromises()
    await openEditor(wrapper)
    await submit(wrapper, '/restore/backups')
    expect(picker.emitted('update:modelValue')?.at(-1)).toEqual(['/restore/backups'])
    expect(wrapper.find('.n-alert').exists()).toBe(false)
  })

  it('④ 越界路径内联报错且不 emit（根外 / 非绝对 / .. 三段各一例）', async () => {
    const { wrapper, picker } = mountPicker()
    await flushPromises()
    await openEditor(wrapper)

    await submit(wrapper, '/etc')
    expect(wrapper.text()).toContain('路径须在恢复目录 /restore 内。')

    await submit(wrapper, 'restore/x')
    expect(wrapper.text()).toContain('必须是绝对路径（以 / 开头）。')

    await submit(wrapper, '/restore/../etc')
    expect(wrapper.text()).toContain('路径不允许包含 .. 段。')

    // 三个非法值都不曾 emit（不把脏值写进父级提交链）
    expect(picker.emitted('update:modelValue')).toBeUndefined()
  })

  it('⑤ 浏览树点目录 emit 容器形态路径（/restore/mydir）', async () => {
    const { wrapper, picker } = mountPicker()
    await flushPromises()
    await wrapper.get('.vtree__row').trigger('click')
    await flushPromises()
    expect(picker.emitted('update:modelValue')?.at(-1)).toEqual(['/restore/mydir'])
  })

  it('⑥ 运行时 restoreRoot 是树根单一事实源（store 值即地址栏根段文案）', async () => {
    const { wrapper, pinia } = mountPicker()
    const runtime = useRuntimeStore(pinia)
    runtime.restoreRoot = '/mnt/restore'
    await flushPromises()
    expect(wrapper.find('.pcrumb').text()).toContain('/mnt/restore')
  })

  it('⑦ host 形态 deny：受保护区域经地址栏提交报错，不 emit', async () => {
    const { wrapper, picker } = mountPicker('/', (runtime) => {
      runtime.deployMode = 'host'
      runtime.restoreRoot = '/'
      runtime.inplaceRoot = '/'
      runtime.denyPrefixes = ['/repo']
    })
    await flushPromises()
    await openEditor(wrapper)
    await submit(wrapper, '/repo/data')
    expect(wrapper.text()).toContain('该位置受保护')
    expect(picker.emitted('update:modelValue')).toBeUndefined()
  })

  it('⑧ host 形态 deny：树上点受保护目录不选中、不下钻', async () => {
    mocks.browse.mockImplementation(async (path: string) => ({
      path,
      items:
        path === '/'
          ? [
              { name: 'repo', type: 'dir', path: '/repo', is_mountpoint: false },
              { name: 'srv', type: 'dir', path: '/srv', is_mountpoint: false },
            ]
          : [],
      mounts: [],
      next_cursor: null,
    }))
    const { wrapper, picker } = mountPicker('/', (runtime) => {
      runtime.deployMode = 'host'
      runtime.restoreRoot = '/'
      runtime.inplaceRoot = '/'
      runtime.denyPrefixes = ['/repo']
    })
    await flushPromises()

    // 第一条 = /repo（受保护）→ 不 emit；第二条 = /srv → 正常选中
    const rows = wrapper.findAll('.vtree__row')
    await rows[0].trigger('click')
    await flushPromises()
    expect(picker.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.text()).toContain('该位置受保护')

    await rows[1].trigger('click')
    await flushPromises()
    expect(picker.emitted('update:modelValue')?.at(-1)).toEqual(['/srv'])
  })
})
