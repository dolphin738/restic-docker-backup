<script setup lang="ts">
/** 系统设置页（页面 11，增量设计 §4）。
 *
 * 分区：DB dump（批次 B 实装：dump.* 八键，密码三态沿 notify.* 先例）
 * → 行为开关（批次 B 实装：behavior.* 五键）→ 缓存维护（E25：占用展示 + ConfirmGate 清空
 * + 409 友好提示）→ 时区（既有键只读展示）→ 管理员改密（E28）。
 * dump/behavior 提交走 E07 patch 通道（api/settings.saveDumpBehaviorConfig）。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NDynamicTags,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { changePassword } from '@/api/auth'
import { getConfig } from '@/api/config'
import { hasCode, isAppError } from '@/api/http'
import { clearCache, getCacheUsage, saveDumpBehaviorConfig } from '@/api/settings'
import ConfirmGate from '@/components/ConfirmGate.vue'
import { formatDateTime } from '@/utils/datetime'

const { t } = useI18n()
const message = useMessage()

type DumpType = 'pg' | 'mysql' | 'sqlite'
const DUMP_TYPES: DumpType[] = ['pg', 'mysql', 'sqlite']

// ---------------------------------------------------------------- 配置读取（E06：dump/behavior/时区共用一份）

const configQuery = useQuery({
  queryKey: ['config', 'settings-page'],
  queryFn: getConfig,
})

/** 按 key 取配置项；E06 敏感键只回 has_value + source（S3 UI 面）。 */
function configItem(key: string) {
  return configQuery.data.value?.items.find((item) => item.key === key) ?? null
}

// ---------------------------------------------------------------- DB dump（批次 B：dump.* 八键）

const dumpEnabled = ref(false)
const dumpType = ref<DumpType>('pg')
const dumpHost = ref('')
const dumpPort = ref<number | null>(null)
const dumpUser = ref('')
const dumpDatabases = ref<string[]>([])
const dumpRetentionDays = ref<number | null>(null)
/** 密码三态：读取回 *_set 布尔；提交时「清除」提交空串、「输入新值」提交原文、都不动 = 不变。 */
const dumpPasswordSet = ref(false)
const dumpPasswordSource = ref<'ui' | 'env' | null>(null)
const dumpPasswordClear = ref(false)
const dumpPasswordInput = ref('')
const savingDump = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  dumpEnabled.value = item('dump.enabled')?.value === '1'
  const type = item('dump.type')?.value
  dumpType.value = type === 'mysql' || type === 'sqlite' ? type : 'pg'
  dumpHost.value = item('dump.host')?.value ?? ''
  const port = Number(item('dump.port')?.value)
  dumpPort.value = Number.isInteger(port) && port > 0 ? port : null
  dumpUser.value = item('dump.user')?.value ?? ''
  const databasesRaw = item('dump.databases')?.value
  try {
    const parsed: unknown = databasesRaw ? JSON.parse(databasesRaw) : []
    dumpDatabases.value = Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
  } catch {
    dumpDatabases.value = []
  }
  const retention = Number(item('dump.retention_days')?.value)
  dumpRetentionDays.value = Number.isInteger(retention) && retention >= 0 ? retention : null
  const password = item('dump.password')
  dumpPasswordSet.value = password?.has_value ?? false
  dumpPasswordSource.value = password?.source ?? null
}, { immediate: true })

const dumpTypeOptions = computed(() =>
  DUMP_TYPES.map((value) => ({ label: t(`settings.dump.type_${value}`), value })),
)

/** pg 版本不匹配提示（PRD `dump.pg_version_mismatch` 口径）：仅 pg 类型下展示。 */
const showPgVersionNote = computed(() => dumpType.value === 'pg')

const dumpFormValid = computed(() => {
  if (dumpPort.value !== null && (!Number.isInteger(dumpPort.value) || dumpPort.value <= 0)) {
    return false
  }
  return dumpRetentionDays.value !== null && Number.isInteger(dumpRetentionDays.value) && dumpRetentionDays.value >= 0
})

