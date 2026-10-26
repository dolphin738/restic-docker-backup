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

/** 调度方式下拉选项（owner 2026-10-26：三段 card tab 改下拉菜单——步骤 4 内容本就长，
 *  tab 条把「保留策略」推得更低；下拉只占一行，且模式数恒为 3 时无需平铺暴露）。 */
const cronModeOptions = computed(() => [
  { label: t('wizard.scheduleMode.none'), value: 'none' as CronMode },
  { label: t('wizard.scheduleMode.simple'), value: 'simple' as CronMode },
  { label: t('wizard.scheduleMode.advanced'), value: 'advanced' as CronMode },
])

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

// ---------------------------------------------------------------- G5 调度摘要（aria-live 实时播报）
/** 星期名的键序（与 cron 标准语义一致：0=周日 … 6=周六）。 */
const DOW_KEYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

/** 把当前调度态翻译成一句人话（简单模式按频率逐档翻译；高级模式回显原文，避免臆读语义）。
 *  Duplicati `schedule.component.html:23-76` 的摘要区即此范式；这里补 aria-live 播报，
 *  使改动能被屏幕阅读器即时读出（模版未做，属超越层）。 */
const scheduleSummary = computed(() => {
  if (cronMode.value === 'none') {
    return t('wizard.summaryManual')
  }
  if (cronMode.value === 'advanced') {
    return t('wizard.summaryCron', { cron: advCron.value.trim() || '—' })
  }
  const time = `${pad2(simple.hour)}:${pad2(simple.minute)}`
  switch (simple.frequency) {
    case 'hourly':
      return t('wizard.summaryHourly', { minute: pad2(simple.minute) })
    case 'every_n_hours':
      return t('wizard.summaryEveryNHours', { n: simple.intervalHours, minute: pad2(simple.minute) })
    case 'daily':
      return t('wizard.summaryDaily', { time })
    case 'every_n_days':
      return t('wizard.summaryEveryNDays', { n: simple.intervalDays, time })
    case 'weekly': {
      const days = simple.daysOfWeek.length
        ? [...simple.daysOfWeek].sort((a, b) => a - b).map((d) => t(`wizard.dow.${DOW_KEYS[d]}`)).join(', ')
        : t('wizard.summaryNoDay')
      return t('wizard.summaryWeekly', { days, time })
    }
    case 'monthly': {
      const days = simple.daysOfMonth.length
        ? [...simple.daysOfMonth].sort((a, b) => a - b).join(', ')
        : t('wizard.summaryNoDay')
      return t('wizard.summaryMonthly', { days, time })
    }
    case 'yearly': {
      const days = simple.daysOfMonth.length
        ? [...simple.daysOfMonth].sort((a, b) => a - b).join(', ')
        : t('wizard.summaryNoDay')
      return t('wizard.summaryYearly', { month: simple.month, days, time })
    }
    default:
      return t('wizard.summaryCron', { cron: simpleToCron(simple) })
  }
})

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
  <n-space vertical :size="16">
    <!-- A6 调度双模式 -->
    <n-space vertical :size="8">
      <n-text strong>{{ t('wizard.schedule') }}</n-text>

      <!-- 调度方式：下拉菜单（原三段 card tab，owner 2026-10-26 改版） -->
      <n-select
        :value="cronMode"
        :options="cronModeOptions"
        size="small"
        class="schedule-form__mode"
        @update:value="onModeChange"
      />

      <!-- 简单模式 -->
      <template v-if="cronMode === 'simple'">
        <n-space :size="8" wrap>
          <n-space vertical :size="4" class="schedule-form__field">
            <n-text depth="3" class="schedule-form__label">{{ t('wizard.freq.label') }}</n-text>
            <n-select
              v-model:value="simple.frequency"
              :options="frequencyOptions"
              size="small"
              style="width: 160px"
              @update:value="emitSimple"
            />
          </n-space>

          <n-space v-if="needsTime" vertical :size="4" class="schedule-form__field">
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

          <n-space v-if="needsMinuteOnly" vertical :size="4" class="schedule-form__field">
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

          <n-space v-if="needsIntervalHours" vertical :size="4" class="schedule-form__field">
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

          <n-space v-if="needsIntervalDays" vertical :size="4" class="schedule-form__field">
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

        <n-space v-if="needsWeekdays" vertical :size="4" class="schedule-form__field">
          <n-text depth="3" class="schedule-form__label">{{ t('wizard.scheduleWeekdays') }}</n-text>
          <n-checkbox-group v-model:value="simple.daysOfWeek" @update:value="emitSimple">
            <n-space :size="4" wrap>
              <n-checkbox v-for="opt in weekdayOptions" :key="opt.value" :value="opt.value" :label="opt.label" size="small" />
            </n-space>
          </n-checkbox-group>
        </n-space>

        <n-space v-if="needsDaysOfMonth" vertical :size="4" class="schedule-form__field">
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

        <n-space v-if="needsMonth" vertical :size="4" class="schedule-form__field">
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
        <n-space :size="8" wrap>
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

      <!-- G5 调度摘要：一句话回读当前计划（aria-live 让屏幕阅读器即时播报改动） -->
      <n-space :size="8" align="center" class="schedule-form__summary">
        <n-text depth="3">{{ t('wizard.scheduleSummary') }}：</n-text>
        <n-text strong aria-live="polite">{{ scheduleSummary }}</n-text>
      </n-space>
    </n-space>

    <!-- 保留策略 -->
    <n-space vertical :size="8">
      <n-text strong>{{ t('wizard.retention') }}</n-text>
      <n-space :size="8" wrap>
        <n-space v-for="field in keepFields" :key="field.key" vertical :size="4" class="schedule-form__keep">
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
      <n-space :size="8" wrap>
        <n-space v-for="field in withinFields" :key="field.key" vertical :size="4" class="schedule-form__keep">
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
      <n-space vertical :size="4" class="schedule-form__keep">
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

/* G5 摘要行：与提示文案同字号，单独一行便于定位；随控件变化实时更新（aria-live） */
.schedule-form__summary {
  flex-wrap: wrap;
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

/* 调度方式下拉：与其它单控件字段同宽档（限制在左半区，不铺满整行） */
.schedule-form__mode {
  width: 220px;
  max-width: 100%;
}

.schedule-form__cron {
  flex: 1 1 280px;
  /* 等宽常量收口：theme/tokens.ts fontFamilyMono（经 --font-family-mono 全局变量） */
  font-family: var(--font-family-mono);
}
</style>
