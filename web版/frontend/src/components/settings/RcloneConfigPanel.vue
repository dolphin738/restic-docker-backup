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
 * 凭据零回显（S3）：列表只渲染 has_* 布尔，表单密码框在编辑态**永不预填**。
 */
import { computed, h, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NPopconfirm,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
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
  cryptRemote: string
  password: string
  password2: string
  filenameEncryption: string
  directoryNameEncryption: boolean
  filenameEncoding: string
  webdavUrl: string
  vendor: string
  user: string
  pass: string
  alreadyObscured: boolean
}

function emptyForm(): FormState {
  return {
    remoteName: '',
    type: 'crypt',
    cryptRemote: '',
    password: '',
    password2: '',
    filenameEncryption: 'standard',
    directoryNameEncryption: true,
    filenameEncoding: 'base64',
    webdavUrl: '',
    vendor: 'other',
    user: '',
    pass: '',
    alreadyObscured: false,
  }
}

const loading = ref(false)
const submitting = ref(false)
const snapshot = ref<RcloneConfigsOut | null>(null)
const form = ref<FormState>(emptyForm())
/** 非空 = 编辑态（远端名只读，密码留空 = 保持不变）。 */
const editingRemote = ref<string | null>(null)

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
  { title: t('settings.rclone.colRemote'), key: 'remote', width: 160, ellipsis: { tooltip: true } },
  { title: t('settings.rclone.colType'), key: 'type', width: 100 },
  {
    title: t('settings.rclone.colParams'),
    key: 'params',
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
    width: 180,
    render: (row) => {
      if (!row.managed) return '—'
      const entries = Object.entries(row.has_credentials ?? {})
      if (entries.length === 0) return '—'
      return h(
        NSpace,
        { size: 4, wrap: false },
        () =>
          entries.map(([k, v]) =>
            h(
              NTag,
              { size: 'small', type: v ? 'success' : 'default', bordered: false },
              { default: () => `${k}: ${v ? t('settings.rclone.hasCredential') : t('settings.rclone.noCredential')}` },
            ),
          ),
      )
    },
  },
  {
    title: '',
    key: 'actions',
    width: 150,
    render: (row) =>
      h(
        NSpace,
        { size: 8, wrap: false },
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

function startEdit(row: RcloneConfigItem): void {
  editingRemote.value = row.remote
  const next = emptyForm()
  next.remoteName = row.remote
  next.type = (MANAGED_TYPES.includes(row.type as ManagedType) ? row.type : 'crypt') as ManagedType
  const p = row.params ?? {}
  if (next.type === 'crypt') {
    next.cryptRemote = p.remote ?? ''
    next.filenameEncryption = p.filename_encryption ?? 'standard'
    next.directoryNameEncryption = p.directory_name_encryption !== 'false'
    next.filenameEncoding = p.filename_encoding ?? 'base64'
  } else {
    next.webdavUrl = p.url ?? ''
    next.vendor = p.vendor ?? 'other'
    next.user = p.user ?? ''
  }
  // 凭据**不预填**（S3）：留空 = 保持不变
  form.value = next
}

function cancelEdit(): void {
  editingRemote.value = null
  form.value = emptyForm()
}

function fillPassword(field: 'password' | 'password2' | 'pass'): void {
  form.value[field] = generatePassword()
}

function buildParams(): Record<string, string> {
  const f = form.value
  if (f.type === 'crypt') {
    const params: Record<string, string> = {
      remote: f.cryptRemote,
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
  return params
}

async function submit(): Promise<void> {
  const name = form.value.remoteName.trim()
  if (!name) {
    message.error(t('settings.rclone.remoteName'))
    return
  }
  submitting.value = true
  try {
    if (isEdit.value && editingRemote.value) {
      await updateRcloneConfig(editingRemote.value, {
        type: form.value.type,
        params: buildParams(),
        already_obscured: form.value.alreadyObscured,
      })
      message.success(t('settings.rclone.updated', { name: editingRemote.value }))
    } else {
      await createRcloneConfig(name, {
        type: form.value.type,
        params: buildParams(),
        already_obscured: form.value.alreadyObscured,
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
          />
        </NFormItem>

        <template v-if="form.type === 'crypt'">
          <NFormItem :label="t('settings.rclone.cryptRemote')">
            <NInput v-model:value="form.cryptRemote" :placeholder="t('settings.rclone.cryptRemotePlaceholder')" />
          </NFormItem>
          <NFormItem :label="t('settings.rclone.password')">
            <NSpace :size="8" wrap :align="'center'">
              <NInput
                v-model:value="form.password"
                type="password"
                show-password-on="click"
                :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
              />
              <NButton size="small" @click="fillPassword('password')">
                {{ t('settings.rclone.generatePassword') }}
              </NButton>
            </NSpace>
          </NFormItem>
          <NFormItem :label="t('settings.rclone.password2')">
            <NSpace :size="8" wrap :align="'center'">
              <NInput
                v-model:value="form.password2"
                type="password"
                show-password-on="click"
                :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
              />
              <NButton size="small" @click="fillPassword('password2')">
                {{ t('settings.rclone.generatePassword') }}
              </NButton>
            </NSpace>
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
          <NFormItem :label="t('settings.rclone.pass')">
            <NSpace :size="8" wrap :align="'center'">
              <NInput
                v-model:value="form.pass"
                type="password"
                show-password-on="click"
                :placeholder="isEdit ? t('settings.rclone.passwordKeepHint') : ''"
              />
              <NButton size="small" @click="fillPassword('pass')">
                {{ t('settings.rclone.generatePassword') }}
              </NButton>
            </NSpace>
          </NFormItem>
        </template>

        <NFormItem>
          <NSwitch v-model:value="form.alreadyObscured" />
          <span class="rclone-panel__inline">{{ t('settings.rclone.alreadyObscured') }}</span>
        </NFormItem>

        <NSpace>
          <NButton type="primary" :loading="submitting" @click="submit">
            {{ isEdit ? t('settings.rclone.submitEdit') : t('settings.rclone.submitNew') }}
          </NButton>
          <NButton v-if="isEdit" @click="cancelEdit">{{ t('settings.rclone.cancelEdit') }}</NButton>
        </NSpace>
      </NForm>
    </NCard>

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
.rclone-panel__inline {
  margin-left: 8px;
  font-size: 12px;
}
</style>
