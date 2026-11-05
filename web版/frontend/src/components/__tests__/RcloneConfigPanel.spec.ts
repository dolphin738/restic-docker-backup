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
import { flushPromises, mount } from '@vue/test-utils'
import { NConfigProvider, NMessageProvider, NPopconfirm, NRadioGroup, NSelect } from 'naive-ui'
import { createPinia, setActivePinia } from 'pinia'
import { h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'

import PasswordEntryModal from '@/components/PasswordEntryModal.vue'
import PasswordInput from '@/components/PasswordInput.vue'
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
  conf_encrypted: false,
  conf_locked: false,
  configs: [
    {
      remote: 'mycrypt',
      type: 'crypt',
      managed: true,
      params: { remote: 'mywebdav:bucket', filename_encoding: 'base32' },
      has_credentials: { password: true, password2: false },
      // 方案 A sidecar 形态记录：password 已知为混淆形态，password2 未知（null）
      credential_obscured: { password: true, password2: null },
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

/** 形态未知的快照（conf 被外部维护过 / 非本服务写入）。 */
const SNAPSHOT_UNKNOWN_FORM: RcloneConfigsOut = {
  ...SNAPSHOT,
  configs: SNAPSHOT.configs.map((c) => (c.remote === 'mycrypt' ? { ...c, credential_obscured: { password: null, password2: null } } : c)),
}

/** 明文形态快照（历史段：conf 里是明文，rclone 实际不可用）。 */
const SNAPSHOT_PLAIN_FORM: RcloneConfigsOut = {
  ...SNAPSHOT,
  configs: SNAPSHOT.configs.map((c) =>
    c.remote === 'mycrypt' ? { ...c, credential_obscured: { password: false, password2: null } } : c,
  ),
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
    // P6 新增：onMounted 会并发拉取加密三态，未 mock 会让 fetch 落空、挂载竞态失败
    vi.spyOn(repoApi, 'getRcloneConfEncryption').mockResolvedValue({
      conf_encrypted: false,
      conf_locked: false,
      password_present: false,
      conf_path: '/etc/rclone/rclone.conf',
      pass_path: '/repos/rclone.pass',
    })
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
    await flushPromises()
    await nextTick()
    await flushPromises()

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
    await flushPromises()
    await nextTick()
    await flushPromises()

    const buttons = [...w.element.querySelectorAll('button')].map((b) => b.textContent?.trim())
    // 两个段：crypt 段有「编辑」+「删除」，drive 段只有「删除」
    expect(buttons.filter((b) => b === zhCN.settings.rclone.edit)).toHaveLength(1)
    expect(buttons.filter((b) => b === zhCN.settings.rclone.delete)).toHaveLength(2)
    expect(w.findAllComponents(NPopconfirm)).toHaveLength(2)
  })

  it('删除走确认门并调用 deleteRcloneConfig', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const confirms = w.findAllComponents(NPopconfirm)
    await confirms[1]!.vm.$emit('positive-click')
    await nextTick()
    await flushPromises()

    expect(repoApi.deleteRcloneConfig).toHaveBeenCalledWith('gdrive')
  })

  it('编辑态预填非凭据字段且密码不预填', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const editBtn = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.edit,
    )
    expect(editBtn).toBeTruthy()
    editBtn!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

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
    await flushPromises()

    const genButtons = [...w.element.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === zhCN.common.generatePassword,
    )
    expect(genButtons.length).toBeGreaterThan(0)
    genButtons[0]!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

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
    await flushPromises()
    const filled = ([...w.element.querySelectorAll('input')] as HTMLInputElement[]).find(
      (i) => i.type === 'password' && i.value === secret,
    )
    expect(filled).toBeTruthy()
  })

  it('未选底层远端时提交被前端拦截（不发请求）', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'newone')
    await nextTick()
    await flushPromises()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(repoApi.createRcloneConfig).not.toHaveBeenCalled()
  })

  it('新增提交调用 createRcloneConfig（remote 拼成 name:path；默认按明文提交）', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'newone')
    // 底层候选来自列表快照（gdrive 段也在册：crypt 可叠在网盘段上）
    pickCryptRemote(w, 'gdrive')
    fillInput(w, zhCN.settings.rclone.cryptRemotePathPlaceholder, 'backup/restic')
    await nextTick()
    await flushPromises()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(repoApi.createRcloneConfig).toHaveBeenCalledTimes(1)
    const [name, payload] = vi.mocked(repoApi.createRcloneConfig).mock.calls[0]!
    expect(name).toBe('newone')
    expect(payload.type).toBe('crypt')
    expect(payload.params.remote).toBe('gdrive:backup/restic')
    expect(payload.params.filename_encoding).toBe('base64')
    // 裁决 17：落盘形态不再是选项（恒为混淆），只声明提交值形态；默认明文 → 后端 --obscure
    expect('store_form' in payload).toBe(false)
    expect(payload.value_obscured).toBe(false)
  })

  it('新增：填密码 + 选「混淆串」→ value_obscured=true（原样落盘，不二次混淆）', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'obscuredone')
    pickCryptRemote(w, 'gdrive')
    // 新增态密码框 placeholder 为空串（只有编辑态才有「留空保持不变」）→ 直接驱动组件
    w.findAllComponents(PasswordInput).at(0)!.vm.$emit('update:modelValue', 'OBSCURED-TOKEN')
    await nextTick()
    await flushPromises()

    const select = w
      .findAllComponents(NSelect)
      .find((c) => (c.props('options') ?? []).some((o) => o.value === 'obscured'))
    expect(select).toBeTruthy()
    expect(select!.props('disabled')).toBe(false)
    select!.vm.$emit('update:value', 'obscured')
    await nextTick()
    await flushPromises()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(repoApi.createRcloneConfig).toHaveBeenCalledTimes(1)
    const [, payload] = vi.mocked(repoApi.createRcloneConfig).mock.calls[0]!
    expect(payload.value_obscured).toBe(true)
    expect(payload.params.password).toBe('OBSCURED-TOKEN')
  })

  it('本地目录模式：提交 remote = 绝对路径（crypt 可包裹本地目录）', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'localcrypt')
    w.findComponent(NRadioGroup).vm.$emit('update:value', 'local')
    await nextTick()
    await flushPromises()

    // 本地模式换成目录树 + 路径输入（DirPickerInline 的输入框）
    fillInput(w, zhCN.settings.rclone.cryptLocalPlaceholder, '/repo/encrypted')
    await nextTick()
    await flushPromises()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(repoApi.createRcloneConfig).toHaveBeenCalledTimes(1)
    const [name, payload] = vi.mocked(repoApi.createRcloneConfig).mock.calls[0]!
    expect(name).toBe('localcrypt')
    expect(payload.params.remote).toBe('/repo/encrypted')
  })

  it('本地目录模式：非绝对路径被前端拦截', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()

    fillInput(w, zhCN.settings.rclone.remoteNamePlaceholder, 'badlocal')
    w.findComponent(NRadioGroup).vm.$emit('update:value', 'local')
    await nextTick()
    await flushPromises()

    fillInput(w, zhCN.settings.rclone.cryptLocalPlaceholder, 'backup/restic')
    await nextTick()
    await flushPromises()

    clickSubmit(w, zhCN.settings.rclone.submitNew)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(repoApi.createRcloneConfig).not.toHaveBeenCalled()
  })

  // ---------------- 落盘形态（裁决 17）：恒为混淆 + 明文段修正 + 导出原密码 ----------------

  function clickEdit(w: ReturnType<typeof mountPanel>): void {
    const editBtn = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.edit,
    )
    expect(editBtn).toBeTruthy()
    editBtn!.dispatchEvent(new Event('click'))
  }

  it('编辑态：徽标显示已存形态（混淆 = 可用），不动密码保存 = value_obscured false', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(textOf(w)).toContain(
      zhCN.settings.rclone.storedForm.replace('{form}', zhCN.settings.rclone.storedObscured),
    )

    clickSubmit(w, zhCN.settings.rclone.submitEdit)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(repoApi.updateRcloneConfig).toHaveBeenCalledTimes(1)
    const [, payload] = vi.mocked(repoApi.updateRcloneConfig).mock.calls[0]!
    // 目标形态字段已删除：落盘恒为混淆，请求体只声明「提交值形态」
    expect('store_form' in payload).toBe(false)
    expect(payload.value_obscured).toBe(false)
  })

  it('编辑态：密码形态下拉在未填新值前禁用（没有作用对象）', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const select = w
      .findAllComponents(NSelect)
      .find((c) => (c.props('options') ?? []).some((o) => o.value === 'plain'))
    expect(select).toBeTruthy()
    expect(select!.props('disabled')).toBe(true)
    expect(textOf(w)).toContain(zhCN.settings.rclone.passwordFormDisabled)
  })

  it('填新密码 + 选「混淆串」→ 提交 value_obscured=true（原样落盘，不二次混淆）', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    fillInput(w, zhCN.settings.rclone.passwordKeepHint, 'OBS-t0ken-from-other-conf')
    await nextTick()
    await flushPromises()

    const select = w
      .findAllComponents(NSelect)
      .find((c) => (c.props('options') ?? []).some((o) => o.value === 'obscured'))
    expect(select).toBeTruthy()
    expect(select!.props('disabled')).toBe(false)
    select!.vm.$emit('update:value', 'obscured')
    await nextTick()
    await flushPromises()

    clickSubmit(w, zhCN.settings.rclone.submitEdit)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const [, payload] = vi.mocked(repoApi.updateRcloneConfig).mock.calls[0]!
    expect(payload.value_obscured).toBe(true)
    expect(payload.params.password).toBe('OBS-t0ken-from-other-conf')
  })

  it('编辑态：形态未知 → 徽标「未知」+ 提示填密码重建记录', async () => {
    vi.mocked(repoApi.listRcloneConfigs).mockResolvedValue(SNAPSHOT_UNKNOWN_FORM)
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(textOf(w)).toContain(
      zhCN.settings.rclone.storedForm.replace('{form}', zhCN.settings.rclone.storedUnknown),
    )
    expect(textOf(w)).toContain(zhCN.settings.rclone.formUnknownHint)
  })

  it('明文段：出现「修正为混淆」，点击 → update 不带凭据（后端就地 --obscure）', async () => {
    vi.mocked(repoApi.listRcloneConfigs).mockResolvedValue(SNAPSHOT_PLAIN_FORM)
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(textOf(w)).toContain(
      zhCN.settings.rclone.storedForm.replace('{form}', zhCN.settings.rclone.storedPlain),
    )
    const btn = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.plainRepairBtn,
    )
    expect(btn).toBeTruthy()
    btn!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(repoApi.updateRcloneConfig).toHaveBeenCalledTimes(1)
    const [, payload] = vi.mocked(repoApi.updateRcloneConfig).mock.calls[0]!
    expect(payload.value_obscured).toBe(false)
    expect(payload.params.password).toBeUndefined() // 不填密码也能修
  })

  it('已混淆段：不出现「修正为混淆」按钮', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const btn = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.plainRepairBtn,
    )
    expect(btn).toBeUndefined()
  })

  it('「如何找回密码」：编辑态只有一个入口，弹窗只给命令示例、不含任何凭据值', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const btns = [...w.element.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.recoverBtn,
    )
    // 两个密码字段（password / password2）共用一个入口——不再有按 key 分的两个导出按钮
    expect(btns).toHaveLength(1)
    btns[0]!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    // 弹窗内容 teleport 到 body：读文本（不是查元素），断言只给命令示例
    const modalText = document.body.textContent ?? ''
    expect(modalText).toContain('rclone reveal')
    expect(modalText).toContain('config dump')
    // 凭据零回显：弹窗不含任何密码值，前端也不再存在导出调用
    expect(modalText).not.toContain('plain-secret')
    expect(repoApi).not.toHaveProperty('revealRcloneCredential')

    w.unmount()
  })

  it('「如何找回密码」：两条命令各带复制按钮，点击复制对应命令原文', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })

    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    clickEdit(w)
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const open = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.recoverBtn,
    )
    open!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    // 弹窗 teleport 到 body → 按弹窗容器定位「复制」按钮（两个），避免其它弹窗残留干扰
    const box = document.body.querySelector('.rclone-panel__recover')
    expect(box).toBeTruthy()
    const copyBtns = [...box!.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === zhCN.common.copy,
    )
    expect(copyBtns).toHaveLength(2)

    copyBtns[0]!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    expect(writeText).toHaveBeenCalledTimes(1)
    const first = String(writeText.mock.calls[0]![0])
    expect(first).toContain('config dump')
    expect(first).toContain(zhCN.settings.rclone.recoverConfPlaceholder)

    copyBtns[1]!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    const second = String(writeText.mock.calls[1]![0])
    expect(second).toContain('rclone reveal')
    expect(second).toContain(zhCN.settings.rclone.recoverValuePlaceholder)

    vi.unstubAllGlobals()
    w.unmount()
  })

  it('「如何找回密码」：新增态不出现（无已存凭据）', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const btn = [...w.element.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === zhCN.settings.rclone.recoverBtn,
    )
    expect(btn).toBeUndefined()
  })
})

