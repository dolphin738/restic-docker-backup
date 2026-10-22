<script setup lang="ts">
/** 完成态进度圆环（P11 / R9；对标 Duplicati restore-progress 的完成态 SVG 圆环）。
 *
 * 纯展示组件：进度百分比 + 状态语义色 + 中心文案。
 * 不依赖 i18n 实例（文案由调用方注入），便于单测；配色走主题变量，不硬编码。
 */

import { computed } from 'vue'

const RADIUS = 34
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

const props = defineProps<{
  /** 0–100；完成态通常传 100（进度语义由调用方决定）。 */
  percent: number
  /** success=成功 / error=失败 / info=进行中。 */
  status: 'success' | 'error' | 'info'
  /** 圆环中心文案（调用方注入 i18n）。 */
  label?: string
}>()

/** 归一化到 0–100：外部传越界值不产生负偏移（避免出现反向圆弧）。 */
const clamped = computed(() => Math.min(100, Math.max(0, props.percent)))
const offset = computed(() => CIRCUMFERENCE * (1 - clamped.value / 100))
</script>

<template>
  <div class="ring" role="img" :aria-label="label ?? ''">
    <svg class="ring__svg" viewBox="0 0 80 80" aria-hidden="true">
      <circle class="ring__track" cx="40" cy="40" :r="34" />
      <circle
        class="ring__value"
        :class="`is-${status}`"
        cx="40"
        cy="40"
        :r="34"
        :stroke-dasharray="CIRCUMFERENCE"
        :stroke-dashoffset="offset"
      />
    </svg>
    <span v-if="label" class="ring__label">{{ label }}</span>
  </div>
</template>

<style scoped>
.ring {
  position: relative;
  width: 80px;
  height: 80px;
  flex: none;
}

.ring__svg {
  width: 100%;
  height: 100%;
  /* 从 12 点方向起笔 */
  transform: rotate(-90deg);
}

.ring__track {
  fill: none;
  stroke: var(--n-action-color, rgba(128, 128, 128, 0.16));
  stroke-width: 8;
}

.ring__value {
  fill: none;
  stroke-width: 8;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.4s ease;
}

.ring__value.is-success {
  stroke: var(--n-color-success, #15803d);
}

.ring__value.is-error {
  stroke: var(--n-color-error, #b91c1c);
}

.ring__value.is-info {
  stroke: var(--n-color-info, #3d5bd9);
}

.ring__label {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  text-align: center;
  line-height: 1.2;
  padding: 0 6px;
  /* 不设 color：继承当前主题文本色 */
}
</style>
