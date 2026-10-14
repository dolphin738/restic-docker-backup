<script setup lang="ts">
/** 系统设置页（页面 11，增量设计 §4 批次 A 骨架）。
 *
 * 分区：缓存维护（E25 实装：占用展示 + ConfirmGate 一键清空 + 409 友好提示）
 * → DB dump（批次 B 占位）→ 行为开关（批次 B 占位）→ 时区（既有键只读展示）
 * → 管理员改密（E28 实装）。批次 B（T06）在 dump/behavior 分区填表单。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NForm,
  NFormItem,
  NInput,
  NSpace,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { changePassword } from '@/api/auth'
import { getConfig } from '@/api/config'
import { hasCode } from '@/api/http'
import { clearCache, getCacheUsage } from '@/api/settings'
import ConfirmGate from '@/components/ConfirmGate.vue'
import { formatDateTime } from '@/utils/datetime'

const { t } = useI18n()
const message = useMessage()

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
    // 409 JOB_CONFLICT：http 层已弹通用「已有任务进行中」，这里再给可操作指引
    if (hasCode(error, 'JOB_CONFLICT')) {
      message.warning(t('settings.cache.conflict'))
    }
    // 其余错误由 http 层全局提示兜底，不重复弹
  } finally {
    clearing.value = false
  }
}

// ---------------------------------------------------------------- 时区（既有键只读展示，本批无编辑入口）

const configQuery = useQuery({
  queryKey: ['config', 'settings-timezone'],
  queryFn: getConfig,
})

const timezoneItem = computed(() => configQuery.data.value?.items.find((item) => item.key === 'timezone') ?? null)

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

    <!-- DB dump（批次 B 占位） -->
    <n-card :title="t('settings.dump.title')" size="small">
      <n-text depth="3">{{ t('settings.dump.placeholder') }}</n-text>
    </n-card>

    <!-- 行为开关（批次 B 占位） -->
    <n-card :title="t('settings.behavior.title')" size="small">
      <n-text depth="3">{{ t('settings.behavior.placeholder') }}</n-text>
    </n-card>

    <!-- 时区（既有键只读展示） -->
    <n-card :title="t('settings.timezone.title')" size="small">
      <n-space vertical :size="8">
        <n-space align="center" :size="8">
          <n-text depth="2">{{ t('settings.timezone.current') }}：</n-text>
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
.settings__password-form {
  max-width: 420px;
}
</style>