// ---------------------------------------------------------------- 删除保护（删除依赖与快照可用性保护方案 P0，裁决 1/2）

const SNAPSHOT_IN_USE: RcloneConfigsOut = {
  ...SNAPSHOT,
  configs: SNAPSHOT.configs.map((c) => (c.remote === 'mycrypt' ? { ...c, used_by_repos: 1 } : c)),
}

function buttonOf(w: ReturnType<typeof mountPanel>, label: string): HTMLButtonElement | undefined {
  return ([...w.element.querySelectorAll('button')] as HTMLButtonElement[]).find(
    (b) => b.textContent?.trim() === label,
  )
}

describe('RcloneConfigPanel 删除保护', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    vi.spyOn(repoApi, 'listRcloneConfigs').mockResolvedValue(SNAPSHOT)
    // P6 新增：onMounted 会并发拉取加密三态，未 mock 会让 fetch 落空、挂载竞态失败
    vi.spyOn(repoApi, 'getRcloneConfEncryption').mockResolvedValue({
      conf_encrypted: false,
      conf_locked: false,
      password_present: false,
      conf_path: '/etc/rclone/rclone.conf',
      pass_path: '/repos/rclone.pass',
    })
    vi.spyOn(repoApi, 'deleteRcloneConfig').mockResolvedValue({
      remote: 'gdrive',
      type: 'drive',
      conf_path: '/etc/rclone/rclone.conf',
      action: 'delete',
      remotes_after: ['mycrypt'],
    })
  })

  it('无仓库引用：删除按钮可点，且不出现查看占用入口', async () => {
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(buttonOf(w, zhCN.settings.rclone.usage)).toBeUndefined()
    const del = buttonOf(w, zhCN.settings.rclone.delete)
    expect(del).toBeTruthy()
    expect(del!.disabled).toBe(false)
  })

  it('有仓库引用：删除按钮禁用，改为查看占用入口', async () => {
    vi.mocked(repoApi.listRcloneConfigs).mockResolvedValue(SNAPSHOT_IN_USE)
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    const usage = buttonOf(w, zhCN.settings.rclone.usage)
    expect(usage).toBeTruthy()
    const del = buttonOf(w, zhCN.settings.rclone.delete)
    expect(del).toBeTruthy()
    expect(del!.disabled).toBe(true)
  })

  it('查看占用：点击后按 remote 拉取明细（清单渲染在弹窗内）', async () => {
    vi.mocked(repoApi.listRcloneConfigs).mockResolvedValue(SNAPSHOT_IN_USE)
    const spy = vi.spyOn(repoApi, 'getRcloneRemoteUsage').mockResolvedValue({
      remote: 'mycrypt',
      repos: [{ id: 'x1', name: '加密仓' }],
      dependent_remotes: [],
      crypt: true,
    })
    const w = mountPanel()
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    buttonOf(w, zhCN.settings.rclone.usage)!.dispatchEvent(new Event('click'))
    await nextTick()
    await flushPromises()
    await nextTick()
    await flushPromises()

    expect(spy).toHaveBeenCalledWith('mycrypt')
  })
})

