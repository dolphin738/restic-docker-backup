/**
 * 一次性明文展示组件（`components/SecretReveal.vue`）测试。
 *
 * 这是「生成密码后只显示这一次」的**唯一呈现实现**——仓库设置密码 / 密钥轮换 /
 * 重建结果 / rclone 配置 / 导出口令全部复用它。因此复制交互与确认/返回语义
 * 在这里锁死：任何一处回归都会同时影响四处以上。
 */
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'

import { createI18n } from 'vue-i18n'

import SecretReveal from '@/components/SecretReveal.vue'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'

// 固定 zh-CN：全局 i18n 实例的 locale 跟随浏览器（测试环境为 en），
// 直接用它会让 t('common.copy') 返回 "Copy"，中文按钮断言全部落空。
const i18n = createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN, 'en-US': enUS } })

const SECRET = 'abcdefghjkmnpqrstuvwxyzABCDEF2345!@#$'

function mountReveal(props: Record<string, unknown> = {}) {
  return mount(
    defineComponent({
      render: () =>
        h(NConfigProvider, null, {
          default: () =>
            h(NMessageProvider, null, {
              default: () =>
                h(SecretReveal, {
                  secret: SECRET,
                  hint: '只显示这一次，请立即备份',
                  confirmLabel: '我已备份',
                  ...props,
                }),
            }),
        }),
    }),
    { global: { plugins: [i18n] } },
  )
}

function buttonByText(wrapper: ReturnType<typeof mountReveal>, text: string): HTMLButtonElement | undefined {
  return [...wrapper.element.querySelectorAll('button')].find((b) => b.textContent?.trim() === text) as
    | HTMLButtonElement
    | undefined
}

describe('SecretReveal', () => {
  it('展示明文提示与只读明文（等宽字体便于抄录）', () => {
    const w = mountReveal()
    expect(w.text()).toContain('只显示这一次，请立即备份')
    const input = w.element.querySelector('input') as HTMLInputElement
    expect(input.value).toBe(SECRET)
    expect(input.readOnly).toBe(true)
    // naive 把 :style 挂在 .n-input 包裹层（不是原生 input）
    const box = w.element.querySelector('.n-input') as HTMLElement
    expect(box.getAttribute('style') ?? '').toContain('font-family')
  })

  it('点「复制」写入剪贴板', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })

    const w = mountReveal()
    const btn = buttonByText(w, '复制')
    expect(btn).toBeTruthy()
    btn!.click()
    await nextTick()
    await nextTick()

    expect(writeText).toHaveBeenCalledWith(SECRET)
    vi.unstubAllGlobals()
  })

  it('剪贴板不可用时不抛异常（降级为手动复制提示）', async () => {
    vi.stubGlobal('navigator', {
      ...navigator,
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })

    const w = mountReveal()
    buttonByText(w, '复制')!.click()
    await nextTick()
    await nextTick()
    // 未抛错即通过（提示走 message.warning）
    expect(w.text()).toContain('我已备份')
    vi.unstubAllGlobals()
  })

  it('确认按钮 emit confirm（副作用归父组件）', async () => {
    const w = mountReveal()
    buttonByText(w, '我已备份')!.click()
    await nextTick()
    expect(w.findComponent(SecretReveal).emitted('confirm')).toHaveLength(1)
  })

  it('showBack 控制「返回」按钮显隐，点击 emit back', async () => {
    const hidden = mountReveal()
    expect(buttonByText(hidden, '取消')).toBeUndefined()

    const shown = mountReveal({ showBack: true })
    const back = buttonByText(shown, '取消')
    expect(back).toBeTruthy()
    back!.click()
    await nextTick()
    expect(shown.findComponent(SecretReveal).emitted('back')).toHaveLength(1)
  })

  it('loading 时确认按钮进入加载态（防重复提交）', () => {
    const w = mountReveal({ loading: true })
    const confirmBtn = buttonByText(w, '我已备份')
    expect(confirmBtn?.className ?? '').toContain('n-button--loading')
  })
})
