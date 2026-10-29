<script setup lang="ts">
import { NAlert, NButton, NCard, NInput, NModal, NSpace, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import OpResultBody from '@/components/OpResultBody.vue'
import WizardStepper from '@/components/WizardStepper.vue'

/** 危险操作确认门（§5 贯穿性安全交互规范）：预览 →（可选）输入确认文本 → 二次确认。
 * 预览区是可插拔插槽（#preview），无预览数据也能工作（只显示 description）。
 *
 * keyword 缺省 = **无关键词单次确认**（A7 原位恢复，对齐 Duplicati 无关键词确认）：
 * 跳过关键词步，预览后直达二次确认；其余危险操作仍走完整三门。
 *
 * **结果阶段**（awaitResult，2026-10-29）：默认行为是「点确认 → 立刻关门 → 结果靠
 * toast」，对**耗时不可逆**的操作（释放 ID / 销毁数据 / 恢复写回）等于「点了没反应」
 * ——弹窗瞬间消失，用户既看不到进度也不知道成败。开 `awaitResult` 后弹窗不自动关，
 * 由父组件驱动 `phase` 依次展示 执行中 → 成功/失败 结果，父组件决定何时关门。
 */

const props = defineProps<{
  show: boolean
  title: string
  /** 用户必须原样输入的确认文本（建议用大写动作词，如 UNLOCK / RESTORE）。
   * 缺省 = 无关键词单次确认（跳过第 2 步）。 */
  keyword?: string
  description?: string
  /** 确认动作执行中：禁用确认按钮并显示 loading。 */
  loading?: boolean
  /** 第三步确认按钮文案；缺省用 confirm.confirmBtn（删除门等场景覆盖，如「确认删除」）。 */
  confirmLabel?: string
  /** 第三步确认按钮颜色：危险动作用 `error`（默认），非破坏性动作（如恢复写回）用 `primary`。 */
  confirmType?: 'error' | 'warning' | 'primary'
  /** true：点确认后**不自动关门**，改由 `phase` 驱动结果阶段。
   *  缺省 false = 旧行为（瞬时动作的结果靠 toast 就够）。 */
  awaitResult?: boolean
  /** 执行结果阶段；null = 未开始。仅 `awaitResult` 为真时生效。 */
  phase?: 'running' | 'done' | 'failed' | null
  /** running 阶段正文（如「正在销毁数据…」）。 */
  progressLabel?: string
  /** done / failed 阶段的结果正文（成功给处置摘要，失败给原因）。 */
  resultText?: string
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'confirm'): void
}>()

const { t } = useI18n()

/** 1=预览 2=输入确认文本（有关键词时） 3=二次确认 */
const step = ref(1)
const typed = ref('')

const hasKeyword = computed(() => typeof props.keyword === 'string' && props.keyword.length > 0)
const keywordOk = computed(() => typed.value.trim() === props.keyword)

/** 结果阶段（只有 awaitResult 时才接管第三步的正文；否则恒为 null = 走旧路径）。 */
const resultPhase = computed(() => (props.awaitResult ? (props.phase ?? null) : null))

/** 步骤定义（P20：WizardStepper 消费）。key 保持 1/2/3 与 step 变量对齐——
 *  无关键词时第 2 步缺位，序号仍按实际步序连续重排（数组决定序号，不做跳号）。 */
const stepItems = computed(() => {
  const items: Array<{ key: number; title: string }> = [{ key: 1, title: t('confirm.stepPreview') }]
  if (hasKeyword.value) {
    items.push({ key: 2, title: t('confirm.stepKeyword') })
  }
  items.push({ key: 3, title: t('confirm.stepFinal') })
  return items
})

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      step.value = 1
      typed.value = ''
    }
  },
)

function close(): void {
  emit('update:show', false)
}

function onConfirm(): void {
  emit('confirm')
  // awaitResult：不关门，等父组件把 phase 推到 done/failed 并展示结果后再由用户关
  if (!props.awaitResult) {
    close()
  }
}
</script>

<template>
  <n-modal :show="show" @update:show="close">
    <n-card style="width: 560px" :title="title" closable @close="close">
      <!-- P20：统一走 WizardStepper（与恢复向导同款序号徽标 + 文案）。
           条件步（有关键词才有第 2 步）由 steps 数组本身表达，序号随数组连续重排。 -->
      <WizardStepper :steps="stepItems" :current="step" :aria-label="title" />

      <div class="confirm__body">
        <!-- 结果阶段（awaitResult）优先级**高于**步进分支：动作一旦开跑，弹窗里就只该有
             执行状态与结果，不应再显示预览/关键词/确认按钮。放在首位还能防住「未经步进
             直接触发 confirm」时用户看到的仍是第 1 步（那样等于还是没有反馈）。 -->
        <template v-if="resultPhase">
          <OpResultBody
            :phase="resultPhase"
            :progress-label="progressLabel || t('confirm.running')"
            :result-text="resultText ?? ''"
            @close="close"
          />
        </template>

        <!-- 第 1 步：预览（可插拔） -->
        <template v-else-if="step === 1">
          <n-alert v-if="description" type="warning" :show-icon="false">{{ description }}</n-alert>
          <slot name="preview" />
          <n-space justify="end">
            <n-button quaternary @click="close">{{ t('common.cancel') }}</n-button>
            <!-- 无关键词（A7 原位恢复）：预览后直达二次确认 -->
            <n-button type="primary" @click="hasKeyword ? (step = 2) : (step = 3)">
              {{ t('confirm.next') }}
            </n-button>
          </n-space>
        </template>

        <!-- 第 2 步：输入确认文本（仅有关键词时） -->
        <template v-else-if="step === 2 && hasKeyword">
          <n-text depth="3">{{ t('confirm.keywordHint', { keyword }) }}</n-text>
          <n-input v-model:value="typed" :placeholder="keyword" />
          <n-space justify="end">
            <n-button @click="step = 1">{{ t('confirm.back') }}</n-button>
            <n-button type="warning" :disabled="!keywordOk" @click="step = 3">
              {{ t('confirm.next') }}
            </n-button>
          </n-space>
        </template>

        <!-- 第 3 步：二次确认 -->
        <template v-else>
          <n-alert type="warning" :show-icon="false">{{ t('confirm.finalHint') }}</n-alert>
          <n-space justify="end">
            <n-button @click="hasKeyword ? (step = 2) : (step = 1)">{{ t('confirm.back') }}</n-button>
            <n-button :type="props.confirmType ?? 'error'" :loading="loading" @click="onConfirm">
              {{ props.confirmLabel ?? t('confirm.confirmBtn') }}
            </n-button>
          </n-space>
        </template>
      </div>
    </n-card>
  </n-modal>
</template>

<style scoped>
.confirm__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}
</style>
