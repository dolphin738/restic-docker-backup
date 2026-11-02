<script setup lang="ts">
/**
 * 设置 · 系统域分区（log.level / db.busy_timeout_ms；候选 6 拆分）。
 *
 * env 覆盖锁定粒度是「整区」：任一键被 compose 锁定即整区禁用，
 * 避免半锁半可改的困惑（原实现同一语义）。
 */

import { useQuery } from '@tanstack/vue-query'
import { IconCheckCircle } from '@/components/icons/icons'
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NIcon,
  NInputNumber,
  NSelect,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig } from '@/api/config'
import { saveDumpBehaviorConfig } from '@/api/settings'
import { useConfigPatch } from '@/composables/useConfigPatch'

const { t } = useI18n()
const message = useMessage()

const configQuery = useQuery({ queryKey: ['config', 'settings-page'], queryFn: getConfig })

/** 按 key 取配置项（env 锁定判定用）。 */
function configItem(key: string) {
  return configQuery.data.value?.items?.find((item) => item.key === key) ?? null
}

const LOG_LEVEL_OPTIONS = [
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
]

const logLevelItem = computed(() => configItem('log.level'))
const busyTimeoutItem = computed(() => configItem('db.busy_timeout_ms'))
/** env 覆盖锁定（任一键被 compose 锁定即整区禁用，避免半锁半可改的困惑）。 */
const systemEnvLocked = computed(
  () => logLevelItem.value?.source === 'env' || busyTimeoutItem.value?.source === 'env',
)

const systemLogLevel = ref('INFO')
const systemBusyTimeout = ref<number | null>(5000)


watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  systemLogLevel.value = (item('log.level')?.value ?? 'INFO').toUpperCase()
  const rawBusy = item('db.busy_timeout_ms')?.value
  const parsed = Number(rawBusy)
  systemBusyTimeout.value = rawBusy && Number.isFinite(parsed) ? parsed : 5000
})

const systemDirty = computed(
  () =>
    systemLogLevel.value !== (logLevelItem.value?.value ?? 'INFO').toUpperCase() ||
    String(systemBusyTimeout.value ?? '') !== (busyTimeoutItem.value?.value ?? '5000'),
)

const systemPatch = useConfigPatch({
  submit: saveDumpBehaviorConfig,
  successMessage: () => t('settings.system.saved'),
  notify: (text) => message.success(text),
  refetch: () => configQuery.refetch(),
})
async function onSaveSystem(): Promise<void> {
  if (systemEnvLocked.value) {
    return
  }
  await systemPatch.save({
    'log.level': systemLogLevel.value,
    'db.busy_timeout_ms': String(systemBusyTimeout.value ?? 5000),
  })
}

</script>

<template>
    <n-card id="sec-system" class="settings__panel" :title="t('settings.system.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.system.desc') }}</n-text>
        <n-alert v-if="systemEnvLocked" type="warning" :show-icon="false">
          {{ t('settings.system.envLocked') }}
        </n-alert>
        <n-form label-placement="top" class="settings__form">
          <n-space vertical :size="12">
            <n-form-item :label="t('settings.system.logLevel')">
              <n-select
                v-model:value="systemLogLevel"
                :options="LOG_LEVEL_OPTIONS"
                :disabled="systemEnvLocked"
                size="small"
              />
            </n-form-item>
            <n-text depth="3" class="settings__hint">{{ t('settings.system.logLevelHint') }}</n-text>
            <n-form-item :label="t('settings.system.busyTimeout')">
              <n-input-number
                v-model:value="systemBusyTimeout"
                :min="100"
                :max="600000"
                :step="1000"
                :show-button="false"
                :disabled="systemEnvLocked"
                size="small"
                class="settings__number"
              />
            </n-form-item>
            <n-text depth="3" class="settings__hint">{{ t('settings.system.busyTimeoutHint') }}</n-text>
          </n-space>
          <n-space justify="end">
            <n-button
              type="primary"
              :loading="systemPatch.saving.value"
              :disabled="!systemDirty || systemEnvLocked"
              @click="onSaveSystem"
            >
              <template #icon>
                <n-icon :size="16"><IconCheckCircle /></n-icon>
              </template>
              {{ t('common.save') }}
            </n-button>
          </n-space>
        </n-form>
      </n-space>
    </n-card>
</template>

<style scoped>
.settings__form { max-width: 560px; }
.settings__number { width: 120px; }
.settings__hint { font-size: 12px; }
</style>
