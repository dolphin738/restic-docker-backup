<script setup lang="ts">
/** 危险/耗时操作的「执行中 → 结果」正文（2026-10-29）。
 *
 * 三处共用同一份呈现（释放 ID / 销毁数据 / rclone 恢复写回），风格必须一致：
 * 归档页两种承载都用它——释放/销毁嵌在 ConfirmGate 的第 3 步（awaitResult 模式），
 * 恢复写回包在自己的弹窗里（该动作非破坏性，不该走确认门，但同样要给人看到结果）。
 *
 * **刻意不给百分比**：这三个动作后端没有任何进度上报，硬凑一条会走的进度条属于
 * 假反馈（用户会以为卡在 37%）。无度量就给不定量指示（转圈 + 阶段文案）。
 */
import { NAlert, NButton, NSpin, NSpace, NText } from 'naive-ui'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps<{
  /** null = 未开始（不渲染）；running / done / failed。 */
  phase: 'running' | 'done' | 'failed' | null
  /** running 阶段文案（如「正在销毁数据…」）。 */
  progressLabel?: string
  /** done / failed 阶段的结果正文。 */
  resultText?: string
}>()

const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div class="op-result">
    <template v-if="props.phase === 'running'">
      <div class="op-result__running">
        <n-spin size="small" />
        <n-text>{{ props.progressLabel }}</n-text>
      </div>
    </template>
    <template v-else-if="props.phase === 'done' || props.phase === 'failed'">
      <n-alert :type="props.phase === 'done' ? 'success' : 'error'" :show-icon="false">
        {{ props.resultText }}
      </n-alert>
      <n-space justify="end">
        <n-button type="primary" @click="emit('close')">{{ t('common.close') }}</n-button>
      </n-space>
    </template>
  </div>
</template>

<style scoped>
.op-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.op-result__running {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
