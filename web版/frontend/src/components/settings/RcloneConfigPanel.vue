<script setup lang="ts">
/** 设置页「rclone 远端配置」分区面板（配置生成工具 B3）。
 *
 * 上下两区：
 * 1. **已有配置列表**：managed 段（crypt/webdav）展示非凭据参数 + has_* 徽标并可编辑；
 *    非 managed 段（OAuth 网盘 / 宿主机手工配置）只露名字与 type，**仅可删除**
 *    （裁决 8：删除全段放开；裁决 9：编辑随注册表扩展）。删除走 n-popconfirm 二次确认，
 *    OAuth 段额外加重提示。
 * 2. **生成/编辑表单**：类型 Select 切换字段组；密码框旁「生成 32 位随机密码」
 *    （Web Crypto，客户端生成，不经服务端）；编辑态预填非凭据字段、密码留空 = 保持不变。
 *
 * 落盘形态（裁决 17，2026-10-28 收敛）：**conf 里的凭据恒为 rclone 混淆产物**。
 * 真机取证：明文落盘对 crypt/webdav 一律不可用（`failed to decrypt password: input
 * too short…`）——那不是"安全性差一点"，是把该段直接写成坏配置。所以「目标存储形态」
 * 不再是选项；用户只需回答一个问题：**我填的这串是明文还是混淆串**（`passwordForm`，
 * 唯一控件）。明文 → `--obscure` 混淆一次后落盘；混淆串 → `--no-obscure` 原样落盘
 * （不二次混淆：二次混淆会让 rclone 换一套加密命名空间，不报错但仓库看起来是空的）。
 *
 * 历史明文段（早期版本 / 宿主机手写 conf）在编辑态显示「明文（不可用）」徽标，并提供
 * **「修正为混淆」**：不填密码也能就地 `--obscure`，修完即可用。
 *
 * 想拿回原密码 → 「如何找回密码」只弹**宿主机自助命令示例**（`rclone config dump` +
 * `rclone reveal <混淆值>`）：本工具不读、不传、不回显任何凭据（S3 零回显口径不变）。
 *
 * 凭据零回显（S3）：列表只渲染 has_* 布尔与形态布尔，表单密码框在编辑态**永不预填**。
 */
