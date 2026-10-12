<script setup lang="ts">
/** 向导步骤 4：调度与保留（增量设计 §5.3，抽自退役的策略页）。
 *
 * - cron 输入 + 常用模板快捷填充 + 「不调度（仅手动）」；
 * - 保留档位 keep_*（0 = 不保留该档）；
 * - 抖动与生效时机说明。
 *
 * 高级字段（group_by / one_file_system / compression / check_sample_ratio /
 * monthly_full_day / prune_interval_days）不在向导 UI 暴露：新建取服务端缺省，
 * 编辑时由 TaskWizard 从 E34b 原样往返，避免第二套校验入口。
 */

import { NButton, NInput, NInputNumber, NSpace, NSwitch, NText } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

export interface ScheduleRetentionValue {
  /** 空串 = 仅手动（不下传 cron）。 */
  cron: string
  keep_last: number
  keep_hourly: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
}

const props = defineProps<{
  modelValue: ScheduleRetentionValue
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: ScheduleRetentionValue): void
}>()

const { t } = useI18n()

function patch(partial: Partial<ScheduleRetentionValue>): void {
  emit('update:modelValue', { ...props.modelValue, ...partial })
}

/** cron 空串 ⇔ 「不调度」开关；重新开启时回填每日模板，避免空表达式。 */
function onNoScheduleChange(manualOnly: boolean): void {
  if (manualOnly) {
    patch({ cron: '' })
    return
  }
  patch({ cron: props.modelValue.cron || '0 2 * * *' })
}

const cronTemplates = computed(() => [
  { label: t('wizard.cronTemplates.daily'), value: '0 2 * * *' },
  { label: t('wizard.cronTemplates.hourly'), value: '15 * * * *' },
  { label: t('wizard.cronTemplates.weekly'), value: '0 3 * * 1' },
  { label: t('wizard.cronTemplates.monthly'), value: '0 4 1 * *' },
])

const keepFields = computed<Array<{ key: keyof ScheduleRetentionValue; label: string }>>(() => [
  { key: 'keep_last', label: t('wizard.keep_last') },
  { key: 'keep_hourly', label: t('wizard.keep_hourly') },
  { key: 'keep_daily', label: t('wizard.keep_daily') },
  { key: 'keep_weekly', label: t('wizard.keep_weekly') },
  { key: 'keep_monthly', label: t('wizard.keep_monthly') },
  { key: 'keep_yearly', label: t('wizard.keep_yearly') },
])
</script>

<template>
  <n-space vertical :size="14">
    <n-space vertical :size="6">
      <n-text strong>{{ t('wizard.cron') }}</n-text>
      <n-input
        :value="modelValue.cron"
        :placeholder="t('wizard.cron')"
        :disabled="modelValue.cron === ''"
        @update:value="(value: string) => patch({ cron: value })"
      />
      <n-space :size="4" wrap>
        <n-button
          v-for="template in cronTemplates"
          :key="template.value"
          size="tiny"
          secondary
          @click="patch({ cron: template.value })"
        >
          {{ template.label }}
        </n-button>
      </n-space>
      <n-space align="center" :size="8">
        <n-switch :value="modelValue.cron === ''" size="small" @update:value="onNoScheduleChange" />
        <n-text depth="3">{{ t('wizard.noSchedule') }}</n-text>
      </n-space>
      <n-text depth="3" class="schedule-form__note">
        {{ modelValue.cron === '' ? t('task.manual_only') : t('wizard.cronEffectiveNote') }}
      </n-text>
      <n-text depth="3" class="schedule-form__note">{{ t('wizard.jitterNote') }}</n-text>
    </n-space>

    <n-space vertical :size="6">
      <n-text strong>{{ t('wizard.retention') }}</n-text>
      <n-space :size="10" wrap>
        <n-space v-for="field in keepFields" :key="field.key" vertical :size="2" class="schedule-form__keep">
          <n-text depth="3" class="schedule-form__keep-label">{{ field.label }}</n-text>
          <n-input-number
            :value="modelValue[field.key] as number"
            :min="0"
            size="small"
            style="width: 96px"
            @update:value="(value: number | null) => patch({ [field.key]: value ?? 0 } as Partial<ScheduleRetentionValue>)"
          />
        </n-space>
      </n-space>
    </n-space>
  </n-space>
</template>

<style scoped>
.schedule-form__note {
  display: block;
  font-size: 12px;
}

.schedule-form__keep-label {
  font-size: 12px;
}
</style>