async function onSaveDump(): Promise<void> {
  if (!dumpFormValid.value || savingDump.value) {
    return
  }
  const patch: Record<string, string | null> = {
    'dump.enabled': dumpEnabled.value ? '1' : '0',
    'dump.type': dumpType.value,
    'dump.host': dumpHost.value.trim() || null,
    'dump.port': dumpPort.value !== null ? String(dumpPort.value) : null,
    'dump.user': dumpUser.value.trim() || null,
    'dump.databases': dumpDatabases.value.length > 0 ? JSON.stringify(dumpDatabases.value) : null,
    'dump.retention_days': dumpRetentionDays.value !== null ? String(dumpRetentionDays.value) : null,
  }
  // 密码三态：undefined = 不变（不进 patch）
  if (dumpPasswordClear.value) {
    patch['dump.password'] = ''
  } else if (dumpPasswordInput.value !== '') {
    patch['dump.password'] = dumpPasswordInput.value
  }
  savingDump.value = true
  try {
    await saveDumpBehaviorConfig(patch)
    message.success(t('settings.dump.saved'))
    dumpPasswordInput.value = ''
    dumpPasswordClear.value = false
    await configQuery.refetch()
  } catch {
    // 错误（422 校验 / ENV_OVERRIDDEN 等）由 http 层全局提示
  } finally {
    savingDump.value = false
  }
}

// ---------------------------------------------------------------- 行为开关（批次 B：behavior.* 五键）

const behaviorAutoInit = ref(false)
const behaviorAutoUnlock = ref(false)
const behaviorCheckEnabled = ref(false)
const behaviorCheckSampleRatio = ref<number>(0)
const behaviorMonthlyFullDay = ref<number>(1)
const savingBehavior = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  behaviorAutoInit.value = item('behavior.auto_init')?.value === '1'
  behaviorAutoUnlock.value = item('behavior.auto_unlock')?.value === '1'
  behaviorCheckEnabled.value = item('behavior.check_enabled')?.value === '1'
  const ratio = Number(item('behavior.check_sample_ratio')?.value)
  behaviorCheckSampleRatio.value = Number.isFinite(ratio) ? ratio : 0
  const day = Number(item('behavior.monthly_full_day')?.value)
  behaviorMonthlyFullDay.value = Number.isInteger(day) && day >= 1 && day <= 28 ? day : 1
}, { immediate: true })

const behaviorRatioValid = computed(
  () => Number.isFinite(behaviorCheckSampleRatio.value) && behaviorCheckSampleRatio.value >= 0 && behaviorCheckSampleRatio.value <= 1,
)
const behaviorDayValid = computed(
  () => Number.isInteger(behaviorMonthlyFullDay.value) && behaviorMonthlyFullDay.value >= 1 && behaviorMonthlyFullDay.value <= 28,
)
const behaviorFormValid = computed(() => behaviorRatioValid.value && behaviorDayValid.value)

async function onSaveBehavior(): Promise<void> {
  if (!behaviorFormValid.value || savingBehavior.value) {
    return
  }
  savingBehavior.value = true
  try {
    await saveDumpBehaviorConfig({
      'behavior.auto_init': behaviorAutoInit.value ? '1' : '0',
      'behavior.auto_unlock': behaviorAutoUnlock.value ? '1' : '0',
      'behavior.check_enabled': behaviorCheckEnabled.value ? '1' : '0',
      'behavior.check_sample_ratio': String(behaviorCheckSampleRatio.value),
      'behavior.monthly_full_day': String(behaviorMonthlyFullDay.value),
    })
    message.success(t('settings.behavior.saved'))
    await configQuery.refetch()
  } catch {
    // 错误（422 键级校验等）由 http 层全局提示
  } finally {
    savingBehavior.value = false
  }
}

// ---------------------------------------------------------------- 凭据加密（R-025 / P3-A）

/** 缺省开（与后端 owner 2026-10-16 裁决一致）：键缺失 = 开；仅显式 '0' 为关。 */
const credEncryption = ref(true)
/** 密码文件就位与否（E06 passfile_status）：缺失时开关置灰，启用被后端 422 拒绝。 */
const credEncryptionAvailable = ref(true)
const savingCredEncryption = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  credEncryption.value = item('security.cred_encryption')?.value !== '0'
  credEncryptionAvailable.value = data.passfile_status === 'configured'
}, { immediate: true })

/** 即时保存（单开关不设保存按钮）：失败回滚开关，错误文案由 http 层全局提示。 */
async function onToggleCredEncryption(value: boolean): Promise<void> {
  if (savingCredEncryption.value) {
    return
  }
  savingCredEncryption.value = true
  try {
    await saveDumpBehaviorConfig({ 'security.cred_encryption': value ? '1' : '0' })
    message.success(t('settings.credential.saved'))
    await configQuery.refetch()
  } catch {
    credEncryption.value = !value
  } finally {
    savingCredEncryption.value = false
  }
}

// ---------------------------------------------------------------- 缓存维护（E25，批次 A 实装）

