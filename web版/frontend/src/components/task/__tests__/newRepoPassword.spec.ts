/**
 * 向导「新建仓库」步骤：一次性密码呈现护栏（2026-11-02）。
 *
 * 锁定语义：建仓成功且密码为后端生成时，一次性明文必须走 **可复制的弹窗**
 * （NewRepoStep 内嵌 PasswordEntryModal 生成态 = SecretReveal），**不得**退回
 * 不可复制的 message.warning toast——32 位密码手抄必错，且 toast 无法选中文本。
 * 确认关闭后明文清出内存（红线 S3：不落 localStorage / 草稿）。
 *
 * 两条实现锚点（防止回归手抄）：
 * - NewRepoStep.vue 源码不得再出现 message.warning(t('wizard.newRepoPassword…
 * - PasswordEntryModal 生成态含 common.copy 按钮（SecretReveal 唯一实现）
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NMessageProvider } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { h } from 'vue'

import PasswordEntryModal from '@/components/PasswordEntryModal.vue'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'

const stepSource = readFileSync('src/components/task/NewRepoStep.vue', 'utf-8')

function mountModal(generated: string | null) {
  const i18n = createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN, 'en-US': enUS } })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return mount(NMessageProvider, {
    global: { plugins: [i18n, createPinia(), [VueQueryPlugin, { queryClient }]] },
    // n-modal 默认 teleport 到 body：挂到真实 document 再查 body 里的内容
    attachTo: document.body,
    slots: {
      default: () =>
        h(PasswordEntryModal, {
          show: generated !== null,
          'onUpdate:show': (v: boolean) => {
            if (!v) {
              state.generated = null
              state.show = false
            }
          },
          title: '仓库密码已生成',
          generatedHint: '只显示这一次',
          confirmLabel: '我已备份，关闭',
          generated,
          loading: false,
          showGeneratedCancel: false,
          showGenerate: false,
          onConfirmGenerated: () => {
            state.generated = null
            state.show = false
          },
        }),
    },
  })
}

const state: { generated: string | null; show: boolean } = { generated: null, show: false }

describe('向导新建仓库 · 一次性密码呈现', () => {
  it('NewRepoStep 已弃用 toast 呈现（源码无 message.warning(wizard.newRepoPassword…)）', () => {
    expect(stepSource).not.toContain("message.warning(t('wizard.newRepoPassword")
    expect(stepSource).toContain('PasswordEntryModal')
    expect(stepSource).toContain('generatedPassword')
  })

  it('生成态渲染只读明文 + 复制按钮 + 确认关闭；确认后清空明文', async () => {
    setActivePinia(createPinia())
    const w = mountModal('GEN-PW-32-CHARACTERS-aaaaaaaaaaaaaaaaaa')
    // n-modal 经 transition 异步挂到 document.body，等两拍再查
    await new Promise((r2) => setTimeout(r2, 0))
    await new Promise((r2) => setTimeout(r2, 50))
    const root = w.element.parentElement ?? document.body
    const input = root.querySelector('input[readonly]')
    if (!input) {
      // eslint-disable-next-line no-console
      console.error('BODY dump:', document.body.innerHTML.slice(0, 900))
    }
    expect((input as HTMLInputElement)!.value).toBe('GEN-PW-32-CHARACTERS-aaaaaaaaaaaaaaaaaa')
    // 复制按钮（SecretReveal 生成态的标准动作）
    const buttons = [...root.querySelectorAll('button')]
    const copyBtn = buttons.find((b) => b.textContent?.includes('复制'))
    expect(copyBtn, '生成态必须有复制按钮').toBeTruthy()
    // 确认关闭 → 父组件把明文清空（S3：不留内存）
    const confirmBtn = buttons.find((b) => b.textContent?.includes('我已备份'))
    expect(confirmBtn).toBeTruthy()
    await confirmBtn!.click()
    await new Promise((r2) => setTimeout(r2, 0))
    expect(state.generated).toBeNull()
    expect(state.show).toBe(false)
  })
})