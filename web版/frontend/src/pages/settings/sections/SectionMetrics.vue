<script setup lang="ts">
/**
 * 设置 · Prometheus 指标分区（R-024 / P3-B；候选 6 拆分）。
 *
 * 抓取令牌三态（同 dump.password 先例）：已设 + 清除 + 改新值，不清空即不变。
 */

import { useQuery } from '@tanstack/vue-query'
import { IconCheckCircle } from '@/components/icons/icons'
import {
  NButton,
  NCheckbox,
  NCard,
  NForm,
  NFormItem,
  NIcon,
  NSpace,
  NText,
  NTooltip,
  useMessage,
} from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig } from '@/api/config'
import { saveDumpBehaviorConfig } from '@/api/settings'
import PasswordInput from '@/components/PasswordInput.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useConfigPatch } from '@/composables/useConfigPatch'

const { t } = useI18n()
const message = useMessage()

const configQuery = useQuery({ queryKey: ['config', 'settings-page'], queryFn: getConfig })

/** 令牌三态（同 dump.password 先例）：读取回 *_set 布尔；清除提交空串、输入新值提交原文。
 * 后端只存 SHA-256 硬哈希——设置后不可读，遗失需重新生成。 */
const metricsTokenSet = ref(false)
/** 失效态（degraded 三态之一）：凭据源读不出来时 has_value=false 但不得当作「未设置」。 */
const metricsTokenDegraded = ref(false)
const metricsTokenDegradedReason = ref<string | null>(null)
const metricsTokenInput = ref('')
const metricsTokenClear = ref(false)


watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  metricsTokenSet.value = item('metrics.token')?.has_value ?? false
  metricsTokenDegraded.value = item('metrics.token')?.degraded ?? false
  metricsTokenDegradedReason.value = item('metrics.token')?.degraded_reason ?? null
}, { immediate: true })

const metricsTokenDirty = computed(() => metricsTokenClear.value || metricsTokenInput.value !== '')

const metricsPatch = useConfigPatch({
  submit: saveDumpBehaviorConfig,
  successMessage: () => t('settings.metrics.saved'),
  notify: (text) => message.success(text),
  refetch: () => configQuery.refetch(),
  afterSave: () => {
    metricsTokenInput.value = ''
    metricsTokenClear.value = false
  },
})
async function onSaveMetricsToken(): Promise<void> {
  if (!metricsTokenDirty.value) {
    return
  }
  const patch: Record<string, string | null> = {}
  if (metricsTokenClear.value) {
    patch['metrics.token'] = ''
  } else if (metricsTokenInput.value !== '') {
    patch['metrics.token'] = metricsTokenInput.value
  }
  await metricsPatch.save(patch)
}

</script>

<template>
    <n-card id="sec-metrics" class="settings__panel" :title="t('settings.metrics.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.metrics.desc') }}</n-text>
        <n-form label-placement="top" class="settings__form">
          <n-space align="center" :size="8">
            <n-text>{{ t('settings.metrics.token') }}</n-text>
            <!-- P3c 收口：抓取令牌状态徽标（失效 → 已设置 → 未设置）；原因走 tooltip 不塞进徽标 -->
            <n-tooltip v-if="metricsTokenDegraded" trigger="hover">
              <template #trigger>
                <StatusTag kind="warning" :label="t('settings.metrics.tokenDegraded')" />
              </template>
              {{
                t('settings.metrics.tokenDegradedReason', { reason: metricsTokenDegradedReason || t('common.none') })
              }}
            </n-tooltip>
            <StatusTag v-else-if="metricsTokenSet" kind="success" :label="t('settings.metrics.tokenSet')" />
            <StatusTag v-else kind="default" :label="t('settings.metrics.tokenUnset')" />
          </n-space>
          <n-form-item :label="t('settings.metrics.newToken')">
            <PasswordInput
              v-model="metricsTokenInput"
              width="100%"
              :placeholder="t('settings.metrics.newTokenPlaceholder')"
            />
          </n-form-item>
          <n-space align="center" :size="8">
            <n-checkbox v-model:checked="metricsTokenClear" :disabled="!metricsTokenSet">
              {{ t('settings.metrics.clear') }}
            </n-checkbox>
            <n-button
              type="primary"
              :loading="metricsPatch.saving.value"
              :disabled="!metricsTokenDirty"
              @click="onSaveMetricsToken"
            >
              <template #icon>
                <n-icon :size="16"><IconCheckCircle /></n-icon>
              </template>
              {{ t('settings.metrics.save') }}
            </n-button>
          </n-space>
        </n-form>
        <n-text depth="3" class="settings__hint">{{ t('settings.metrics.hint') }}</n-text>
      </n-space>
    </n-card>
</template>

<style scoped>
.settings__form { max-width: 560px; }
.settings__hint { font-size: 12px; }
</style>