// ---------------------------------------------------------------- conf 加密面板（真机缺陷 1 / 2 回归）

const ENC_STATUS_PLAIN = {
  conf_encrypted: false,
  conf_locked: false,
  password_present: false,
  conf_path: '/etc/rclone/rclone.conf',
  pass_path: '/repos/rclone.pass',
}

const ENC_STATUS_ENCRYPTED = { ...ENC_STATUS_PLAIN, conf_encrypted: true, password_present: true }

/** 加密卡片范围内的写按钮文案（避免与列表区/表单区按钮混淆）。 */
function confEncButtons(w: ReturnType<typeof mountPanel>): string[] {
  const labels = [
    zhCN.settings.rclone.confEnc.enableBtn,
    zhCN.settings.rclone.confEnc.rekeyBtn,
    zhCN.settings.rclone.confEnc.disableBtn,
    zhCN.settings.rclone.confEnc.unlockBtn,
  ]
  return ([...w.element.querySelectorAll('button')] as HTMLButtonElement[])
    .map((b) => b.textContent?.trim() ?? '')
    .filter((text) => labels.includes(text))
}

/** 面板挂载后等两轮微任务（onMounted 并发拉三态 + 列表）。 */
async function mountReady(): Promise<void> {
  await nextTick()
  await flushPromises()
  await nextTick()
  await flushPromises()
}

