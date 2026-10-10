<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
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
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listPolicies, updatePolicy } from '@/api/policies'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

const policiesQuery = useQuery({ queryKey: ['policies'], queryFn: () => listPolicies() })

const defaultPolicy = computed(
  () => (policiesQuery.data.value?.items ?? []).find((policy) => policy.is_default) ?? null,
)

const form = reactive({
  enabled: true,
  tag: '',
  cron: '',
  keep_last: 0,
  keep_hourly: 0,
  keep_daily: 0,
  keep_weekly: 0,
  keep_monthly: 0,
  keep_yearly: 0,
  group_by: 'host,paths,tags',
  one_file_system: false,
  compression: 'auto',
  check_sample_ratio: 0.02,
  monthly_full_day: 1,
  prune_interval_days: 7,
})

const saving = ref(false)

const cronTemplates = computed(() => [
  { label: t('policy.cronDaily'), value: '0 2 * * *' },
  { label: t('policy.cronHourly'), value: '15 * * * *' },
  { label: t('policy.cronWeekly'), value: '0 3 * * 1' },
  { label: t('policy.cronMonthly'), value: '0 4 1 * *' },
])

const compressionOptions = computed(() => [
  { label: 'auto', value: 'auto' },
  { label: 'always', value: 'always' },
  { label: 'never', value: 'never' },
])

watch(
  () => defaultPolicy.value,
  (policy) => {
    if (!policy) {
      return
    }
    form.enabled = policy.enabled
    form.tag = policy.tag ?? ''
    form.cron = policy.cron ?? ''
    form.keep_last = policy.keep_last
    form.keep_hourly = policy.keep_hourly
    form.keep_daily = policy.keep_daily
    form.keep_weekly = policy.keep_weekly
    form.keep_monthly = policy.keep_monthly
    form.keep_yearly = policy.keep_yearly
    form.group_by = policy.group_by
    form.one_file_system = policy.one_file_system
    form.compression = policy.compression
    form.check_sample_ratio = policy.check_sample_ratio
    form.monthly_full_day = policy.monthly_full_day
    form.prune_interval_days = policy.prune_interval_days
  },
  { immediate: true },
)

