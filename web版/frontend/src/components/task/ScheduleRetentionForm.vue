<script setup lang="ts">
/** 向导步骤 4：调度与保留（增量设计 §5.3，抽自退役的策略页）。
 *
 * 调度（A6 双模式，门槛降到 Duplicati 级）：
 * - 「不调度（仅手动）」/「简单模式」/「高级模式」三段切换；
 * - 简单模式三控件生成 cron：频率下拉 + 时间点（时/分）+ 日子多选（每周→星期、每月→几号、
 *   每年→月+几号），全程不露 cron 字样；
 * - 高级模式直编 5 字段 cron（标准 crontab 语义）；
 * - 双向解析：受控子集恒可反向解析；编辑回显时解析不了的复杂表达式自动落高级模式，不丢信息。
 *
 * 保留（沿用）：keep_* 计数档 + A2 时间线档 keep_within*（restic duration）+ keep_tags（--keep-tag）。
 * 高级字段（group_by / one_file_system / compression / check_sample_ratio /
 * monthly_full_day / prune_interval_days）不在向导 UI 暴露：新建取服务端缺省，
 * 编辑时由 TaskWizard 从 E34b 原样往返，避免第二套校验入口。
 *
 * 后端零改动：DB 存的还是 cron 字符串，调度语义/APScheduler/cron 星期修正护栏全复用。
 */

import {
  NCheckbox,
  NCheckboxGroup,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NTabPane,
  NTabs,
  NText,
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  cronToSimple,
  defaultSimpleSchedule,
  simpleToCron,
  type SimpleFrequency,
  type SimpleSchedule,
} from '@/utils/cronConverter'

