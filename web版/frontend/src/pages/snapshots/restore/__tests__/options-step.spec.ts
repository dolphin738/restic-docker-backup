/**
 * 恢复向导第二步「恢复选项」行为护栏（P16 / UI全量改造方案 §4.9）。
 *
 * 存在理由：P16 把第二步从「n-space 平铺两分区」重写为 Duplicati 式三折叠卡 +
 * 两端页脚，并引入 restoreFrom/customTarget 新状态与路径选择器。最容易静默坏掉的是：
 *  ① 单选卡不 emit / 原位置置灰却仍可点（后端双重锁前的提示性限制失效）；
 *  ② 模式与内嵌控件脱钩（原位置不显目标下拉、选择位置不显选择器）；
 *  ③ E20 预览并入卡片②后，原位流/选择位置流的分支渲染错位；
 *  ④ 页脚 submit 在 canOpenGate=false 时仍可点（未预览就开确认门）。
 * 另锁死 A7 既有语义不回退：overwrite 布尔 emit、picking 模式回落文案。
 */

import { mount } from '@vue/test-utils'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { createPinia } from 'pinia'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Options from '@/pages/snapshots/restore/options.vue'
import { i18n } from '@/i18n'
import { lightThemeOverrides } from '@/theme/tokens'

// 选择器挂载时会拉 mounts 快跳与树根；mock 掉保持 hermetic（本规格不断言取数）。
vi.mock('@/api/fs', () => ({
  browseFs: vi.fn(async () => ({ path: '/restore', items: [], mounts: [], next_cursor: null })),
}))

function mountOptions(props: Record<string, unknown> = {}) {
  const wrapper = mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: {
      default: () =>
        h(NMessageProvider, null, {
          default: () =>
            h(Options, {
              restoreFrom: 'original',
              isInPlace: true,
              inPlaceDisabled: false,
              inPlaceBlockReason: '',
              inplaceTarget: '/data',
              inplaceTargetOptions: [{ label: '/data', value: '/data' }],
              customTarget: '/restore',
              restoreRoot: '/restore',
              overwriteExisting: false,
              submitting: false,
              canOpenGate: true,
              previewItems: [],
              previewStats: null,
              previewCursor: null,
              previewLoading: false,
              previewLoadingMore: false,
              previewError: '',
              previewSizeText: '',
              previewPolicy: '',
              ...props,
            }),
        }),
    },
    global: { plugins: [i18n, createPinia()] },
  })
  return wrapper
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
})

describe('第二步 options（P16 三卡片 + 两端页脚）', () => {
  it('① 卡片① 单选卡：原位置默认选中（aria-checked），点选择位置 emit restoreFrom', async () => {
    const w = mountOptions()
    const radios = w.findAll('[role="radio"]')
    expect(radios).toHaveLength(2)
    expect(radios[0].attributes('aria-checked')).toBe('true')
    expect(radios[1].attributes('aria-checked')).toBe('false')

    await radios[1].trigger('click')
    expect(w.findComponent(Options).emitted('update:restoreFrom')?.[0]).toEqual(['pickLocation'])
  })

  it('② 原位置置灰：单选卡 disabled + 原因 alert 出现；点击不 emit', async () => {
    const w = mountOptions({
      inPlaceDisabled: true,
      inPlaceBlockReason: '该快照未记录可定位的原目录，无法原位恢复。',
    })
    const radios = w.findAll('[role="radio"]')
    expect(radios[0].attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('该快照未记录可定位的原目录，无法原位恢复。')

    await radios[0].trigger('click')
    expect(w.findComponent(Options).emitted('update:restoreFrom')).toBeUndefined()
  })

  it('③ 模式与内嵌控件挂钩：原位置显目标下拉，选择位置显路径选择器', () => {
    const inplace = mountOptions()
    expect(inplace.find('.n-base-selection').exists()).toBe(true)
    expect(inplace.findComponent({ name: 'RestoreTargetPicker' }).exists()).toBe(false)

    const pick = mountOptions({ restoreFrom: 'pickLocation', isInPlace: false })
    expect(pick.findComponent({ name: 'RestoreTargetPicker' }).exists()).toBe(true)
  })

  it('④ 卡片②：原位流 overwrite 布尔 emit；选择位置流显零覆盖说明且无勾选', async () => {
    // naive n-checkbox 渲染 div[role=checkbox]（无原生 input），按角色定位
    const inplace = mountOptions()
    const box = inplace.get('.n-checkbox')
    expect(box.attributes('aria-checked')).toBe('false')
    await box.trigger('click')
    expect(inplace.findComponent(Options).emitted('update:overwriteExisting')?.[0]).toEqual([true])

    const pick = mountOptions({ restoreFrom: 'pickLocation', isInPlace: false })
    expect(pick.text()).toContain('时间戳子目录为零覆盖语义，不涉及覆盖选项。')
    expect(pick.find('.n-checkbox').exists()).toBe(false)
  })

  it('⑤ 页脚两端：返回 emit back / 提交 emit next；canOpenGate=false 时提交禁用', async () => {
    const w = mountOptions()
    const buttons = w.findAll('button')
    const back = buttons.find((b) => b.text().includes('返回'))
    const submit = buttons.find((b) => b.text().includes('提交'))
    expect(back).toBeTruthy()
    expect(submit).toBeTruthy()

    await back!.trigger('click')
    expect(w.findComponent(Options).emitted('back')).toBeTruthy()
    await submit!.trigger('click')
    expect(w.findComponent(Options).emitted('next')).toBeTruthy()

    const blocked = mountOptions({ canOpenGate: false })
    const blockedSubmit = blocked.findAll('button').find((b) => b.text().includes('提交'))
    expect(blockedSubmit!.attributes('disabled')).toBeDefined()
  })

  it('⑥ 三卡片标题齐备（恢复到哪 / 已存在文件；Metadata·高级按 D1/D3 不渲染）', () => {
    const w = mountOptions()
    expect(w.text()).toContain('恢复到哪')
    expect(w.text()).toContain('已存在文件')
    expect(w.text()).not.toContain('元数据')
    expect(w.text()).not.toContain('高级选项')
  })

  it('⑦ submitting 透传到提交按钮 loading（确认门进行中防重复提交）', () => {
    const w = mountOptions({ submitting: true })
    const submit = w.findAll('button').find((b) => b.text().includes('提交'))
    expect(submit!.classes()).toContain('n-button--loading')
  })
})