async function onSave(): Promise<void> {
  const policy = defaultPolicy.value
  if (!policy) {
    return
  }
  saving.value = true
  try {
    await updatePolicy(policy.id, {
      name: policy.name,
      enabled: form.enabled,
      tag: form.tag || null,
      // 备份源/用户追加排除在 /sources 页编辑，这里原样回传避免误清
      sources: policy.sources,
      excludes: policy.excludes,
      keep_last: form.keep_last,
      keep_hourly: form.keep_hourly,
      keep_daily: form.keep_daily,
      keep_weekly: form.keep_weekly,
      keep_monthly: form.keep_monthly,
      keep_yearly: form.keep_yearly,
      group_by: form.group_by,
      cron: form.cron || null,
      one_file_system: form.one_file_system,
      compression: form.compression,
      check_sample_ratio: form.check_sample_ratio,
      monthly_full_day: form.monthly_full_day,
      prune_interval_days: form.prune_interval_days,
      limit_up: policy.limit_up,
      limit_down: policy.limit_down,
    })
    message.success(t('policy.saved'))
    await policiesQuery.refetch()
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <n-alert type="info" :show-icon="false">{{ t('policy.p1Note') }}</n-alert>

    <n-grid cols="1 1100:2" :x-gap="16" :y-gap="16" responsive="screen">
      <n-gi>
        <n-card :title="t('nav.policies')" size="small">
          <n-space vertical :size="10">
            <n-space align="center" :size="8">
              <n-tag size="small" round type="success">{{ t('policy.defaultTag') }}</n-tag>
              <n-text v-if="defaultPolicy?.last_run_at" depth="3">
                {{ t('policy.lastRun') }}：{{ defaultPolicy.last_run_at }}
              </n-text>
            </n-space>

            <n-form label-placement="left" label-width="150" size="small">
              <n-form-item :label="t('policy.enabled')">
                <n-switch v-model:value="form.enabled" />
              </n-form-item>
              <n-form-item :label="t('policy.tag')">
                <n-input v-model:value="form.tag" :placeholder="t('common.none')" />
              </n-form-item>
              <n-form-item :label="t('policy.cron')">
                <n-space vertical :size="4" class="policy__field">
                  <n-input v-model:value="form.cron" :placeholder="t('policy.cronPlaceholder')" />
                  <n-space :size="4">
                    <n-button
                      v-for="template in cronTemplates"
                      :key="template.value"
                      size="tiny"
                      secondary
                      @click="form.cron = template.value"
                    >
                      {{ template.label }}
                    </n-button>
                  </n-space>
                </n-space>
              </n-form-item>
              <n-form-item :label="t('policy.groupBy')">
                <n-input v-model:value="form.group_by" />
              </n-form-item>
              <n-form-item :label="t('policy.oneFileSystem')">
                <n-switch v-model:value="form.one_file_system" />
              </n-form-item>
              <n-form-item :label="t('policy.compression')">
                <n-select v-model:value="form.compression" :options="compressionOptions" size="small" />
              </n-form-item>
              <n-form-item :label="t('policy.checkRatio')">
                <n-input-number v-model:value="form.check_sample_ratio" :min="0" :max="1" :step="0.01" size="small" />
              </n-form-item>
              <n-form-item :label="t('policy.monthlyFullDay')">
                <n-input-number v-model:value="form.monthly_full_day" :min="1" :max="28" size="small" />
              </n-form-item>
              <n-form-item :label="t('policy.pruneInterval')">
                <n-input-number v-model:value="form.prune_interval_days" :min="1" :max="365" size="small" />
              </n-form-item>
            </n-form>
          </n-space>
        </n-card>
      </n-gi>

      <n-gi>
        <n-card :title="t('policy.retentionTitle')" size="small">
          <n-form label-placement="left" label-width="150" size="small">
            <n-form-item :label="t('policy.keepLast')">
              <n-input-number v-model:value="form.keep_last" :min="0" size="small" />
            </n-form-item>
            <n-form-item :label="t('policy.keepHourly')">
              <n-input-number v-model:value="form.keep_hourly" :min="0" size="small" />
            </n-form-item>
            <n-form-item :label="t('policy.keepDaily')">
              <n-input-number v-model:value="form.keep_daily" :min="0" size="small" />
            </n-form-item>
            <n-form-item :label="t('policy.keepWeekly')">
              <n-input-number v-model:value="form.keep_weekly" :min="0" size="small" />
            </n-form-item>
            <n-form-item :label="t('policy.keepMonthly')">
              <n-input-number v-model:value="form.keep_monthly" :min="0" size="small" />
            </n-form-item>
            <n-form-item :label="t('policy.keepYearly')">
              <n-input-number v-model:value="form.keep_yearly" :min="0" size="small" />
            </n-form-item>
          </n-form>

          <n-space vertical :size="6">
            <n-text strong>{{ t('policy.limitsTitle') }}</n-text>
            <n-form label-placement="left" label-width="150" size="small">
              <n-form-item :label="t('policy.limitUp')">
                <n-space vertical :size="2" class="policy__field">
                  <n-input-number
                    :value="defaultPolicy?.limit_up ?? 0"
                    :min="0"
                    size="small"
                    disabled
                  />
                  <n-text depth="3">{{ t('policy.limitP2Note') }}</n-text>
                </n-space>
              </n-form-item>
              <n-form-item :label="t('policy.limitDown')">
                <n-input-number :value="defaultPolicy?.limit_down ?? 0" :min="0" size="small" disabled />
              </n-form-item>
            </n-form>
          </n-space>

          <n-space justify="end">
            <n-button type="primary" :loading="saving" :disabled="!defaultPolicy" @click="onSave">
              {{ t('policy.save') }}
            </n-button>
          </n-space>
        </n-card>
      </n-gi>
    </n-grid>
  </n-space>
</template>

<style scoped>
.policy__field {
  width: 100%;
}
</style>