export interface ScheduleRetentionValue {
  /** 空串 = 仅手动（不下传 cron）。 */
  cron: string
  keep_last: number
  keep_hourly: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
  /** A2 时间线保留（restic duration，如 30d / 1y5m7d2h；空串 = 未启用）。 */
  keep_within: string
  keep_within_hourly: string
  keep_within_daily: string
  keep_within_weekly: string
  keep_within_monthly: string
  keep_within_yearly: string
  /** A2 keep_tags 的表单层表示：逗号分隔文本；空串 = 未启用（提交时拆分为数组）。 */
  keep_tags_text: string
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

// ---------------------------------------------------------------- A6 调度双模式
type CronMode = 'none' | 'simple' | 'advanced'

const cronMode = ref<CronMode>('simple')
const simple = reactive<SimpleSchedule>(defaultSimpleSchedule())
const advCron = ref('')

/** 当前内部态对应的 cron（用于判等，打破「emit → 父回填 → watch 重同步」回环）。 */
function representedCron(): string {
  if (cronMode.value === 'none') return ''
  if (cronMode.value === 'advanced') return advCron.value.trim()
  return simpleToCron(simple)
}

/** 从 cron 字符串同步内部态（不 emit）。 */
function syncFromCron(cron: string): void {
  if (!cron) {
    cronMode.value = 'none'
    return
  }
  const parsed = cronToSimple(cron)
  if (parsed) {
    cronMode.value = 'simple'
    Object.assign(simple, parsed)
  } else {
    cronMode.value = 'advanced'
    advCron.value = cron
  }
}

// 初始同步 + 外部变更（编辑预填）同步；与自身产出判等则跳过，避免回环
watch(
  () => props.modelValue.cron,
  (cron) => {
    if (cron === representedCron()) return
    syncFromCron(cron)
  },
  { immediate: true },
)

function onModeChange(mode: CronMode): void {
  if (mode === 'none') {
    cronMode.value = 'none'
    patch({ cron: '' })
    return
  }
  if (mode === 'simple') {
    // 从高级模式切回：若当前 cron 可解析则载入，否则以默认简单调度起步
    const parsed = cronToSimple(advCron.value.trim())
    if (parsed) {
      Object.assign(simple, parsed)
    } else if (cronMode.value === 'advanced') {
      Object.assign(simple, defaultSimpleSchedule())
    }
    cronMode.value = 'simple'
    patch({ cron: simpleToCron(simple) })
    return
  }
  // advanced：以当前 cron（或简单态生成）作为可编辑原文
  advCron.value = representedCron() || simpleToCron(simple)
  cronMode.value = 'advanced'
  patch({ cron: advCron.value })
}

function emitSimple(): void {
  patch({ cron: simpleToCron(simple) })
}

function onAdvInput(): void {
  patch({ cron: advCron.value.trim() })
}

const frequencyOptions = computed(() => [
  { label: t('wizard.freq.daily'), value: 'daily' as SimpleFrequency },
  { label: t('wizard.freq.hourly'), value: 'hourly' as SimpleFrequency },
  { label: t('wizard.freq.weekly'), value: 'weekly' as SimpleFrequency },
  { label: t('wizard.freq.monthly'), value: 'monthly' as SimpleFrequency },
  { label: t('wizard.freq.yearly'), value: 'yearly' as SimpleFrequency },
  { label: t('wizard.freq.everyNHours'), value: 'every_n_hours' as SimpleFrequency },
  { label: t('wizard.freq.everyNDays'), value: 'every_n_days' as SimpleFrequency },
])

const weekdayOptions = computed(() => [
  { label: t('wizard.dow.sun'), value: 0 },
  { label: t('wizard.dow.mon'), value: 1 },
  { label: t('wizard.dow.tue'), value: 2 },
  { label: t('wizard.dow.wed'), value: 3 },
  { label: t('wizard.dow.thu'), value: 4 },
  { label: t('wizard.dow.fri'), value: 5 },
  { label: t('wizard.dow.sat'), value: 6 },
])

const dayOfMonthOptions = computed(() =>
  Array.from({ length: 31 }, (_, i) => ({ label: String(i + 1), value: i + 1 })),
)

const monthOptions = computed(() =>
  Array.from({ length: 12 }, (_, i) => ({ label: `${i + 1} 月`, value: i + 1 })),
)

/** 是否需要「时间点（时/分）」控件（hourly/every_n_hours 仅用分钟）。 */
const needsTime = computed(() =>
  ['daily', 'weekly', 'monthly', 'yearly', 'every_n_days'].includes(simple.frequency),
)
const needsMinuteOnly = computed(() => simple.frequency === 'hourly' || simple.frequency === 'every_n_hours')
const needsWeekdays = computed(() => simple.frequency === 'weekly')
const needsDaysOfMonth = computed(() => simple.frequency === 'monthly' || simple.frequency === 'yearly')
const needsMonth = computed(() => simple.frequency === 'yearly')
const needsIntervalHours = computed(() => simple.frequency === 'every_n_hours')
const needsIntervalDays = computed(() => simple.frequency === 'every_n_days')

const keepFields = computed<Array<{ key: keyof ScheduleRetentionValue; label: string }>>(() => [
  { key: 'keep_last', label: t('wizard.keep_last') },
  { key: 'keep_hourly', label: t('wizard.keep_hourly') },
  { key: 'keep_daily', label: t('wizard.keep_daily') },
  { key: 'keep_weekly', label: t('wizard.keep_weekly') },
  { key: 'keep_monthly', label: t('wizard.keep_monthly') },
  { key: 'keep_yearly', label: t('wizard.keep_yearly') },
])

/** A2 时间线档（restic duration 文本输入）：与计数档并列的第二排保留规则。 */
const withinFields = computed<Array<{ key: keyof ScheduleRetentionValue; label: string }>>(() => [
  { key: 'keep_within', label: t('wizard.keep_within') },
  { key: 'keep_within_hourly', label: t('wizard.keep_within_hourly') },
  { key: 'keep_within_daily', label: t('wizard.keep_within_daily') },
  { key: 'keep_within_weekly', label: t('wizard.keep_within_weekly') },
  { key: 'keep_within_monthly', label: t('wizard.keep_within_monthly') },
  { key: 'keep_within_yearly', label: t('wizard.keep_within_yearly') },
])
</script>

<template>
  <n-space vertical :size="14">
    <!-- A6 调度双模式 -->
    <n-space vertical :size="6">
      <n-text strong>{{ t('wizard.schedule') }}</n-text>

      <n-tabs v-model:value="cronMode" type="segment" size="small" @update:value="onModeChange">
        <n-tab-pane name="none" :tab="t('wizard.scheduleMode.none')" />
        <n-tab-pane name="simple" :tab="t('wizard.scheduleMode.simple')" />
        <n-tab-pane name="advanced" :tab="t('wizard.scheduleMode.advanced')" />
      </n-tabs>

      <!-- 简单模式 -->
      <template v-if="cronMode === 'simple'">
        <n-space :size="10" wrap>
          <n-space vertical :size="2" class="schedule-form__field">
            <n-text depth="3" class="schedule-form__label">{{ t('wizard.freq.label') }}</n-text>
            <n-select
              v-model:value="simple.frequency"
              :options="frequencyOptions"
              size="small"
              style="width: 160px"
              @update:value="emitSimple"
            />
          </n-space>

          <n-space v-if="needsTime" vertical :size="2" class="schedule-form__field">
            <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleTime') }}</n-text>
            <n-space :size="4" align="center">
              <n-input-number
                v-model:value="simple.hour"
                :min="0"
                :max="23"
                size="small"
                style="width: 72px"
                @update:value="emitSimple"
              />
              <n-text depth="3">:</n-text>
              <n-input-number
                v-model:value="simple.minute"
                :min="0"
                :max="59"
                size="small"
                style="width: 72px"
                @update:value="emitSimple"
              />
            </n-space>
          </n-space>

