/**
 * 恢复目标路径选择器护栏（P16 / owner 裁决 D2 树双模式）。
 *
 * 存在理由：D2 选择器是第二步「选择位置」流的核心交互，复刻 Duplicati
 * app-file-tree 的「浏览树 / 手动输入」双模 + 越界内联校验。锁死四件事：
 *  ① 二模切换显隐（浏览树 / 手输框）；
 *  ② 手输合法路径实时 emit（父级 customTarget 是唯一提交值）；
 *  ③ 越界路径（根外 / 非绝对 / .. 穿越）内联报错且**不 emit**——与后端
 *     RESTORE_ALLOWED_PREFIXES 同口径的第一道（V-6）；
 *  ④ 浏览树点目录 emit 容器形态路径（树根 = runtime.restoreRoot 单一事实源）。
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

function mountPicker(modelValue = '/restore') {
  const pinia = createPinia()
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

describe('RestoreTargetPicker：树双模式 + 越界校验', () => {
  it('① 默认浏览树模式：挂载 chips + 树渲染，无手输框', async () => {
    const { wrapper } = mountPicker()
    await flushPromises()
    expect(wrapper.find('.restore-picker__mounts').exists()).toBe(true)
    expect(wrapper.find('.vtree').exists()).toBe(true)
    expect(wrapper.find('input').exists()).toBe(false)
  })

  it('② 切「手动输入」：显输入框 + 说明', async () => {
    const { wrapper } = mountPicker()
    await flushPromises()
    const manualBtn = wrapper.findAll('button').find((b) => b.text().includes('手动输入'))
    await manualBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.find('input').exists()).toBe(true)
    expect(wrapper.text()).toContain('输入 /restore 下的绝对路径作为恢复目标。')
  })

  it('③ 合法路径实时 emit（父级 customTarget 单一来源）', async () => {
    const { wrapper, picker } = mountPicker()
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('手动输入'))!.trigger('click')
    await flushPromises()

    await wrapper.get('input').setValue('/restore/backups')
    await flushPromises()
    expect(picker.emitted('update:modelValue')?.at(-1)).toEqual(['/restore/backups'])
    expect(wrapper.find('.n-alert').exists()).toBe(false)
  })

  it('④ 越界路径内联报错且不 emit（根外 / 非绝对 / .. 三段各一例）', async () => {
    const { wrapper, picker } = mountPicker()
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('手动输入'))!.trigger('click')
    await flushPromises()
    const input = wrapper.get('input')

    await input.setValue('/etc')
    await flushPromises()
    expect(wrapper.text()).toContain('路径须在恢复目录 /restore 内。')

    await input.setValue('restore/x')
    await flushPromises()
    expect(wrapper.text()).toContain('必须是绝对路径（以 / 开头）。')

    await input.setValue('/restore/../etc')
    await flushPromises()
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

  it('⑥ 运行时 restoreRoot 是树根单一事实源（store 值即根 chip 文案）', async () => {
    const { wrapper, pinia } = mountPicker()
    const runtime = useRuntimeStore(pinia)
    runtime.restoreRoot = '/mnt/restore'
    await flushPromises()
    expect(wrapper.text()).toContain('/mnt/restore')
  })
})