const cacheQuery = useQuery({
  queryKey: ['cache', 'usage'],
  queryFn: getCacheUsage,
})

const usage = computed(() => cacheQuery.data.value ?? null)
const gateShow = ref(false)
const clearing = ref(false)

/** 字节数人类可读化（二进制口径 B/KiB/MiB/GiB/TiB）；非法值回落 —。 */
function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return '—'
  }
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${unitIndex === 0 ? String(value) : value.toFixed(2)} ${units[unitIndex]}`
}

async function onConfirmClear(): Promise<void> {
  clearing.value = true
  try {
    await clearCache()
    message.success(t('settings.cache.cleared'))
    await cacheQuery.refetch()
  } catch (error) {
    // silent 请求：错误由本页统一处理，消除双重 toast
    if (hasCode(error, 'JOB_CONFLICT')) {
      message.warning(t('settings.cache.conflict'))
    } else {
      message.error(isAppError(error) ? error.message : String(error))
    }
  } finally {
    clearing.value = false
  }
}

// ---------------------------------------------------------------- 时区（既有键只读展示，本批无编辑入口）

const timezoneItem = computed(() => configItem('timezone'))

// ---------------------------------------------------------------- 管理员改密（E28）

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const changing = ref(false)

/** 与首启引导同规则：至少 10 位且两类字符。 */
const newPasswordValid = computed(() => {
  const value = newPassword.value
  if (value.length < 10) {
    return false
  }
  let kinds = 0
  if (/[a-z]/.test(value)) kinds += 1
  if (/[A-Z]/.test(value)) kinds += 1
  if (/\d/.test(value)) kinds += 1
  if (/[^a-zA-Z\d]/.test(value)) kinds += 1
  return kinds >= 2
})

const passwordMismatch = computed(() => confirmPassword.value !== '' && confirmPassword.value !== newPassword.value)

const canSubmitPassword = computed(
  () => oldPassword.value !== '' && newPasswordValid.value && !passwordMismatch.value && !changing.value,
)

async function onChangePassword(): Promise<void> {
  if (!canSubmitPassword.value) {
    return
  }
  changing.value = true
  try {
    await changePassword(oldPassword.value, newPassword.value)
    message.success(t('settings.password.saved'))
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch {
    // 错误文案（PASSWORD_WRONG 等）由 http 层全局提示
  } finally {
    changing.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <!-- DB dump（批次 B 实装：dump.* 八键 + 密码三态） -->
    <n-card :title="t('settings.dump.title')" size="small">
      <n-space vertical :size="10">
        <n-text depth="3">{{ t('settings.dump.desc') }}</n-text>
        <n-alert v-if="showPgVersionNote" type="warning" :show-icon="false">
          {{ t('settings.dump.pgVersionNote') }}
        </n-alert>
        <n-form label-placement="top" class="settings__form">
          <n-space align="center" :size="8">
            <n-switch v-model:value="dumpEnabled" size="small" />
            <n-text>{{ t('settings.dump.enabled') }}</n-text>
          </n-space>
          <n-form-item :label="t('settings.dump.type')">
            <n-select v-model:value="dumpType" :options="dumpTypeOptions" class="settings__dump-type" />
          </n-form-item>
          <n-space :size="12" class="settings__grid">
            <n-form-item :label="t('settings.dump.host')" class="settings__cell">
              <n-input v-model:value="dumpHost" :placeholder="t('settings.dump.hostPlaceholder')" />
            </n-form-item>
            <n-form-item :label="t('settings.dump.port')" class="settings__cell settings__cell--narrow">
              <n-input-number v-model:value="dumpPort" :min="1" :show-button="false" class="settings__number" />
            </n-form-item>
            <n-form-item :label="t('settings.dump.user')" class="settings__cell">
              <n-input v-model:value="dumpUser" />
            </n-form-item>
          </n-space>
          <n-form-item :label="t('settings.dump.password')">
            <n-space vertical :size="6" class="settings__full">
              <n-space align="center" :size="8">
                <n-tag size="small" round :type="dumpPasswordSet ? 'success' : 'default'">
                  {{ dumpPasswordSet ? t('settings.dump.passwordSet') : t('settings.dump.passwordUnset') }}
                </n-tag>
                <n-tag v-if="dumpPasswordSource === 'env'" size="small" type="warning">
                  {{ t('settings.timezone.envNote') }}
                </n-tag>
                <n-button
                  v-if="dumpPasswordSet"
                  size="tiny"
                  quaternary
                  :type="dumpPasswordClear ? 'primary' : 'warning'"
                  @click="dumpPasswordClear = !dumpPasswordClear"
                >
                  {{ dumpPasswordClear ? t('settings.dump.passwordClearUndo') : t('settings.dump.passwordClear') }}
                </n-button>
              </n-space>
              <n-input
                v-model:value="dumpPasswordInput"
                type="password"
                show-password-on="click"
                :disabled="dumpPasswordClear"
                :placeholder="t('settings.dump.passwordNewPlaceholder')"
              />
            </n-space>
          </n-form-item>
          <n-form-item :label="t('settings.dump.databases')">
            <n-space vertical :size="4" class="settings__full">
              <n-dynamic-tags v-model:value="dumpDatabases" />
              <n-text depth="3">{{ t('settings.dump.databasesHint') }}</n-text>
            </n-space>
          </n-form-item>
          <n-form-item
            :label="t('settings.dump.retentionDays')"
            :validation-status="dumpFormValid ? undefined : 'error'"
            :feedback="dumpFormValid ? undefined : t('settings.dump.needRetention')"
          >
            <n-input-number
              v-model:value="dumpRetentionDays"
              :min="0"
              :show-button="false"
              class="settings__number"
            />
          </n-form-item>
          <n-space justify="end">
            <n-button type="primary" :loading="savingDump" :disabled="!dumpFormValid" @click="onSaveDump">
              {{ t('common.save') }}
            </n-button>
          </n-space>
        </n-form>
      </n-space>
    </n-card>

    <!-- 行为开关（批次 B 实装：behavior.* 五键） -->
    <n-card :title="t('settings.behavior.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.behavior.desc') }}</n-text>
        <n-form label-placement="top" class="settings__form">
          <n-space vertical :size="12">
            <n-space vertical :size="2">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorAutoInit" size="small" />
                <n-text>{{ t('settings.behavior.autoInit') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.autoInitHint') }}</n-text>
            </n-space>
            <n-space vertical :size="2">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorAutoUnlock" size="small" />
                <n-text>{{ t('settings.behavior.autoUnlock') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.autoUnlockHint') }}</n-text>
            </n-space>
            <n-space vertical :size="2">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorCheckEnabled" size="small" />
                <n-text>{{ t('settings.behavior.checkEnabled') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.checkEnabledHint') }}</n-text>
            </n-space>
            <n-form-item
              :label="t('settings.behavior.checkSampleRatio')"
              :validation-status="behaviorRatioValid ? undefined : 'error'"
              :feedback="behaviorRatioValid ? undefined : t('settings.behavior.invalidRatio')"
              class="settings__ratio-item"
            >
              <n-space align="center" :size="10">
                <n-input-number
                  v-model:value="behaviorCheckSampleRatio"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :show-button="false"
                  class="settings__number"
                />
                <n-text depth="3">{{ t('settings.behavior.checkSampleRatioHint') }}</n-text>
              </n-space>
            </n-form-item>
            <n-form-item
              :label="t('settings.behavior.monthlyFullDay')"
              :validation-status="behaviorDayValid ? undefined : 'error'"
              :feedback="behaviorDayValid ? undefined : t('settings.behavior.invalidDay')"
              class="settings__ratio-item"
            >
              <n-space align="center" :size="10">
                <n-input-number
                  v-model:value="behaviorMonthlyFullDay"
                  :min="1"
                  :max="28"
                  :show-button="false"
                  class="settings__number"
                />
                <n-text depth="3">{{ t('settings.behavior.monthlyFullDayHint') }}</n-text>
              </n-space>
            </n-form-item>
          </n-space>
          <n-space justify="end">
            <n-button type="primary" :loading="savingBehavior" :disabled="!behaviorFormValid" @click="onSaveBehavior">
              {{ t('common.save') }}
            </n-button>
          </n-space>
        </n-form>
      </n-space>
    </n-card>

    <!-- 凭据加密（R-025 / P3-A：密钥源自仓库密码文件，默认开） -->
    <n-card :title="t('settings.credential.title')" size="small">
      <n-space vertical :size="10">
        <n-text depth="3">{{ t('settings.credential.desc') }}</n-text>
        <n-alert v-if="!credEncryptionAvailable" type="warning" :show-icon="false">
          {{ t('settings.credential.unavailable') }}
        </n-alert>
        <n-space align="center" :size="8">
          <n-switch
            :value="credEncryption"
            size="small"
            :loading="savingCredEncryption"
            :disabled="!credEncryptionAvailable"
            @update:value="onToggleCredEncryption"
          />
          <n-text>{{ t('settings.credential.enabled') }}</n-text>
        </n-space>
        <n-text depth="3" class="settings__hint">{{ t('settings.credential.hint') }}</n-text>
      </n-space>
    </n-card>

    <!-- 缓存维护（E25） -->
    <n-card :title="t('settings.cache.title')" size="small">
      <template #header-extra>
        <n-space :size="8">
          <n-button size="small" secondary :loading="cacheQuery.isFetching.value" @click="cacheQuery.refetch()">
            {{ t('common.refresh') }}
          </n-button>
          <n-button size="small" type="warning" @click="gateShow = true">
            {{ t('settings.cache.clear') }}
          </n-button>
        </n-space>
      </template>
      <n-space vertical :size="10">
        <n-text depth="3">{{ t('settings.cache.desc') }}</n-text>
        <n-alert v-if="cacheQuery.error.value" type="error" :show-icon="false">
          {{ String(cacheQuery.error.value) }}
        </n-alert>
        <n-descriptions v-if="usage" :column="2" size="small" bordered>
          <n-descriptions-item :label="t('settings.cache.path')">{{ usage.path }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.cache.size')">
            <n-space align="center" :size="6">
              {{ formatBytes(usage.size_bytes) }}
              <n-tag v-if="usage.truncated" size="small" type="warning">{{ t('settings.cache.truncated') }}</n-tag>
            </n-space>
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.cache.items')">{{ usage.items_count }}</n-descriptions-item>
          <n-descriptions-item :label="t('settings.cache.measured')">
            {{ formatDateTime(usage.measured_at) }}
          </n-descriptions-item>
        </n-descriptions>
      </n-space>
    </n-card>

    <!-- 时区（既有键只读展示） -->
    <n-card :title="t('settings.timezone.title')" size="small">
      <n-space vertical :size="8">
        <n-space align="center" :size="8">
          <n-text depth="2">{{ t('settings.timezone.current') }}</n-text>
          <n-tag v-if="timezoneItem" size="small" round>
            {{ timezoneItem.value ?? t('settings.timezone.unset') }}
          </n-tag>
          <n-tag v-if="timezoneItem?.source === 'env'" size="small" type="warning">
            {{ t('settings.timezone.envNote') }}
          </n-tag>
          <n-text v-if="!timezoneItem" depth="3">{{ t('settings.timezone.unset') }}</n-text>
        </n-space>
        <n-text depth="3">{{ t('settings.timezone.note') }}</n-text>
      </n-space>
    </n-card>

    <!-- 管理员改密（E28） -->
    <n-card :title="t('settings.password.title')" size="small">
      <n-form label-placement="top" class="settings__password-form">
        <n-form-item :label="t('settings.password.oldPassword')">
          <n-input
            v-model:value="oldPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.password.oldPassword')"
          />
        </n-form-item>
        <n-form-item :label="t('settings.password.newPassword')">
          <n-input
            v-model:value="newPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.password.newPassword')"
          />
        </n-form-item>
        <n-form-item
          :label="t('settings.password.confirmPassword')"
          :validation-status="passwordMismatch ? 'error' : undefined"
          :feedback="passwordMismatch ? t('settings.password.mismatch') : undefined"
        >
          <n-input
            v-model:value="confirmPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.password.confirmPassword')"
          />
        </n-form-item>
        <n-text depth="3">{{ t('settings.password.passwordRule') }}</n-text>
        <n-space justify="end">
          <n-button type="primary" :loading="changing" :disabled="!canSubmitPassword" @click="onChangePassword">
            {{ t('settings.password.submit') }}
          </n-button>
        </n-space>
      </n-form>
    </n-card>

    <!-- 缓存清空确认门：预览 → 输入 CLEAR → 二次确认 -->
    <ConfirmGate
      v-model:show="gateShow"
      :title="t('settings.cache.gateTitle')"
      :keyword="'CLEAR'"
      :description="t('settings.cache.clearDesc')"
      :loading="clearing"
      @confirm="onConfirmClear"
    />
  </n-space>
</template>

<style scoped>
.settings__form {
  max-width: 560px;
}

.settings__password-form {
  max-width: 420px;
}

.settings__grid {
  flex-wrap: wrap;
}

.settings__cell {
  width: 220px;
}

.settings__cell--narrow {
  width: 120px;
}

.settings__full {
  width: 100%;
}

.settings__dump-type {
  max-width: 220px;
}

.settings__number {
  width: 120px;
}

.settings__ratio-item {
  max-width: 480px;
}

.settings__hint {
  font-size: 12px;
}
</style>
