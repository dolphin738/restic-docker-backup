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
import { NConfigProvider, NMessageProvider, NPopconfirm, NRadioGroup, NSelect } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'

import SecretReveal from '@/components/SecretReveal.vue'
import RcloneConfigPanel from '@/components/settings/RcloneConfigPanel.vue'
import type { RcloneConfigsOut } from '@/api/types'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'
import * as repoApi from '@/api/repo'

// 本地目录模式会挂 DirPickerInline（内部走 E33）——测试不发真实请求
vi.mock('@/api/fs', () => ({ browseFs: vi.fn().mockResolvedValue({ path: '/', items: [], mounts: [], next_cursor: null }) }))

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

/** 按 placeholder 定位输入框（不用 DOM 下标：NSelect 内部也渲染 input，下标会漂）。 */
function fillInput(w: ReturnType<typeof mountPanel>, placeholder: string, value: string): void {
  const input = ([...w.element.querySelectorAll('input')] as HTMLInputElement[]).find(
    (i) => i.placeholder === placeholder,
  )
  expect(input).toBeTruthy()
  input!.value = value
  input!.dispatchEvent(new Event('input'))
}

function clickSubmit(w: ReturnType<typeof mountPanel>, label: string): void {
  const btn = [...w.element.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)
  expect(btn).toBeTruthy()
  btn!.dispatchEvent(new Event('click'))
}

/** 选底层远端：naive Select 需点开再点选项（选项 teleport 到 body），测试直接发 v-model 事件。 */
function pickCryptRemote(w: ReturnType<typeof mountPanel>, value: string): void {
  const select = w
    .findAllComponents(NSelect)
    .find((c) => c.props('placeholder') === zhCN.settings.rclone.cryptRemoteNamePlaceholder)
  expect(select).toBeTruthy()
  select!.vm.$emit('update:value', value)
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
    // 底层目标已拆两段：`mywebdav:bucket` → 远端（NSelect 选中态）+ 路径输入框 value=bucket
    const pathInput = inputs.find(
      (i) => i.placeholder === zhCN.settings.rclone.cryptRemotePathPlaceholder,
    )
    expect(pathInput).toBeTruthy()
    expect(pathInput!.value).toBe('bucket')
    const remoteSelect = w
      .findAllComponents(NSelect)
      .find((c) => c.props('placeholder') === zhCN.settings.rclone.cryptRemoteNamePlaceholder)
    expect(remoteSelect).toBeTruthy()
    expect(remoteSelect!.props('value')).toBe('mywebdav')
    // 密码框（type=password）必须为空——凭据不预填（S3）
    const passwordInputs = inputs.filter((i) => i.type === 'password')
    expect(passwordInputs.length).toBeGreaterThan(0)
    for (const input of passwordInputs) {
      expect(input.value).toBe('')
    }
  })

  it('生成随机密码：先弹一次性明文（32 位），确认后才填入密码栏', async () => {
    const w = mountPanel()
    await nextTick()

    const genButtons = [...w.element.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === zhCN.common.generatePassword,
    )
    expect(genButtons.length).toBeGreaterThan(0)
    genButtons[0]!.dispatchEvent(new Event('click'))
    await nextTick()
    await nextTick()

    // ① 弹窗出现，明文 32 位（全站统一长度），且此刻**尚未**写入表单
    const reveal = w.findComponent(SecretReveal)
    expect(reveal.exists()).toBe(true)
    const secret = reveal.props('secret') as string
    expect(secret).toHaveLength(32)
    const passwordInputs = [...w.element.querySelectorAll('input')] as HTMLInputElement[]
    expect(passwordInputs.filter((i) => i.type === 'password' && i.value.length === 32)).toHaveLength(0)

    // ② 确认（我已备份）→ 写入当前密码栏并关闭
    reveal.vm.$emit('confirm')
    await nextTick()
    const filled = ([...w.element.querySelectorAll('input')] as HTMLInputElement[]).find(
      (i) => i.type === 'password' && i.value === secret,
    )
    expect(filled).toBeTruthy()
  })

  it('未选底层远端时提交被前端拦截（不发请求）', async () => {
    const w = mountPanel()
    await nextTick()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'newone')
    await nextTick()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await nextTick()

    expect(repoApi.createRcloneConfig).not.toHaveBeenCalled()
  })

  it('新增提交调用 createRcloneConfig（remote 拼成 name:path）', async () => {
    const w = mountPanel()
    await nextTick()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'newone')
    // 底层候选来自列表快照（gdrive 段也在册：crypt 可叠在网盘段上）
    pickCryptRemote(w, 'gdrive')
    fillInput(w, zhCN.settings.rclone.cryptRemotePathPlaceholder, 'backup/restic')
    await nextTick()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await nextTick()

    expect(repoApi.createRcloneConfig).toHaveBeenCalledTimes(1)
    const [name, payload] = vi.mocked(repoApi.createRcloneConfig).mock.calls[0]!
    expect(name).toBe('newone')
    expect(payload.type).toBe('crypt')
    expect(payload.params.remote).toBe('gdrive:backup/restic')
    expect(payload.params.filename_encoding).toBe('base64')
  })

  it('本地目录模式：提交 remote = 绝对路径（crypt 可包裹本地目录）', async () => {
    const w = mountPanel()
    await nextTick()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'localcrypt')
    w.findComponent(NRadioGroup).vm.$emit('update:value', 'local')
    await nextTick()

    // 本地模式换成目录树 + 路径输入（DirPickerInline 的输入框）
    fillInput(w, zhCN.settings.rclone.cryptLocalPlaceholder, '/repo/encrypted')
    await nextTick()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await nextTick()

    expect(repoApi.createRcloneConfig).toHaveBeenCalledTimes(1)
    const [name, payload] = vi.mocked(repoApi.createRcloneConfig).mock.calls[0]!
    expect(name).toBe('localcrypt')
    expect(payload.params.remote).toBe('/repo/encrypted')
  })

  it('本地目录模式：非绝对路径被前端拦截', async () => {
    const w = mountPanel()
    await nextTick()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'badlocal')
    w.findComponent(NRadioGroup).vm.$emit('update:value', 'local')
    await nextTick()

    fillInput(w, zhCN.settings.rclone.cryptLocalPlaceholder, 'backup/restic')
    await nextTick()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await nextTick()

    expect(repoApi.createRcloneConfig).not.toHaveBeenCalled()
  })
})
