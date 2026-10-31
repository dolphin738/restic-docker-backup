<script setup lang="ts">
/**
 * 设置 · 行为开关分区（候选 6 拆分：原 settings/index.vue 的 behavior 段整体搬出）。
 *
 * 自包含：配置读取 / 回填 / 校验 / 保存都在本组件内。
 * 旧键回退（check_sample_ratio → 分母 t）保留原地，语义不变。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NButton,
  NCard,
  NForm,
  NFormItem,
  NIcon,
  NInputNumber,
  NSpace,
  NSwitch,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig } from '@/api/config'
import { saveDumpBehaviorConfig } from '@/api/settings'
import { IconCheckCircle } from '@/components/icons/icons'
import { useConfigPatch } from '@/composables/useConfigPatch'

const { t } = useI18n()
const message = useMessage()

// queryKey 与父页一致 → vue-query 去重，不额外发请求
const configQuery = useQuery({ queryKey: ['config', 'settings-page'], queryFn: getConfig })

const behaviorAutoInit = ref(false)
const behaviorAutoUnlock = ref(false)
const behaviorCheckEnabled = ref(false)
// 每日 n/t 滚动扫描周期天数 t（1–256；0 = 不做每日扫描，仅月度全量）
const behaviorCheckSubsetDenominator = ref<number>(30)
const behaviorMonthlyFullEnabled = ref(false)
const behaviorMonthlyFullDay = ref<number>(1)


watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  behaviorAutoInit.value = item('behavior.auto_init')?.value === '1'
  behaviorAutoUnlock.value = item('behavior.auto_unlock')?.value === '1'
  behaviorCheckEnabled.value = item('behavior.check_enabled')?.value === '1'
  // 新键优先；旧键 check_sample_ratio（已废弃的随机百分比）兜底按 t = round(1/ratio) 换算
  const rawDenominator = item('behavior.check_subset_denominator')?.value
  const denom = Number(rawDenominator)
  if (rawDenominator !== undefined && Number.isInteger(denom) && denom >= 0 && denom <= 256) {
    behaviorCheckSubsetDenominator.value = denom
  } else {
    const ratio = Number(item('behavior.check_sample_ratio')?.value)
    behaviorCheckSubsetDenominator.value =
      Number.isFinite(ratio) && ratio > 0 ? Math.min(Math.round(1 / ratio), 256) : 30
  }
  behaviorMonthlyFullEnabled.value = item('behavior.monthly_full_enabled')?.value === '1'
  const day = Number(item('behavior.monthly_full_day')?.value)
  behaviorMonthlyFullDay.value = Number.isInteger(day) && day >= 1 && day <= 28 ? day : 1
}, { immediate: true })

const behaviorDenominatorValid = computed(
  () => Number.isInteger(behaviorCheckSubsetDenominator.value) && behaviorCheckSubsetDenominator.value >= 0 && behaviorCheckSubsetDenominator.value <= 256,
)
// 派生展示：每天约读 1/t（t=0 = 不做每日扫描）；仅用于提示文案，不回写后端
const behaviorDailyPercent = computed(() => {
  const total = behaviorCheckSubsetDenominator.value
  return total > 0 ? (100 / total).toFixed(1) : '0'
})
const behaviorDayValid = computed(
  () => Number.isInteger(behaviorMonthlyFullDay.value) && behaviorMonthlyFullDay.value >= 1 && behaviorMonthlyFullDay.value <= 28,
)
const behaviorFormValid = computed(() => behaviorDenominatorValid.value && behaviorDayValid.value)

const behaviorPatch = useConfigPatch({
  submit: saveDumpBehaviorConfig,
  successMessage: () => t('settings.behavior.saved'),
  notify: (text) => message.success(text),
  refetch: () => configQuery.refetch(),
})
async function onSaveBehavior(): Promise<void> {
  if (!behaviorFormValid.value) {
    return
  }
  await behaviorPatch.save({
    'behavior.auto_init': behaviorAutoInit.value ? '1' : '0',
    'behavior.auto_unlock': behaviorAutoUnlock.value ? '1' : '0',
    'behavior.check_enabled': behaviorCheckEnabled.value ? '1' : '0',
    // 周期天数 t：每天读第 n 个桶，t 天把全仓数据块无重复、无遗漏读一遍
    'behavior.check_subset_denominator': String(behaviorCheckSubsetDenominator.value),
    'behavior.monthly_full_enabled': behaviorMonthlyFullEnabled.value ? '1' : '0',
    'behavior.monthly_full_day': String(behaviorMonthlyFullDay.value),
  })
}

</script>

<template>
    <n-card id="sec-behavior" class="settings__panel" :title="t('settings.behavior.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.behavior.desc') }}</n-text>
        <n-form label-placement="top" class="settings__form">
          <n-space vertical :size="12">
            <n-space vertical :size="4">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorAutoInit" size="small" :aria-label="t('settings.behavior.autoInit')" />
                <n-text>{{ t('settings.behavior.autoInit') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.autoInitHint') }}</n-text>
            </n-space>
            <n-space vertical :size="4">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorAutoUnlock" size="small" :aria-label="t('settings.behavior.autoUnlock')" />
                <n-text>{{ t('settings.behavior.autoUnlock') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.autoUnlockHint') }}</n-text>
            </n-space>
            <n-space vertical :size="4">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorCheckEnabled" size="small" :aria-label="t('settings.behavior.checkEnabled')" />
                <n-text>{{ t('settings.behavior.checkEnabled') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.checkEnabledHint') }}</n-text>
            </n-space>
            <n-form-item
              :label="t('settings.behavior.checkSubsetDenominator')"
              :validation-status="behaviorDenominatorValid ? undefined : 'error'"
              :feedback="behaviorDenominatorValid ? undefined : t('settings.behavior.invalidDenominator')"
              class="settings__ratio-item"
            >
              <n-space align="center" :size="8">
                <n-input-number
                  v-model:value="behaviorCheckSubsetDenominator"
                  :min="0"
                  :max="256"
                  :step="1"
                  :show-button="false"
                  class="settings__number"
                />
                <n-text depth="3">{{ t('settings.behavior.checkSubsetDenominatorHint', { percent: behaviorDailyPercent }) }}</n-text>
              </n-space>
            </n-form-item>
            <n-space vertical :size="4">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorMonthlyFullEnabled" size="small" :aria-label="t('settings.behavior.monthlyFullEnabled')" />
                <n-text>{{ t('settings.behavior.monthlyFullEnabled') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.monthlyFullEnabledHint') }}</n-text>
            </n-space>
            <n-form-item
              :label="t('settings.behavior.monthlyFullDay')"
              :validation-status="behaviorDayValid ? undefined : 'error'"
              :feedback="behaviorDayValid ? undefined : t('settings.behavior.invalidDay')"
              class="settings__ratio-item"
            >
              <n-space align="center" :size="8">
                <n-input-number
                  v-model:value="behaviorMonthlyFullDay"
                  :min="1"
                  :max="28"
                  :show-button="false"
                  :disabled="!behaviorMonthlyFullEnabled"
                  class="settings__number"
                />
                <n-text depth="3">{{ t('settings.behavior.monthlyFullDayHint') }}</n-text>
              </n-space>
            </n-form-item>
          </n-space>
          <n-space justify="end">
            <n-button type="primary" :loading="behaviorPatch.saving.value" :disabled="!behaviorFormValid" @click="onSaveBehavior">
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
.settings__ratio-item { max-width: 480px; }
.settings__hint { font-size: 12px; }
</style>