describe('RcloneConfigPanel conf 加密面板', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    vi.spyOn(repoApi, 'listRcloneConfigs').mockResolvedValue(SNAPSHOT)
  })

  it('缺陷 1：三态读取失败时一个写按钮都不出现，改为可操作的 alert', async () => {
    vi.spyOn(repoApi, 'getRcloneConfEncryption').mockRejectedValue(new Error('三态接口 500'))
    const w = mountPanel()
    await mountReady()

    expect(confEncButtons(w)).toHaveLength(0)
    const text = textOf(w)
    expect(text).toContain(zhCN.settings.rclone.confEnc.stateUnknown)
    // alert 里带后端 message，且给出「重试」这个可操作出口
    expect(text).toContain('三态接口 500')
    expect(buttonOf(w, zhCN.common.retry)).toBeTruthy()
    w.unmount()
  })

  it('缺陷 1：三态就绪且未加密时只出现「生成并启用」（单个按钮，无转圈重影）', async () => {
    vi.spyOn(repoApi, 'getRcloneConfEncryption').mockResolvedValue(ENC_STATUS_PLAIN)
    const w = mountPanel()
    await mountReady()

    expect(confEncButtons(w)).toEqual([zhCN.settings.rclone.confEnc.enableBtn])
    // 状态就绪时没有「状态未知」那条 alert（徽标此时是「未加密」）
    expect(textOf(w)).not.toContain(zhCN.settings.rclone.confEnc.stateUnknown)
    w.unmount()
  })

  it('缺陷 2：换口令的新口令段能生成并填入明文，旧口令段没有生成入口', async () => {
    vi.spyOn(repoApi, 'getRcloneConfEncryption').mockResolvedValue(ENC_STATUS_ENCRYPTED)
    const w = mountPanel()
    await mountReady()

    // 进 rekey：先旧口令段
    buttonOf(w, zhCN.settings.rclone.confEnc.rekeyBtn)!.dispatchEvent(new Event('click'))
    await mountReady()

    const modal = () => w.findComponent(PasswordEntryModal)
    const genButtons = () =>
      ([...document.body.querySelectorAll('button')] as HTMLButtonElement[]).filter(
        (b) => b.textContent?.trim() === zhCN.common.generatePassword,
      )

    expect(modal().props('show')).toBe(true)
    // 旧口令段：默认不出现生成入口（旧口令是用户已有的，不该生成）
    expect(modal().props('showGenerate')).toBe(false)
    expect(genButtons()).toHaveLength(0)

    // 旧口令提交 → 进新口令段
    modal().vm.$emit('submit-custom', 'OLD-PASS-1234')
    await mountReady()

    expect(modal().props('showGenerate')).toBe(true)
    const genBtn = genButtons()[0]
    expect(genBtn).toBeTruthy()

    // 点生成 → 组件进入生成态（generated 有值 = 一次性明文，32 位 = 全站统一长度）
    genBtn!.dispatchEvent(new Event('click'))
    await mountReady()
    const generated = modal().props('generated') as string | null
    expect(generated).toHaveLength(32)

    // 生成态确认 = 用这份明文执行轮换（新口令确实被带进请求，不是空提交）
    const rekey = vi
      .spyOn(repoApi, 'rekeyRcloneConfEncryption')
      .mockResolvedValue({ code: 'ok', password_once: null })
    modal().vm.$emit('confirm-generated')
    await mountReady()
    expect(rekey).toHaveBeenCalledWith({ old_password: 'OLD-PASS-1234', new_password: generated })

    w.unmount()
  })
})