          <n-space v-if="needsMinuteOnly" vertical :size="2" class="schedule-form__field">
            <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleMinute') }}</n-text>
            <n-input-number
              v-model:value="simple.minute"
              :min="0"
              :max="59"
              size="small"
              style="width: 96px"
              @update:value="emitSimple"
            />
          </n-space>

          <n-space v-if="needsIntervalHours" vertical :size="2" class="schedule-form__field">
            <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleIntervalHours') }}</n-text>
            <n-input-number
              v-model:value="simple.intervalHours"
              :min="1"
              :max="24"
              size="small"
              style="width: 96px"
              @update:value="emitSimple"
            />
          </n-space>

          <n-space v-if="needsIntervalDays" vertical :size="2" class="schedule-form__field">
            <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleIntervalDays') }}</n-text>
            <n-input-number
              v-model:value="simple.intervalDays"
              :min="1"
              :max="365"
              size="small"
              style="width: 96px"
              @update:value="emitSimple"
            />
          </n-space>
        </n-space>

        <n-space v-if="needsWeekdays" vertical :size="2" class="schedule-form__field">
          <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleWeekdays') }}</n-text>
          <n-checkbox-group v-model:value="simple.daysOfWeek" @update:value="emitSimple">
            <n-space :size="4" wrap>
              <n-checkbox v-for="opt in weekdayOptions" :key="opt.value" :value="opt.value" :label="opt.label" size="small" />
            </n-space>
          </n-checkbox-group>
        </n-space>

        <n-space v-if="needsDaysOfMonth" vertical :size="2" class="schedule-form__field">
          <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleDaysOfMonth') }}</n-text>
          <n-select
            v-model:value="simple.daysOfMonth"
            :options="dayOfMonthOptions"
            multiple
            size="small"
            style="width: 100%"
            @update:value="emitSimple"
          />
        </n-space>

        <n-space v-if="needsMonth" vertical :size="2" class="schedule-form__field">
          <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleMonth') }}</n-text>
          <n-select
            v-model:value="simple.month"
            :options="monthOptions"
            size="small"
            style="width: 120px"
            @update:value="emitSimple"
          />
        </n-space>

        <n-text depth="3" class="schedule-form__note">{{ t('wizard.scheduleSimpleHint') }}</n-text>
      </template>

      <!-- 高级模式 -->
      <template v-else-if="cronMode === 'advanced'">
        <n-space :size="6" wrap>
          <n-input
            v-model:value="advCron"
            size="small"
            class="schedule-form__cron"
            :placeholder="t('wizard.cronAdvancedPlaceholder')"
            @update:value="onAdvInput"
          />
        </n-space>
        <n-text depth="3" class="schedule-form__note">{{ t('wizard.cronAdvancedNote') }}</n-text>
      </template>

      <n-text v-else depth="3" class="schedule-form__note">
        {{ t('task.manual_only') }}
      </n-text>
      <n-text depth="3" class="schedule-form__note">{{ t('wizard.jitterNote') }}</n-text>
    </n-space>

    <!-- 保留策略 -->
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

      <!-- A2 时间线保留（keep-within*）：与计数档并列，均为「至少一个保留规则」的成员 -->
      <n-text strong>{{ t('wizard.retentionWithin') }}</n-text>
      <n-space :size="10" wrap>
        <n-space v-for="field in withinFields" :key="field.key" vertical :size="2" class="schedule-form__keep">
          <n-text depth="3" class="schedule-form__keep-label">{{ field.label }}</n-text>
          <n-input
            :value="modelValue[field.key] as string"
            size="small"
            style="width: 96px"
            :placeholder="t('wizard.withinPlaceholder')"
            @update:value="(value: string) => patch({ [field.key]: value.trim() } as Partial<ScheduleRetentionValue>)"
          />
        </n-space>
      </n-space>
      <n-text depth="3" class="schedule-form__note">{{ t('wizard.withinNote') }}</n-text>

      <!-- A2 keep_tags（--keep-tag）：命中任一的快照保留；逗号分隔 -->
      <n-space vertical :size="2" class="schedule-form__keep">
        <n-text depth="3" class="schedule-form__keep-label">{{ t('wizard.keepTags') }}</n-text>
        <n-input
          :value="modelValue.keep_tags_text"
          size="small"
          :placeholder="t('wizard.keepTagsPlaceholder')"
          @update:value="(value: string) => patch({ keep_tags_text: value })"
        />
      </n-space>
      <n-text depth="3" class="schedule-form__note">{{ t('wizard.keepTagsNote') }}</n-text>
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

.schedule-form__label {
  font-size: 12px;
}

.schedule-form__field {
  margin-bottom: 4px;
}

.schedule-form__cron {
  flex: 1 1 280px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
</style>
