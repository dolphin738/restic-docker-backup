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
 * 存储形态（裁决 16）：开关 = **目标存储形态**（开到「混淆」由 rclone 混淆一次后落盘，
 * 关到「明文」原样落盘）——新建与编辑**同一语义**，不再因模式而反转；提交侧「值本身
 * 已是混淆后凭证」为独立副复选框（与明文存储互斥）。形态未知（历史配置）
 * 用「存储形态声明」登记一次（只写记录、不改密码），登记后开关即可就地转换。
 *
 * 凭据零回显（S3）：列表只渲染 has_* 布尔与形态布尔，表单密码框在编辑态**永不预填**。
 */
import { computed, h, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NPopconfirm,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import DirPickerInline from '@/components/DirPickerInline.vue'
import PasswordInput from '@/components/PasswordInput.vue'
import SecretReveal from '@/components/SecretReveal.vue'
import {
  createRcloneConfig,
  deleteRcloneConfig,
  listRcloneConfigs,
  updateRcloneConfig,
} from '@/api/repo'
import type { RcloneConfigItem, RcloneConfigsOut } from '@/api/types'
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
  /** 目标存储形态（裁决 16：开关 = 存储形态，新建与编辑同一语义）。 */
  storeForm: 'obscured' | 'plain'
  /** 提交的凭据本身已是 rclone obscure 产物（不再混淆一次）。 */
  valueObscured: boolean
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
    // 默认以混淆形态存储（conf 里是混淆后凭证）——与旧实现落盘结果一致
    storeForm: 'obscured',
    valueObscured: false,
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
const generatedField = ref<'password' | 'password2' | 'pass' | 'bearerToken' | null>(null)
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

const isEdit = computed(() => editingRemote.value !== null)

/** 是否已填写任一凭据：开关作用于「本次填入的值」时必须有输入。 */
const hasCredentialInput = computed(() => {
  const f = form.value
  if (f.type === 'crypt') return !!(f.password || f.password2)
  return !!(f.pass || f.bearerToken)
})

/** 编辑目标的已存凭据形态（方案 A sidecar）：只要有一个键形态已知，编辑态开关即可
 *  作用于「就地转换」（留空也能转换存储形态）；全未知则无可作用对象。 */
const editingRow = computed(
  () => snapshot.value?.configs.find((c) => c.remote === editingRemote.value) ?? null,
)
const storedFormKnown = computed(() => {
  const forms = Object.values(editingRow.value?.credential_obscured ?? {})
  return forms.length > 0 && forms.some((f) => f !== null)
})

/** 开关禁用：新增态无输入禁用（无可作用值）；编辑态仅「形态未知且无输入」禁用。 */
const switchDisabled = computed(() =>
  isEdit.value ? !storedFormKnown.value && !hasCredentialInput.value : !hasCredentialInput.value,
)

/** 存储形态开关（ON=混淆 / OFF=明文）的可点性：编辑态形态未知且无输入时无对象可作用。 */
const storeFormLabel = computed(() =>
  form.value.storeForm === 'obscured'
    ? t('settings.rclone.storeFormObscured')
    : t('settings.rclone.storeFormPlain'),
)

/** 副复选框（我填入的已是混淆产物）：只在填了新值时有意义；与「明文存储」互斥。 */
const valueObscuredDisabled = computed(() => !hasCredentialInput.value || form.value.storeForm === 'plain')

/** 勾选「值已是混淆产物」→ 目标形态必须为混淆（矛盾组合前端直接拦住）。 */
watch(
  () => form.value.valueObscured,
  (checked) => {
    if (checked) form.value.storeForm = 'obscured'
  },
)
/** 目标形态改成明文 → 取消「值已是混淆产物」（否则两者矛盾）。 */
watch(
  () => form.value.storeForm,
  (form_) => {
    if (form_ === 'plain') form.value.valueObscured = false
  },
)

/** 形态声明（编辑态 + 形态未知时出现）：该段凭据在 conf 里究竟是混淆值还是明文——
 *  本工具无法可靠自动判定（rclone reveal 无认证校验，§12.5 实证），由用户声明一次。
 *  声明只登记形态、**不改密码**；声明后开关即可做真正的就地转换。 */
const declareOptions = computed(() => [
  { label: t('settings.rclone.declareUnknown'), value: '' },
  { label: t('settings.rclone.declareObscured'), value: 'obscured' },
  { label: t('settings.rclone.declarePlain'), value: 'plain' },
])
const declareForm = ref<'' | 'obscured' | 'plain'>('')
const showDeclare = computed(() => isEdit.value && !storedFormKnown.value)
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
    width: 132,
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
          h(
            NPopconfirm,
            { onPositiveClick: () => remove(row.remote) },
            {
              trigger: () =>
                h(NButton, { size: 'small', type: 'error', secondary: true }, { default: () => t('settings.rclone.delete') }),
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
    snapshot.value = await listRcloneConfigs()
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  } finally {
    loading.value = false
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
  // 开关初始值 = 已存形态（方案 A）：已存为混淆形态 → 初始开（保存 = 保持不变，
  // 不触发转换）；已存明文 → 初始关。让「编辑只改别的字段就保存」默认无副作用，
  // 刻意拨动开关才触发就地转换。形态未知（外部改过 conf）→ 关 + 禁用提示。
  const forms = row.credential_obscured ?? {}
  const primary = next.type === 'crypt' ? forms.password : forms.pass
  // 目标存储形态初始值 = 已存形态（方案 A）：已存为混淆 → 开关开（保存 = 保持不变，不
  // 触发转换）；已存明文 → 开关关。让「编辑只改别的字段就保存」默认无副作用，刻意
  // 拨动开关才触发就地转换。形态未知（外部改过 conf）→ 默认混淆 + 需先声明。
  next.storeForm = primary === false ? 'plain' : 'obscured'
  declareForm.value = ''
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
  declareForm.value = ''
}

/** 点「生成」：本地 CSPRNG 生成 32 位密码 → 弹窗一次性展示（确认后才写入表单）。 */
function requestGenerate(field: 'password' | 'password2' | 'pass' | 'bearerToken'): void {
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

/** 形态声明载荷：仅编辑态 + 形态未知 + 用户明确选择时才发（None=不声明）。 */
function declarePayload(): boolean | undefined {
  if (!isEdit.value || !showDeclare.value) return undefined
  if (declareForm.value === 'obscured') return true
  if (declareForm.value === 'plain') return false
  return undefined
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
        store_form: form.value.storeForm,
        value_obscured: form.value.valueObscured,
        declare_form: declarePayload(),
      })
      message.success(t('settings.rclone.updated', { name: editingRemote.value }))
    } else {
      await createRcloneConfig(name, {
        type: form.value.type,
        params: buildParams(),
        store_form: form.value.storeForm,
        value_obscured: form.value.valueObscured,
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

defineExpose({ refresh })
</script>

<template>
  <div class="rclone-panel">
    <NAlert v-if="snapshot && !snapshot.available" type="warning" :bordered="false" class="rclone-panel__alert">
      {{ t('settings.rclone.unavailable') }}<template v-if="snapshot.reason">：{{ snapshot.reason }}</template>
    </NAlert>

    <NSpace align="center" justify="space-between" class="rclone-panel__head">
      <span class="rclone-panel__muted">
        {{ t('settings.rclone.confPath') }}：<code>{{ snapshot?.conf_path ?? '—' }}</code>
        <template v-if="snapshot">（{{ confStatus }}）</template>
      </span>
      <NButton size="small" :loading="loading" @click="refresh">{{ t('settings.rclone.refresh') }}</NButton>
    </NSpace>

    <NCard size="small" :title="t('settings.rclone.listTitle')" class="rclone-panel__card">
      <NDataTable
        :columns="columns"
        :data="snapshot?.configs ?? []"
        :loading="loading"
        size="small"
        :bordered="false"
        :scroll-x="780"
        :row-key="(row: RcloneConfigItem) => row.remote"
      />
      <p v-if="snapshot && snapshot.configs.length === 0" class="rclone-panel__muted">
        {{ t('settings.rclone.empty') }}
      </p>
    </NCard>

    <NCard size="small" :title="formTitle" class="rclone-panel__card">
      <NForm label-placement="left" label-width="140">
        <NFormItem :label="t('settings.rclone.type')">
          <NSelect v-model:value="form.type" :options="TYPE_OPTIONS" :disabled="isEdit" style="width: 200px" />
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
                  style="width: 220px"
                />
                <NInput
                  v-model:value="form.cryptRemotePath"
                  :placeholder="t('settings.rclone.cryptRemotePathPlaceholder')"
                  style="width: 280px"
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
            <PasswordInput
              v-model="form.password"
              width="400px"
              :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
              :generate-label="t('common.generatePassword')"
              @generate="requestGenerate('password')"
            />
            <span v-if="isEdit" class="rclone-panel__muted rclone-panel__note">{{ t('settings.rclone.passwordObscureNote') }}</span>
          </NFormItem>
          <NFormItem :label="t('settings.rclone.password2')">
            <PasswordInput
              v-model="form.password2"
              width="400px"
              :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
              :generate-label="t('common.generatePassword')"
              @generate="requestGenerate('password2')"
            />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.filenameEncryption')">
            <NSelect v-model:value="form.filenameEncryption" :options="FILENAME_ENCRYPTION_OPTIONS" style="width: 200px" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.directoryNameEncryption')">
            <NSwitch v-model:value="form.directoryNameEncryption" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.filenameEncoding')">
            <NSpace vertical :size="4">
              <NSelect v-model:value="form.filenameEncoding" :options="FILENAME_ENCODING_OPTIONS" style="width: 200px" />
              <span class="rclone-panel__muted">{{ t('settings.rclone.filenameEncodingHint') }}</span>
            </NSpace>
          </NFormItem>
        </template>

        <template v-else>
          <NFormItem :label="t('settings.rclone.webdavUrl')">
            <NInput v-model:value="form.webdavUrl" :placeholder="t('settings.rclone.webdavUrlPlaceholder')" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.vendor')">
            <NSelect v-model:value="form.vendor" :options="VENDOR_OPTIONS" style="width: 200px" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.user')">
            <NInput v-model:value="form.user" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.bearerToken')">
            <PasswordInput
              v-model="form.bearerToken"
              width="400px"
              :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : t('settings.rclone.bearerTokenPlaceholder')"
              :generate-label="t('common.generatePassword')"
              @generate="requestGenerate('bearerToken')"
            />
            <span v-if="isEdit" class="rclone-panel__muted rclone-panel__note">{{ t('settings.rclone.passwordObscureNote') }}</span>
          </NFormItem>
          <NFormItem :label="t('settings.rclone.pass')">
            <PasswordInput
              v-model="form.pass"
              width="400px"
              :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
              :generate-label="t('common.generatePassword')"
              @generate="requestGenerate('pass')"
            />
            <span v-if="isEdit" class="rclone-panel__muted rclone-panel__note">{{ t('settings.rclone.passwordObscureNote') }}</span>
          </NFormItem>
        </template>

        <NFormItem>
          <NSpace vertical :size="4">
            <NSpace :size="8" :wrap="false" align="center">
              <NSwitch
                :value="form.storeForm === 'obscured'"
                :disabled="switchDisabled"
                @update:value="(v: boolean) => (form.storeForm = v ? 'obscured' : 'plain')"
              />
              <span class="rclone-panel__inline">{{ t('settings.rclone.storeForm') }}</span>
              <NTag size="small" :bordered="false" :type="form.storeForm === 'obscured' ? 'success' : 'warning'">
                {{ storeFormLabel }}
              </NTag>
            </NSpace>
            <span class="rclone-panel__muted">
              {{
                switchDisabled
                  ? t('settings.rclone.obscureSwitchDisabled')
                  : isEdit && storedFormKnown
                    ? t('settings.rclone.alreadyObscuredEditHint')
                    : t('settings.rclone.alreadyObscuredHint')
              }}
            </span>
            <!-- 提交侧形态（只在填了新值时有意义）：值本身已是混淆后凭证 -->
            <NCheckbox
              v-model:checked="form.valueObscured"
              :disabled="valueObscuredDisabled"
              class="rclone-panel__check"
            >
              {{ t('settings.rclone.valueObscured') }}
            </NCheckbox>
            <span v-if="hasCredentialInput" class="rclone-panel__muted">
              {{ t('settings.rclone.valueObscuredHint') }}
            </span>
          </NSpace>
        </NFormItem>

        <!-- 形态未知（历史配置 / 宿主机手工维护）：一次性声明现值形态，声明不改密码 -->
        <NFormItem v-if="showDeclare" :label="t('settings.rclone.declareForm')">
          <NSpace vertical :size="4">
            <NSelect v-model:value="declareForm" :options="declareOptions" style="width: 320px" />
            <span class="rclone-panel__muted">{{ t('settings.rclone.declareHint') }}</span>
          </NSpace>
        </NFormItem>

        <NSpace>
          <NButton type="primary" :loading="submitting" @click="submit">
            {{ isEdit ? t('settings.rclone.submitEdit') : t('settings.rclone.submitNew') }}
          </NButton>
          <NButton v-if="isEdit" @click="cancelEdit">{{ t('settings.rclone.cancelEdit') }}</NButton>
        </NSpace>
      </NForm>
    </NCard>

    <!-- 生成密码后的一次性明文（全站统一呈现，SecretReveal） -->
    <NModal v-model:show="generatedShow" preset="card" :title="t('settings.rclone.passwordGeneratedTitle')" style="width: min(520px, calc(100vw - 32px))">
      <SecretReveal
        :secret="generatedValue"
        :hint="t('settings.rclone.passwordGeneratedHint')"
        :confirm-label="t('settings.rclone.passwordSavedClose')"
        @confirm="confirmGenerated"
      />
    </NModal>

    <p class="rclone-panel__muted">{{ deployHint }}</p>
  </div>
</template>

<style scoped>
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
.rclone-panel__inline {
  margin-left: 8px;
  font-size: 12px;
}
.rclone-panel__note {
  display: block;
  margin-top: 4px;
}
.rclone-panel__check {
  margin-top: 4px;
  font-size: 12px;
}
</style>
