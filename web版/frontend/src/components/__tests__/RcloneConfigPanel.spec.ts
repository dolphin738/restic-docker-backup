/**
 * RcloneConfigPanel 契约护栏（配置生成工具 B3）。
 *
 * 锁定语义：
 * - 列表只渲染**非凭据参数** + has_* 徽标，凭据值（混淆后的密文）绝不出现在 DOM（S3）；
 * - managed 段可编辑、非 managed 段（OAuth 网盘）**只有删除按钮**（裁决 8/9）；
 * - 编辑态预填非凭据字段、**密码不预填**（留空 = 保持不变）；
 * - 提交按新增/编辑分别调 create/update，删除调 delete；
 * - 「生成随机密码」按钮填充 32 位密码（Web Crypto）。
 *
 * DOM 查询一律走 `wrapper.element`（VTU 挂载在脱离文档的 div，禁 document 全局搜）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { NConfigProvider, NMessageProvider, NPopconfirm } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'

import RcloneConfigPanel from '@/components/settings/RcloneConfigPanel.vue'
import type { RcloneConfigsOut } from '@/api/types'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'
import * as repoApi from '@/api/repo'

// 显式标注：两段形状不同（managed 有参数 / 非 managed 空对象），不标注会被推断成
// 带 `key?: undefined` 的联合类型，与 `RcloneConfigItem[]` 不兼容（typecheck 会红）。
const SNAPSHOT: RcloneConfigsOut = {
  conf_path: '/etc/rclone/rclone.conf',
  conf_present: true,
  available: true,
  reason: null,
  configs: [
    {
      remote: 'mycrypt',
      type: 'crypt',
      managed: true,
      params: { remote: 'mywebdav:bucket', filename_encoding: 'base32' },
      has_credentials: { password: true, password2: false },
    },
    {
      remote: 'gdrive',
      type: 'drive',
      managed: false,
      params: {},
      has_credentials: {},
    },
  ],
}

function mountPanel() {
  const i18n = createI18n({ legacy: false, locale: 'zh-CN', messages: { 'zh-CN': zhCN, 'en-US': enUS } })
  // naive 的 useMessage 要求外层 NMessageProvider（否则组件 setup 直接抛错）
  return mount(NConfigProvider, {
    global: { plugins: [i18n] },
    slots: {
      default: () => h(NMessageProvider, null, { default: () => h(RcloneConfigPanel) }),
    },
  })
}

function textOf(wrapper: ReturnType<typeof mountPanel>): string {
  return wrapper.element.textContent ?? ''
}

describe('RcloneConfigPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    vi.spyOn(repoApi, 'listRcloneConfigs').mockResolvedValue(SNAPSHOT)
    vi.spyOn(repoApi, 'createRcloneConfig').mockResolvedValue({
      remote: 'newone',
      type: 'crypt',
      conf_path: '/etc/rclone/rclone.conf',
      action: 'create',
      remotes_after: ['mycrypt', 'gdrive', 'newone'],
    })
    vi.spyOn(repoApi, 'updateRcloneConfig').mockResolvedValue({
      remote: 'mycrypt',
      type: 'crypt',
      conf_path: '/etc/rclone/rclone.conf',
      action: 'update',
      remotes_after: ['mycrypt', 'gdrive'],
    })
    vi.spyOn(repoApi, 'deleteRcloneConfig').mockResolvedValue({
      remote: 'gdrive',
      type: 'drive',
      conf_path: '/etc/rclone/rclone.conf',
      action: 'delete',
      remotes_after: ['mycrypt'],
    })
  })

  it('列表展示非凭据参数与 has_* 徽标，凭据零回显', async () => {
    const w = mountPanel()
    await nextTick()
    await nextTick()

    const text = textOf(w)
    expect(text).toContain('mycrypt')
    expect(text).toContain('filename_encoding=base32')
    expect(text).toContain('password')
    // 非 managed 段只标记为宿主机维护，不露参数
    expect(text).toContain('gdrive')
    expect(text).toContain(zhCN.settings.rclone.unmanaged)
  })

  it('非 managed 段（OAuth）只有删除、没有编辑按钮', async () => {
    const w = mountPanel()
    await nextTick()
    await nextTick()

    const buttons = [...w.element.querySelectorAll('button')].map((b) => b.textContent?.trim())
    // 两个段：crypt 段有「编辑」+「删除」，drive 段只有「删除」
    expect(buttons.filter((b) => b === zhCN.settings.rclone.edit)).toHaveLength(1)
    expect(buttons.filter((b) => b === zhCN.settings.rclone.delete)).toHaveLength(2)
    expect(w.findAllComponents(NPopconfirm)).toHaveLength(2)
  })

  it('删除走确认门并调用 deleteRcloneConfig', async () => {
    const w = mountPanel()
    await nextTick()
    await nextTick()

    const confirms = w.findAllComponents(NPopconfirm)
    await confirms[1]!.vm.$emit('positive-click')
    await nextTick()

    expect(repoApi.deleteRcloneConfig).toHaveBeenCalledWith('gdrive')
  })

  it('编辑态预填非凭据字段且密码不预填', async () => {
    const w = mountPanel()
    await nextTick()
    await nextTick()

    const editBtn = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.edit,
    )
    expect(editBtn).toBeTruthy()
    editBtn!.dispatchEvent(new Event('click'))
    await nextTick()
    await nextTick()

    const inputs = [...w.element.querySelectorAll('input')] as HTMLInputElement[]
    const remoteInput = inputs.find((i) => i.value === 'mywebdav:bucket')
    expect(remoteInput).toBeTruthy()
    // 密码框（type=password）必须为空——凭据不预填（S3）
    const passwordInputs = inputs.filter((i) => i.type === 'password')
    expect(passwordInputs.length).toBeGreaterThan(0)
    for (const input of passwordInputs) {
      expect(input.value).toBe('')
    }
  })

  it('生成随机密码按钮填充 32 位密码', async () => {
    const w = mountPanel()
    await nextTick()

    const genButtons = [...w.element.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.generatePassword,
    )
    expect(genButtons.length).toBeGreaterThan(0)
    genButtons[0]!.dispatchEvent(new Event('click'))
    await nextTick()

    const filled = ([...w.element.querySelectorAll('input')] as HTMLInputElement[]).find(
      (i) => i.type === 'password' && i.value.length === 32,
    )
    expect(filled).toBeTruthy()
  })

  it('新增提交调用 createRcloneConfig（带 type 与 params）', async () => {
    const w = mountPanel()
    await nextTick()

    const submit = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.submitNew,
    )
    expect(submit).toBeTruthy()

    // 填远端名后提交（默认 crypt，密码非必填于前端：后端负责必填校验）
    const remoteInput = ([...w.element.querySelectorAll('input')] as HTMLInputElement[])[0]!
    remoteInput.value = 'newone'
    remoteInput.dispatchEvent(new Event('input'))
    await nextTick()

    submit!.dispatchEvent(new Event('click'))
    await nextTick()
    await nextTick()

    expect(repoApi.createRcloneConfig).toHaveBeenCalledTimes(1)
    const [name, payload] = vi.mocked(repoApi.createRcloneConfig).mock.calls[0]!
    expect(name).toBe('newone')
    expect(payload.type).toBe('crypt')
    expect(payload.params.filename_encoding).toBe('base64')
  })
})