import { computed, h, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { copyWithNotify } from '@/utils/clipboard'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NModal,
  NPopconfirm,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  NTooltip,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import ConfirmGate from '@/components/ConfirmGate.vue'
import DirPickerInline from '@/components/DirPickerInline.vue'
import { IconCheckCircle } from '@/components/icons/icons'
import PasswordEntryModal from '@/components/PasswordEntryModal.vue'
import PasswordInput from '@/components/PasswordInput.vue'
import SecretReveal from '@/components/SecretReveal.vue'
import {
  createRcloneConfig,
  deleteRcloneConfig,
  disableRcloneConfEncryption,
  enableRcloneConfEncryption,
  getRcloneConfEncryption,
  getRcloneRemoteUsage,
  listRcloneConfigs,
  rekeyRcloneConfEncryption,
  repointRcloneRemote,
  unlockRcloneConfEncryption,
  updateRcloneConfig,
} from '@/api/repo'
import type {
  RcloneConfEncryptionOut,
  RcloneConfigItem,
  RcloneConfigsOut,
} from '@/api/types'
import { useRuntimeStore } from '@/stores/runtime'
import { generatePassword } from '@/utils/password'

const { t } = useI18n()
const message = useMessage()
const runtime = useRuntimeStore()

/** 注册表内类型（与后端 GENERATORS 对齐；新增类型需两端同步）。 */
const MANAGED_TYPES = ['crypt', 'webdav'] as const
type ManagedType = (typeof MANAGED_TYPES)[number]

const TYPE_OPTIONS = MANAGED_TYPES.map((value) => ({ label: value, value }))

interface FormState {
  remoteName: string
  type: ManagedType
  /** 底层目标形态：远端叠加（`name:path`）或本地目录（绝对路径，官方支持的纯本地加密）。 */
  cryptRemoteMode: 'remote' | 'local'
  /** 远端指针模式下的「所选远端」；本地模式不用。 */
  cryptRemoteName: string
  /** 远端指针模式 = 远端内路径；本地模式 = 绝对路径（互斥，按 mode 解释）。 */
  cryptRemotePath: string
  password: string
  password2: string
  filenameEncryption: string
  directoryNameEncryption: boolean
  filenameEncoding: string
  webdavUrl: string
  vendor: string
  user: string
  pass: string
  /** WebDAV Bearer 令牌（替代 user/pass 的认证方式；与密码同属凭据，零回显）。 */
  bearerToken: string
  /** 提交值形态（裁决 17 唯一控件）：`plain` = 我填的是明文（服务端混淆一次后落盘）；
   *  `obscured` = 我填的是 rclone 混淆串（原样落盘，不再混淆一次）。
   *  两者落盘结果都是混淆形态——明文 conf 对 crypt/webdav 是坏配置，故不可选。 */
  passwordForm: 'plain' | 'obscured'
}

function emptyForm(): FormState {
  return {
    remoteName: '',
    type: 'crypt',
    cryptRemoteMode: 'remote',
    cryptRemoteName: '',
    cryptRemotePath: '',
    password: '',
    password2: '',
    filenameEncryption: 'standard',
    directoryNameEncryption: true,
    filenameEncoding: 'base64',
    webdavUrl: '',
    vendor: 'other',
    user: '',
    pass: '',
    bearerToken: '',
    // 默认按明文提交：服务端混淆一次后落盘（conf 恒为混淆，rclone 才认）
    passwordForm: 'plain',
  }
}

const loading = ref(false)
const submitting = ref(false)
const snapshot = ref<RcloneConfigsOut | null>(null)
const form = ref<FormState>(emptyForm())
/** 非空 = 编辑态（远端名只读，密码留空 = 保持不变）。 */
const editingRemote = ref<string | null>(null)

/** 一次性明文弹窗：生成密码后**只显示一次**（可复制），确认才写进表单。
 *  收敛口径与仓库设置密码一致（SecretReveal 唯一实现）——crypt 密码丢失 = 数据无法解密，
 *  直接把明文塞进掩码框（旧实现）用户既看不到也复制不了。 */
const generatedField = ref<'password' | 'password2' | 'pass' | null>(null)
const generatedValue = ref('')
const generatedShow = computed({
  get: () => generatedField.value !== null,
  set: (value: boolean) => {
    if (!value) {
      generatedField.value = null
      generatedValue.value = ''
    }
  },
})

const FILENAME_ENCRYPTION_OPTIONS = [
  { label: 'standard', value: 'standard' },
  { label: 'obfuscate', value: 'obfuscate' },
  { label: 'off', value: 'off' },
]
const FILENAME_ENCODING_OPTIONS = [
  { label: 'base32', value: 'base32' },
  { label: 'base64', value: 'base64' },
  { label: 'base32768', value: 'base32768' },
]
const VENDOR_OPTIONS = ['other', 'owncloud', 'nextcloud', 'sharepoint'].map((value) => ({
  label: value,
  value,
}))
/** 密码形态二选一（裁决 17：唯一控件——我填的是明文还是混淆串）。 */
const PASSWORD_FORM_OPTIONS = computed(() => [
  { label: t('settings.rclone.passwordFormPlain'), value: 'plain' },
  { label: t('settings.rclone.passwordFormObscured'), value: 'obscured' },
])

const isEdit = computed(() => editingRemote.value !== null)

/** 是否已填写任一凭据：密码形态只在「本次填了新值」时有意义。 */
const hasCredentialInput = computed(() => {
  const f = form.value
  if (f.type === 'crypt') return !!(f.password || f.password2)
  return !!(f.pass || f.bearerToken)
})

/** 编辑目标的已存凭据形态（方案 A 记录，哈希校验过才算数）。 */
const editingRow = computed(
  () => snapshot.value?.configs.find((c) => c.remote === editingRemote.value) ?? null,
)

/** 主凭据键（crypt = password / webdav = pass）：徽标与「修正」都以它为准。 */
const primaryCredentialKey = computed(() => (form.value.type === 'crypt' ? 'password' : 'pass'))

/** 已存落盘形态：true = 混淆（可用）/ false = 明文（rclone 不可用）/ null = 未知。 */
const storedForm = computed<boolean | null>(
  () => (editingRow.value?.credential_obscured ?? {})[primaryCredentialKey.value] ?? null,
)

/** 落盘形态徽标（编辑态只读展示——目标形态是恒定的，不让用户选）。 */
const storedFormLabel = computed(() => {
  if (storedForm.value === true) return t('settings.rclone.storedObscured')
  if (storedForm.value === false) return t('settings.rclone.storedPlain')
  return t('settings.rclone.storedUnknown')
})
const storedFormTagType = computed<'success' | 'warning' | 'default'>(() => {
  if (storedForm.value === true) return 'success'
  if (storedForm.value === false) return 'warning'
  return 'default'
})

/** 密码形态下拉：没填新值就没有作用对象 → 禁用（而不是"点了会炸"）。 */
const passwordFormDisabled = computed(() => !hasCredentialInput.value)

/** 历史明文段 + 未提交新值 → 提供「修正为混淆」（不填密码就能就地修好）。 */
const canRepairPlain = computed(
  () => isEdit.value && storedForm.value === false && !hasCredentialInput.value,
)

/** 编辑态且该段已配置凭据 → 显示「如何找回密码」入口（纯说明文本，不接触任何凭据值）。 */
const showRecoverHint = computed(() => {
  const row = editingRow.value
  if (!row || !isEdit.value) return false
  return Object.values(row.has_credentials ?? {}).some(Boolean)
})
const formTitle = computed(() =>
  isEdit.value ? t('settings.rclone.formTitleEdit', { name: editingRemote.value }) : t('settings.rclone.formTitleNew'),
)

/** 部署形态提示（容器档：目录挂载；主机档：文件权限）。文案随形态切换键。 */
const deployHint = computed(() =>
  runtime.deployMode === 'host'
    ? t('settings.rclone.deployHintHost')
    : t('settings.rclone.deployHintDocker'),
)

const confStatus = computed(() => {
  const snap = snapshot.value
  if (!snap) return ''
  return snap.conf_present ? t('settings.rclone.confPresent') : t('settings.rclone.confMissing')
})

// ---------------------------------------------------------------- rclone conf 加密口令（三态）

/** 三态只由 GET /api/rclone/conf-encryption 决定；口令明文永不回显。 */
const confEncStatus = ref<RcloneConfEncryptionOut | null>(null)
const confEncBusy = ref(false)
const confEncError = ref('')
/** 一次性明文：仅弹窗存活期存在，关闭即丢弃（不落库、不落日志、不进 URL）。 */
const confEncReveal = ref<string | null>(null)
/** 当前口令输入弹窗的用途；null = 关闭。 */
type ConfEncAction = 'unlock' | 'disable' | 'rekey-old' | 'rekey-new' | null
const confEncAction = ref<ConfEncAction>(null)
/** 仅内存暂存的旧/当前口令：提交或关闭弹窗时立即清空。 */
const confEncOldPassword = ref('')
const confEncDisablePassword = ref('')
const confEncDisableOpen = ref(false)

/** 快照侧也带锁死位；任一来源为 true 即按锁死处理（宁可禁用，不可误写）。 */
const confLocked = computed(() => {
  const statusLocked = confEncStatus.value?.conf_locked ?? false
  const snapshotLocked = snapshot.value?.conf_locked ?? false
  return statusLocked || snapshotLocked
})
const confEncStateLabel = computed(() => {
  const status = confEncStatus.value
  if (!status) return t('settings.rclone.confEnc.stateUnknown')
  if (confLocked.value) return t('settings.rclone.confEnc.stateLocked')
  return status.conf_encrypted
    ? t('settings.rclone.confEnc.stateEncrypted')
    : t('settings.rclone.confEnc.statePlain')
})
const confEncTagType = computed<'default' | 'success' | 'error'>(() => {
  if (confLocked.value) return 'error'
  return confEncStatus.value?.conf_encrypted === true ? 'success' : 'default'
})
/** 锁死态除解锁外所有写按钮禁用；状态未知时一律禁用。 */
const confEncCanEnable = computed(
  () => confEncStatus.value !== null && !confEncStatus.value.conf_encrypted && !confLocked.value && !confEncBusy.value,
)
const confEncCanManage = computed(
  () => confEncStatus.value !== null && confEncStatus.value.conf_encrypted && !confLocked.value && !confEncBusy.value,
)
const confEncCanUnlock = computed(() => confLocked.value && !confEncBusy.value)

/** 口令弹窗文案随用途切换（组件只有一个实例，:key 强制用途切换时清空输入）。 */
const confEncPwCopy = computed(() => {
  switch (confEncAction.value) {
    case 'unlock':
      return {
        title: t('settings.rclone.confEnc.pwUnlockTitle'),
        intro: t('settings.rclone.confEnc.pwUnlockIntro'),
        label: t('settings.rclone.confEnc.pwLabel'),
        placeholder: t('settings.rclone.confEnc.pwPlaceholder'),
      }
    case 'disable':
      return {
        title: t('settings.rclone.confEnc.pwDisableTitle'),
        intro: t('settings.rclone.confEnc.pwDisableIntro'),
        label: t('settings.rclone.confEnc.pwOldLabel'),
        placeholder: t('settings.rclone.confEnc.pwOldPlaceholder'),
      }
    case 'rekey-old':
      return {
        title: t('settings.rclone.confEnc.pwRekeyOldTitle'),
        intro: t('settings.rclone.confEnc.pwRekeyOldIntro'),
        label: t('settings.rclone.confEnc.pwOldLabel'),
        placeholder: t('settings.rclone.confEnc.pwOldPlaceholder'),
      }
    case 'rekey-new':
      return {
        title: t('settings.rclone.confEnc.pwRekeyNewTitle'),
        intro: t('settings.rclone.confEnc.pwRekeyNewIntro'),
        label: t('settings.rclone.confEnc.pwNewLabel'),
        placeholder: t('settings.rclone.confEnc.pwNewPlaceholder'),
      }
    default:
      return { title: '', intro: '', label: '', placeholder: '' }
  }
})

/** 三态单独刷新（外部显式调用 / 暴露给父组件）。 */
async function refreshConfEnc(): Promise<void> {
  try {
    confEncStatus.value = await getRcloneConfEncryption()
    confEncError.value = ''
  } catch (err) {
    confEncStatus.value = null
    confEncError.value = err instanceof Error ? err.message : String(err)
  }
}

/** 统一写入口：失败显示后端 message，不吞掉；成功返回的一次性明文交给 SecretReveal。 */
async function runConfEncWrite(call: () => Promise<string | null>): Promise<void> {
  confEncBusy.value = true
  confEncError.value = ''
  try {
    const oneTime = await call()
    if (oneTime) {
      confEncReveal.value = oneTime
    } else {
      await refresh()
    }
  } catch (err) {
    confEncError.value = err instanceof Error ? err.message : String(err)
  } finally {
    confEncBusy.value = false
  }
}

function onConfEncEnable(): void {
  void runConfEncWrite(async () => (await enableRcloneConfEncryption()).password_once)
}

function startConfEncUnlock(): void {
  confEncError.value = ''
  confEncAction.value = 'unlock'
}

function startConfEncRekey(): void {
  confEncError.value = ''
  confEncAction.value = 'rekey-old'
}

function startConfEncDisable(): void {
  confEncError.value = ''
  confEncAction.value = 'disable'
}

function closeConfEncPw(): void {
  confEncAction.value = null
  confEncOldPassword.value = ''
}

/** 只有用户主动关闭弹窗才走 setter；程序化收口（action 置空）不会误清正在流转的口令。 */
const confEncPwShow = computed({
  get: () => confEncAction.value !== null,
  set: (value: boolean) => {
    if (!value) closeConfEncPw()
  },
})

async function onConfEncPassword(password: string): Promise<void> {
  const action = confEncAction.value
  if (action === 'unlock') {
    closeConfEncPw()
    await runConfEncWrite(async () => {
      await unlockRcloneConfEncryption({ password })
      return null
    })
  } else if (action === 'disable') {
    confEncDisablePassword.value = password
    confEncAction.value = null
    confEncDisableOpen.value = true
  } else if (action === 'rekey-old') {
    confEncOldPassword.value = password
    // :key 随用途切换 → 弹窗重建，自填草稿从空开始，旧口令只留在内存变量里
    confEncAction.value = 'rekey-new'
  } else if (action === 'rekey-new') {
    const oldPassword = confEncOldPassword.value
    closeConfEncPw()
    if (!oldPassword) return
    await runConfEncWrite(async () => {
      const result = await rekeyRcloneConfEncryption({
        old_password: oldPassword,
        new_password: password,
      })
      return result.password_once
    })
  }
}

function onConfEncDisableConfirm(): void {
  const password = confEncDisablePassword.value
  confEncDisableOpen.value = false
  confEncDisablePassword.value = ''
  if (!password) return
  void runConfEncWrite(async () => {
    await disableRcloneConfEncryption({ current_password: password })
    return null
  })
}

function onConfEncDisableGateClose(): void {
  confEncDisableOpen.value = false
  confEncDisablePassword.value = ''
}

function onConfEncRevealConfirm(): void {
  if (confEncReveal.value === null) return
  confEncReveal.value = null
  void refresh()
}

const confEncDisableGateShow = computed({
  get: () => confEncDisableOpen.value,
  set: (value: boolean) => {
    if (!value) onConfEncDisableGateClose()
  },
})

const confEncRevealShow = computed({
  get: () => confEncReveal.value !== null,
  set: (value: boolean) => {
    if (!value) onConfEncRevealConfirm()
  },
})

const columns = computed<DataTableColumns<RcloneConfigItem>>(() => [
  { title: t('settings.rclone.colRemote'), key: 'remote', width: 150, ellipsis: { tooltip: true } },
  { title: t('settings.rclone.colType'), key: 'type', width: 92 },
  {
    title: t('settings.rclone.colParams'),
    key: 'params',
    minWidth: 180,
    ellipsis: { tooltip: true },
    render: (row) => {
      if (!row.managed) {
        return h('span', { class: 'rclone-panel__muted' }, t('settings.rclone.unmanaged'))
      }
      const entries = Object.entries(row.params ?? {})
      if (entries.length === 0) return '—'
      return entries.map(([k, v]) => `${k}=${v}`).join(' · ')
    },
  },
  {
    title: t('settings.rclone.hasCredential'),
    key: 'credentials',
    width: 220,
    render: (row) => {
      if (!row.managed) return '—'
      const entries = Object.entries(row.has_credentials ?? {})
      if (entries.length === 0) return '—'
      // 徽标必须自己成行并可换行：DataTable 单元格**不做 overflow 裁剪**，此前用
      // `NSpace wrap=false` 时两个 `password: 已配置` 直接溢出到相邻「操作」列下方，
      // 视觉上按钮压在徽标上（2026-10-27 实拍重叠）。内联样式不受 scoped 作用域限制。
      return h(
        'div',
        { style: 'display:flex;flex-wrap:wrap;gap:4px;min-width:0' },
        entries.map(([k, v]) =>
          h(
            NTag,
            { size: 'small', type: v ? 'success' : 'default', bordered: false, style: 'margin:0' },
            { default: () => `${k}: ${v ? t('settings.rclone.hasCredential') : t('settings.rclone.noCredential')}` },
          ),
        ),
      )
    },
  },
  {
    title: '',
    key: 'actions',
    // 宽度容纳「编辑 / 查看占用 / 删除」三档（含禁用态），避免按钮挤压换行
    width: 214,
    align: 'right',
    fixed: 'right',
    render: (row) =>
      h(
        NSpace,
        { size: 8, wrap: false, justify: 'end' },
        () => [
          row.managed
            ? h(
                NButton,
                { size: 'small', secondary: true, onClick: () => startEdit(row) },
                { default: () => t('settings.rclone.edit') },
              )
            : null,
          // 有仓库引用 → 删除按钮禁用，改为「查看占用」入口（裁决 1/2：阻断 + 可见）
          (row.used_by_repos ?? 0) > 0
            ? h(NButton, { size: 'small', onClick: () => openUsage(row.remote) }, () =>
                t('settings.rclone.usage'),
              )
            : null,
          (row.used_by_repos ?? 0) > 0
            ? h(
                NTooltip,
                {},
                {
                  trigger: () =>
                    h(
                      NButton,
                      { size: 'small', type: 'error', secondary: true, disabled: true },
                      { default: () => t('settings.rclone.delete') },
                    ),
                  default: () => t('settings.rclone.deleteBlocked', { n: row.used_by_repos ?? 0 }),
                },
              )
            : h(
                NPopconfirm,
                { onPositiveClick: () => remove(row.remote), disabled: confLocked.value },
                {
                  trigger: () =>
                    h(
                      NButton,
                      { size: 'small', type: 'error', secondary: true, disabled: confLocked.value },
                      { default: () => t('settings.rclone.delete') },
                    ),
                  default: () =>
                    h('div', {}, [
                      h('div', {}, t('settings.rclone.deleteConfirm', { name: row.remote })),
                      isOauthType(row.type)
                        ? h('div', { class: 'rclone-panel__warn' }, t('settings.rclone.deleteOauthWarn'))
                        : null,
                    ]),
                },
              ),
        ].filter(Boolean) as unknown[],
      ),
  },
])

/** OAuth 网盘等需浏览器授权的类型（永不进注册表，删除提示需加重）。 */
function isOauthType(type: string): boolean {
  return !MANAGED_TYPES.includes(type as ManagedType)
}

/** 底层远端候选：conf 内**所有**段（crypt 可叠加在任何 remote 上，含 OAuth 网盘），
 *  排除正在编辑的段自身（自引用会成环）；在册校验由此**前移**为「选不到就填不了」。 */
const remoteOptions = computed(() =>
  (snapshot.value?.configs ?? [])
    .filter((c) => c.remote !== editingRemote.value)
    .map((c) => ({ label: c.remote, value: c.remote })),
)

async function refresh(): Promise<void> {
  loading.value = true
  try {
    // 三态是状态唯一真源；conf 段清单单独拉取，失败不拖累状态显示
    const [encStatus, nextSnapshot] = await Promise.all([
      getRcloneConfEncryption().catch((err: unknown) => {
        confEncError.value = err instanceof Error ? err.message : String(err)
        return null
      }),
      listRcloneConfigs().catch((err: unknown) => {
        message.error(err instanceof Error ? err.message : String(err))
        return null
      }),
    ])
    confEncStatus.value = encStatus
    if (encStatus !== null) confEncError.value = ''
    if (nextSnapshot) snapshot.value = nextSnapshot
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 占用明细（删除保护方案 P0，裁决 2）

/** 「查看占用」弹窗目标远端名；空串 = 关闭。 */
const usageRemote = ref('')
/** 编辑态下当前远端被多少仓库引用（>0 即触发删除阻断）。 */
const usedByCount = computed(
  () =>
    (snapshot.value?.configs ?? []).find((c) => c.remote === editingRemote.value)?.used_by_repos ??
    0,
)
const usageOpen = computed({
  get: () => usageRemote.value !== '',
  set: (value: boolean) => {
    if (!value) usageRemote.value = ''
  },
})
const usageLoading = ref(false)
const usageError = ref('')
const usageRepos = ref<{ id: string; name: string }[]>([])
const usageDependents = ref<string[]>([])
const usageCrypt = ref(false)

/** 打开「查看占用」：懒加载明细（列表接口只给计数，不给清单）。 */
async function openUsage(remote: string): Promise<void> {
  usageRemote.value = remote
  usageLoading.value = true
  usageError.value = ''
  usageRepos.value = []
  usageDependents.value = []
  usageCrypt.value = false
  try {
    const usage = await getRcloneRemoteUsage(remote)
    usageRepos.value = usage.repos ?? []
    usageDependents.value = usage.dependent_remotes ?? []
    usageCrypt.value = Boolean(usage.crypt)
  } catch (err) {
    usageError.value = err instanceof Error ? err.message : String(err)
    usageRemote.value = '' // 取不到就别留个空壳弹窗
    message.error(usageError.value)
  } finally {
    usageLoading.value = false
  }
}

/** 「改指向」目标 remote（空 = 未选）；与 openUsage 同弹窗内操作。 */
const repointTarget = ref('')
const repointing = ref(false)

/** 可选目标：conf 内除当前 remote 之外的其余段。 */
const repointOptions = computed(() =>
  (snapshot.value?.configs ?? [])
    .filter((item) => item.remote !== usageRemote.value)
    .map((item) => ({ label: `${item.remote}（${item.type}）`, value: item.remote })),
)

/** 批量改指向：把引用当前 remote 的仓库 url 切到目标 remote，之后旧段即可删除。 */
async function onRepoint(): Promise<void> {
  const target = repointTarget.value
  if (!target || !usageRemote.value) {
    return
  }
  repointing.value = true
  try {
    const result = await repointRcloneRemote(usageRemote.value, target)
    message.success(t('settings.rclone.repointDone', { n: result.repos.length, name: target }))
    repointTarget.value = ''
    usageRemote.value = ''
    await refresh()
  } catch {
    // 错误由 http 层统一提示（422 目标不在册 / 409 env 覆盖）
  } finally {
    repointing.value = false
  }
}

/** 本地绝对路径判定（与后端 `classify_crypt_remote` 的 local 分支同口径：
 *  `/x`、`C:/x`、`C:\x`、`\\srv\share`）。仅用于**表单模式回填**，真校验在后端。 */
function isLocalTarget(value: string): boolean {
  return /^(\/|[A-Za-z]:[\\/]|\\\\)/.test((value || '').trim())
}

function startEdit(row: RcloneConfigItem): void {
  editingRemote.value = row.remote
  const next = emptyForm()
  next.remoteName = row.remote
  next.type = (MANAGED_TYPES.includes(row.type as ManagedType) ? row.type : 'crypt') as ManagedType
  const p = row.params ?? {}
  // 密码形态默认「明文」：编辑态密码框留空 = 不动密码（控件禁用），只有填了新值才生效
  next.passwordForm = 'plain'
  if (next.type === 'crypt') {
    // 本地绝对路径（`/x`、`C:/x`、`C:\x`、`\\srv\share`）→ 本地模式，整串进路径位；
    // 其余按 `name:path` 拆两段（首个冒号；path 内允许再出现冒号）
    const raw = p.remote ?? ''
    if (isLocalTarget(raw)) {
      next.cryptRemoteMode = 'local'
      next.cryptRemotePath = raw
    } else {
      const idx = raw.indexOf(':')
      if (idx > 0) {
        next.cryptRemoteName = raw.slice(0, idx)
        next.cryptRemotePath = raw.slice(idx + 1)
      } else {
        // 无法识别的历史值 → 原样落路径位，不静默丢值
        next.cryptRemotePath = raw
      }
    }
    next.filenameEncryption = p.filename_encryption ?? 'standard'
    next.directoryNameEncryption = p.directory_name_encryption !== 'false'
    next.filenameEncoding = p.filename_encoding ?? 'base64'
  } else {
    next.webdavUrl = p.url ?? ''
    next.vendor = p.vendor ?? 'other'
    next.user = p.user ?? ''
    next.bearerToken = p.bearer_token ?? ''
  }
  // 凭据**不预填**（S3）：留空 = 保持不变
  form.value = next
}

function cancelEdit(): void {
  editingRemote.value = null
  form.value = emptyForm()
}

/** 点「生成」：本地 CSPRNG 生成 32 位密码 → 弹窗一次性展示（确认后才写入表单）。 */
function requestGenerate(field: 'password' | 'password2' | 'pass'): void {
  generatedField.value = field
  generatedValue.value = generatePassword()
}

/** 确认已备份 → 写入对应密码栏并关闭弹窗。 */
function confirmGenerated(): void {
  const field = generatedField.value
  if (field) {
    form.value[field] = generatedValue.value
  }
  generatedShow.value = false
}

function buildParams(): Record<string, string> {
  const f = form.value
  if (f.type === 'crypt') {
    const params: Record<string, string> = {
      // 本地模式：整串就是绝对路径；远端模式：拼回 `name:path`（路径留空 = 远端根）
      remote:
        f.cryptRemoteMode === 'local'
          ? f.cryptRemotePath.trim()
          : f.cryptRemoteName
            ? `${f.cryptRemoteName}:${f.cryptRemotePath.trim()}`
            : '',
      filename_encryption: f.filenameEncryption,
      directory_name_encryption: String(f.directoryNameEncryption),
      filename_encoding: f.filenameEncoding,
    }
    if (f.password) params.password = f.password
    if (f.password2) params.password2 = f.password2
    return params
  }
  const params: Record<string, string> = { url: f.webdavUrl, vendor: f.vendor, user: f.user }
  if (f.pass) params.pass = f.pass
  if (f.bearerToken) params.bearer_token = f.bearerToken
  return params
}

async function submit(): Promise<void> {
  const name = form.value.remoteName.trim()
  if (!name) {
    message.error(t('settings.rclone.remoteName'))
    return
  }
  if (form.value.type === 'crypt') {
    if (form.value.cryptRemoteMode === 'local') {
      if (!isLocalTarget(form.value.cryptRemotePath)) {
        message.error(t('settings.rclone.cryptLocalRequired'))
        return
      }
    } else if (!form.value.cryptRemoteName) {
      message.error(t('settings.rclone.cryptRemoteRequired'))
      return
    }
  }
  submitting.value = true
  try {
    if (isEdit.value && editingRemote.value) {
      await updateRcloneConfig(editingRemote.value, {
        type: form.value.type,
        params: buildParams(),
        value_obscured: form.value.passwordForm === 'obscured',
      })
      message.success(t('settings.rclone.updated', { name: editingRemote.value }))
    } else {
      await createRcloneConfig(name, {
        type: form.value.type,
        params: buildParams(),
        value_obscured: form.value.passwordForm === 'obscured',
      })
      message.success(t('settings.rclone.created', { name }))
    }
    cancelEdit()
    await refresh()
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------- 修正明文段 / 导出原密码

const repairing = ref(false)

/** 「修正为混淆」：不填密码，按现值就地 `--obscure`（后端先做 reveal 一致性检查，
 *  现值若能被解开说明已是混淆值 → 中止并提示填密码，绝不二次混淆）。 */
async function repairPlain(): Promise<void> {
  const remote = editingRemote.value
  if (!remote) {
    return
  }
  repairing.value = true
  try {
    await updateRcloneConfig(remote, {
      type: form.value.type,
      params: buildParams(),
      value_obscured: false,
    })
    message.success(t('settings.rclone.repaired', { name: remote }))
    await refresh()
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  } finally {
    repairing.value = false
  }
}

/** 「如何找回密码」：只展示宿主机自助命令示例——**本工具不执行命令、不读 conf、不回显凭据**。 */
const recoverOpen = ref(false)

/** 命令文本（模板渲染与复制共用一份，避免两处拼接漂移）。 */
const recoverCommands = computed(() => ({
  dump: `rclone --config <${t('settings.rclone.recoverConfPlaceholder')}> config dump`,
  reveal: `rclone reveal <${t('settings.rclone.recoverValuePlaceholder')}>`,
}))

/** 复制到剪贴板（与 SecretReveal 同一口径：clipboard API + 失败提示；utils/clipboard 单一实现点）。 */
async function copyText(text: string): Promise<void> {
  await copyWithNotify(text, message, t('common.copied'), t('common.copyFailed'))
}

async function remove(remote: string): Promise<void> {
  try {
    await deleteRcloneConfig(remote)
    message.success(t('settings.rclone.deleted', { name: remote }))
    if (editingRemote.value === remote) cancelEdit()
    await refresh()
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  }
}

onMounted(refresh)

defineExpose({ refresh, refreshConfEnc })
</script>

<template>
  <div class="rclone-panel">
    <NAlert v-if="snapshot && !snapshot.available" type="warning" :bordered="false" class="rclone-panel__alert">
      {{ t('settings.rclone.unavailable') }}<template v-if="snapshot.reason">：{{ snapshot.reason }}</template>
    </NAlert>
    <NAlert v-if="confLocked" type="error" :bordered="false" class="rclone-panel__alert">
      {{ t('settings.rclone.confEnc.lockedHint') }}
    </NAlert>
    <NAlert v-if="confEncError" type="error" :bordered="false" class="rclone-panel__alert">
      {{ confEncError }}
    </NAlert>

    <NSpace align="center" justify="space-between" class="rclone-panel__head">
      <span class="rclone-panel__muted">
        {{ t('settings.rclone.confPath') }}：<code>{{ snapshot?.conf_path ?? '—' }}</code>
        <template v-if="snapshot">（{{ confStatus }}）</template>
      </span>
      <NButton size="small" :loading="loading" @click="refresh">{{ t('settings.rclone.refresh') }}</NButton>
    </NSpace>

    <NCard size="small" :title="t('settings.rclone.confEnc.title')" class="rclone-panel__card">
      <NSpace vertical :size="10">
        <NSpace align="center" :size="8" class="rclone-panel__row">
          <NTag size="small" :type="confEncTagType" :bordered="false">{{ confEncStateLabel }}</NTag>
          <span class="rclone-panel__muted">{{ t('settings.rclone.confEnc.hint') }}</span>
        </NSpace>
        <NSpace align="center" :size="8" class="rclone-panel__row">
          <NButton
            v-if="!confLocked && (!confEncStatus || !confEncStatus.conf_encrypted)"
            size="small"
            type="primary"
            :disabled="!confEncCanEnable"
            :loading="confEncBusy"
            @click="onConfEncEnable"
          >
            {{ t('settings.rclone.confEnc.enableBtn') }}
          </NButton>
          <NButton
            v-if="confEncStatus && confEncStatus.conf_encrypted && !confLocked"
            size="small"
            type="primary"
            :disabled="!confEncCanManage"
            :loading="confEncBusy"
            @click="startConfEncRekey"
          >
            {{ t('settings.rclone.confEnc.rekeyBtn') }}
          </NButton>
          <NButton
            v-if="confEncStatus && confEncStatus.conf_encrypted && !confLocked"
            size="small"
            :disabled="!confEncCanManage"
            :loading="confEncBusy"
            @click="startConfEncDisable"
          >
            {{ t('settings.rclone.confEnc.disableBtn') }}
          </NButton>
          <NButton
            v-if="confLocked"
            size="small"
            type="primary"
            :disabled="!confEncCanUnlock"
            :loading="confEncBusy"
            @click="startConfEncUnlock"
          >
            {{ t('settings.rclone.confEnc.unlockBtn') }}
          </NButton>
        </NSpace>
      </NSpace>
    </NCard>

    <NCard size="small" :title="t('settings.rclone.listTitle')" class="rclone-panel__card">
      <NDataTable
        :columns="columns"
        :data="snapshot?.configs ?? []"
        :loading="loading"
        size="small"
        :bordered="false"
        :scroll-x="860"
        :row-key="(row: RcloneConfigItem) => row.remote"
      />
      <p v-if="snapshot && snapshot.configs.length === 0" class="rclone-panel__muted">
        {{ t('settings.rclone.empty') }}
      </p>
    </NCard>

    <NCard size="small" :title="formTitle" class="rclone-panel__card">
      <NForm label-placement="left" label-width="140">
        <NFormItem :label="t('settings.rclone.type')">
          <NSelect v-model:value="form.type" :options="TYPE_OPTIONS" :disabled="isEdit" style="width: 320px" />
        </NFormItem>
        <NFormItem :label="t('settings.rclone.remoteName')">
          <NInput
            v-model:value="form.remoteName"
            :placeholder="t('settings.rclone.remoteNamePlaceholder')"
            :disabled="isEdit"
            class="rclone-panel__field"
          />
        </NFormItem>

        <template v-if="form.type === 'crypt'">
          <NFormItem :label="t('settings.rclone.cryptRemoteMode')">
            <NRadioGroup v-model:value="form.cryptRemoteMode">
              <NSpace :size="16">
                <NRadio value="remote">{{ t('settings.rclone.cryptRemoteModeRemote') }}</NRadio>
                <NRadio value="local">{{ t('settings.rclone.cryptRemoteModeLocal') }}</NRadio>
              </NSpace>
            </NRadioGroup>
          </NFormItem>

          <!-- ① 远端叠加：远端名下拉（在册校验前移）+ 远端内路径 -->
          <NFormItem v-if="form.cryptRemoteMode === 'remote'" :label="t('settings.rclone.cryptRemote')">
            <NSpace vertical :size="4" style="width: 100%">
              <NSpace :size="8" wrap :align="'center'">
                <NSelect
                  v-model:value="form.cryptRemoteName"
                  :options="remoteOptions"
                  :placeholder="t('settings.rclone.cryptRemoteNamePlaceholder')"
                  filterable
                  style="width: 320px"
                />
                <NInput
                  v-model:value="form.cryptRemotePath"
                  :placeholder="t('settings.rclone.cryptRemotePathPlaceholder')"
                  style="width: 320px"
                />
              </NSpace>
              <span class="rclone-panel__muted">{{ t('settings.rclone.cryptRemoteHint') }}</span>
            </NSpace>
          </NFormItem>

          <!-- ② 本地目录：容器视角目录树（E33 + VirtualTree + PathBreadcrumb，与仓库页 local 行同一组件） -->
          <NFormItem v-else :label="t('settings.rclone.cryptLocalPath')">
            <DirPickerInline
              v-model:path="form.cryptRemotePath"
              :placeholder="t('settings.rclone.cryptLocalPlaceholder')"
              :hint="t('settings.rclone.cryptLocalHint')"
              :value-label="t('settings.rclone.cryptLocalValueLabel')"
              :value-hint="t('settings.rclone.cryptLocalValueHint')"
              :height="240"
            />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.password')">
            <NSpace vertical :size="4" style="width: 100%">
              <PasswordInput
                v-model="form.password"
                width="400px"
                :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
                :generate-label="t('common.generatePassword')"
                @generate="requestGenerate('password')"
              />
              <span v-if="isEdit" class="rclone-panel__muted">{{ t('settings.rclone.passwordObscureNote') }}</span>
              <span v-if="isEdit" class="rclone-panel__warn">{{ t('settings.rclone.passwordRewriteWarn') }}</span>
            </NSpace>
          </NFormItem>
          <NFormItem :label="t('settings.rclone.password2')">
            <NSpace vertical :size="4" style="width: 100%">
              <PasswordInput
                v-model="form.password2"
                width="400px"
                :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
                :generate-label="t('common.generatePassword')"
                @generate="requestGenerate('password2')"
              />
              <span v-if="isEdit" class="rclone-panel__warn">{{ t('settings.rclone.passwordRewriteWarn') }}</span>
            </NSpace>
          </NFormItem>
          <NFormItem :label="t('settings.rclone.filenameEncryption')">
            <NSelect v-model:value="form.filenameEncryption" :options="FILENAME_ENCRYPTION_OPTIONS" style="width: 320px" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.directoryNameEncryption')">
            <NSwitch v-model:value="form.directoryNameEncryption" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.filenameEncoding')">
            <NSpace vertical :size="4">
              <NSelect v-model:value="form.filenameEncoding" :options="FILENAME_ENCODING_OPTIONS" style="width: 320px" />
              <span class="rclone-panel__muted">{{ t('settings.rclone.filenameEncodingHint') }}</span>
            </NSpace>
          </NFormItem>
        </template>

        <template v-else>
          <NFormItem :label="t('settings.rclone.webdavUrl')">
            <NInput v-model:value="form.webdavUrl" :placeholder="t('settings.rclone.webdavUrlPlaceholder')" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.vendor')">
            <NSelect v-model:value="form.vendor" :options="VENDOR_OPTIONS" style="width: 320px" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.user')">
            <NInput v-model:value="form.user" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.bearerToken')">
            <NSpace vertical :size="4" style="width: 100%">
              <PasswordInput
                v-model="form.bearerToken"
                width="400px"
                :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : t('settings.rclone.bearerTokenPlaceholder')"
              />
              <span v-if="isEdit" class="rclone-panel__muted">{{ t('settings.rclone.passwordObscureNote') }}</span>
            </NSpace>
          </NFormItem>
          <NFormItem :label="t('settings.rclone.pass')">
            <NSpace vertical :size="4" style="width: 100%">
              <PasswordInput
                v-model="form.pass"
                width="400px"
                :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
                :generate-label="t('common.generatePassword')"
                @generate="requestGenerate('pass')"
              />
              <span v-if="isEdit" class="rclone-panel__muted">{{ t('settings.rclone.passwordObscureNote') }}</span>
            </NSpace>
          </NFormItem>
        </template>

        <NFormItem :label="t('settings.rclone.passwordForm')">
          <div class="rclone-panel__formform">
            <div class="rclone-panel__row">
              <NSelect
                v-model:value="form.passwordForm"
                :options="PASSWORD_FORM_OPTIONS"
                :disabled="passwordFormDisabled"
                style="width: 400px"
              />
              <!-- 落盘形态只读徽标：目标形态恒定，用户不选 -->
              <NTag v-if="isEdit" size="small" :bordered="false" :type="storedFormTagType">
                {{ t('settings.rclone.storedForm', { form: storedFormLabel }) }}
              </NTag>
            </div>
            <span class="rclone-panel__muted">
              {{
                passwordFormDisabled
                  ? t('settings.rclone.passwordFormDisabled')
                  : t('settings.rclone.passwordFormHint')
              }}
            </span>

            <!-- 形态未知（无记录 / conf 被外部改过）：只能填密码重存来重建记录 -->
            <span v-if="isEdit && storedForm === null" class="rclone-panel__muted">
              {{ t('settings.rclone.formUnknownHint') }}
            </span>

            <!-- 次级维护入口归组：找回密码（命令示例，凭据零回显）+ 明文段就地修正 -->
            <div v-if="showRecoverHint || canRepairPlain" class="rclone-panel__maint">
              <div class="rclone-panel__maint-label">{{ t('settings.rclone.maintGroupLabel') }}</div>
              <div class="rclone-panel__row">
                <NButton
                  v-if="showRecoverHint"
                  size="tiny"
                  secondary
                  @click="recoverOpen = true"
                >
                  {{ t('settings.rclone.recoverBtn') }}
                </NButton>
              </div>
            </div>

            <!-- 历史明文段：不填密码也能就地修正（后端做 reveal 一致性检查，绝不二次混淆） -->
            <NAlert v-if="canRepairPlain" type="warning" :bordered="false">
              <div class="rclone-panel__formform">
                <span>{{ t('settings.rclone.plainRepairHint') }}</span>
                <div>
                  <NButton size="small" :disabled="confLocked" :loading="repairing" @click="repairPlain">
                    {{ t('settings.rclone.plainRepairBtn') }}
                  </NButton>
                </div>
              </div>
            </NAlert>

            <!-- 有仓库引用该远端 → 明示阻断原因与占用清单（裁决 1/2/3） -->
            <NAlert v-if="isEdit && usedByCount > 0" type="warning" :bordered="false">
              {{ t('settings.rclone.deleteBlocked', { n: usedByCount }) }}
            </NAlert>
          </div>
        </NFormItem>

        <NSpace>
          <NButton type="primary" :disabled="confLocked" :loading="submitting" @click="submit">
            <template #icon>
              <NIcon :size="16"><IconCheckCircle /></NIcon>
            </template>
            {{ isEdit ? t('settings.rclone.submitEdit') : t('settings.rclone.submitNew') }}
          </NButton>
          <NButton v-if="isEdit" @click="cancelEdit">{{ t('settings.rclone.cancelEdit') }}</NButton>
        </NSpace>
      </NForm>
    </NCard>

    <!-- 「查看占用」：删除被阻断时，列出是谁在引用这个远端 -->
    <NModal v-model:show="usageOpen" preset="card" :title="t('settings.rclone.usageTitle', { name: usageRemote })" style="width: min(520px, calc(100vw - 32px))">
      <NSpace vertical :size="8">
        <template v-if="usageLoading">
          <span class="rclone-panel__muted">{{ t('settings.rclone.usageLoading') }}</span>
        </template>
        <template v-else>
          <div v-if="usageRepos.length > 0">
            <div class="rclone-panel__muted">{{ t('settings.rclone.usageRepos') }}</div>
            <NTag v-for="repo in usageRepos" :key="repo.id" size="small" :bordered="false">
              {{ repo.name }}
            </NTag>
            <!-- 改指向（删除前的那条出路）：把引用它的仓库切到另一个远端，之后旧段即可删 -->
            <div class="rclone-panel__repoint">
              <div class="rclone-panel__muted">{{ t('settings.rclone.repointHint') }}</div>
              <div class="rclone-panel__repoint-row">
                <NSelect
                  v-model:value="repointTarget"
                  :options="repointOptions"
                  size="small"
                  :placeholder="t('settings.rclone.repointPlaceholder')"
                  style="flex: 1"
                />
                <NButton size="small" :disabled="!repointTarget || confLocked" :loading="repointing" @click="onRepoint">
                  {{ t('settings.rclone.repointBtn') }}
                </NButton>
              </div>
            </div>
          </div>
          <div v-if="usageDependents.length > 0">
            <div class="rclone-panel__muted">{{ t('settings.rclone.usageDependents') }}</div>
            <NTag v-for="dep in usageDependents" :key="dep" size="small" type="warning" :bordered="false">
              {{ dep }}
            </NTag>
          </div>
          <NAlert v-if="usageCrypt" type="error" :bordered="false">
            {{ t('settings.rclone.deleteCryptWarn') }}
          </NAlert>
        </template>
      </NSpace>
    </NModal>

    <!-- conf 加密口令输入：解锁 / 停用 / 换口令（新旧两段）共用同一组件，口令只留内存 -->
    <PasswordEntryModal
      :key="confEncAction ?? 'closed'"
      v-model:show="confEncPwShow"
      :title="confEncPwCopy.title"
      :intro="confEncPwCopy.intro"
      :generated-hint="t('settings.rclone.confEnc.revealHint')"
      :custom-label="confEncPwCopy.label"
      :custom-placeholder="confEncPwCopy.placeholder"
      :submit-label="t('common.confirm')"
      :confirm-label="t('settings.rclone.confEnc.revealConfirm')"
      :generated="null"
      :loading="confEncBusy"
      :min-length="1"
      :show-generated-cancel="false"
      :show-generate="false"
      @submit-custom="onConfEncPassword"
    />

    <!-- 停用加密：输入当前口令后再过一次危险操作确认门 -->
    <ConfirmGate
      v-model:show="confEncDisableGateShow"
      :title="t('settings.rclone.confEnc.disableGateTitle')"
      :description="t('settings.rclone.confEnc.disableGateDesc')"
      :loading="confEncBusy"
      :confirm-label="t('settings.rclone.confEnc.disableBtn')"
      @confirm="onConfEncDisableConfirm"
    />

    <!-- 后端生成 / 换口令的一次性明文（全站统一呈现，SecretReveal） -->
    <NModal
      v-model:show="confEncRevealShow"
      preset="card"
      :title="t('settings.rclone.confEnc.revealTitle')"
      style="width: min(520px, calc(100vw - 32px))"
    >
      <SecretReveal
        :secret="confEncReveal ?? ''"
        :hint="t('settings.rclone.confEnc.revealHint')"
        :confirm-label="t('settings.rclone.confEnc.revealConfirm')"
        @confirm="onConfEncRevealConfirm"
      />
    </NModal>

    <!-- 生成密码后的一次性明文（全站统一呈现，SecretReveal） -->
    <NModal v-model:show="generatedShow" preset="card" :title="t('settings.rclone.passwordGeneratedTitle')" style="width: min(520px, calc(100vw - 32px))">
      <SecretReveal
        :secret="generatedValue"
        :hint="t('settings.rclone.passwordGeneratedHint')"
        :confirm-label="t('settings.rclone.passwordSavedClose')"
        @confirm="confirmGenerated"
      />
    </NModal>

    <!-- 找回密码：宿主机自助命令示例（纯文本；本工具不执行、不读 conf、不回显凭据） -->
    <NModal
      v-model:show="recoverOpen"
      preset="card"
      :title="t('settings.rclone.recoverTitle')"
      style="width: min(640px, calc(100vw - 32px))"
    >
      <div class="rclone-panel__formform rclone-panel__recover">
        <span>{{ t('settings.rclone.recoverIntro') }}</span>
        <span class="rclone-panel__muted">{{ t('settings.rclone.recoverStepDump') }}</span>
        <div class="rclone-panel__cmdrow">
          <pre class="rclone-panel__code">{{ recoverCommands.dump }}</pre>
          <NButton size="tiny" secondary @click="copyText(recoverCommands.dump)">
            {{ t('common.copy') }}
          </NButton>
        </div>
        <span class="rclone-panel__muted">{{ t('settings.rclone.recoverStepReveal') }}</span>
        <div class="rclone-panel__cmdrow">
          <pre class="rclone-panel__code">{{ recoverCommands.reveal }}</pre>
          <NButton size="tiny" secondary @click="copyText(recoverCommands.reveal)">
            {{ t('common.copy') }}
          </NButton>
        </div>
        <span class="rclone-panel__muted">
          {{ t('settings.rclone.recoverPathHint', { path: snapshot?.conf_path ?? '—' }) }}
        </span>
        <NAlert type="warning" :bordered="false">{{ t('settings.rclone.recoverNote') }}</NAlert>
      </div>
    </NModal>

    <p class="rclone-panel__muted">{{ deployHint }}</p>
  </div>
</template>

<style scoped>
.rclone-panel__repoint {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rclone-panel__repoint-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.rclone-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rclone-panel__head {
  flex-wrap: wrap;
}
.rclone-panel__card {
  --n-border-radius: 10px;
}
.rclone-panel__muted {
  color: var(--n-text-color-3, #8a8f98);
  font-size: 12px;
}
.rclone-panel__warn {
  margin-top: 4px;
  color: var(--n-warning-color, #f0a020);
}
.rclone-panel__field {
  width: 320px;
  max-width: 100%;
}
.rclone-panel__formform {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rclone-panel__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
/* 次级维护入口分组框（找回密码 / 就地修正）：与主输入区分层，弱化为虚线细框 */
.rclone-panel__maint {
  border: 1px dashed var(--n-border-color, rgba(255, 255, 255, 0.14));
  border-radius: 6px;
  padding: 8px 12px;
}
.rclone-panel__maint-label {
  color: var(--n-text-color-3, #8a8f98);
  font-size: 11px;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}
.rclone-panel__cmdrow {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.rclone-panel__code {
  flex: 1;
  min-width: 0;
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--n-action-color, rgba(128, 128, 128, 0.12));
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
