<script setup lang="ts">
/**
 * 统一状态标签（UI界面设计方案 §6 / §10 PV-2）：圆点 6px + 文案，五语义态。
 *
 * 收口各页面散落的 `NTag type` 硬编码，分批落地：
 * - P3a：BasicLayout（仓库状态）/ TaskCard（任务状态、保留清理提示、上次结果）；
 * - P3b：jobs（含 cancelled）/ audit（含无 type 场景）；
 * - P3c：repo / settings / notifications / TierCard / JobProgress / dashboard。
 *
 * 纪律（PV-2 验收）：只做等价替换——同色同文案，不合并语义差异；
 * 圆点背景取 currentColor 继承 NTag 语义色（零硬编码色值，亮/暗与主题令牌同源）；
 * 文案由调用方经 i18n 传入，本组件不内置词条、不持有业务映射。
 */

import { NTag } from 'naive-ui'

/** 五语义态。`info` 供 audit 只读动作用（P3b）；jobs 的 `cancelled` 态由 P3b 在调用方映射落位。 */
export type StatusTagKind = 'success' | 'error' | 'warning' | 'default' | 'info'

defineProps<{
  /** 语义态：决定圆点与文字色（透传 NTag type，替换前后同色）。 */
  kind: StatusTagKind
  /** 展示文案（调用方负责 i18n）。 */
  label: string
}>()
</script>

<template>
  <n-tag size="small" round :type="kind" class="status-tag">
    <span class="status-tag__dot" aria-hidden="true" />
    {{ label }}
  </n-tag>
</template>

<style scoped>
/* 圆点 6px + currentColor：颜色随 NTag 语义态，不在本组件落任何色值 */
.status-tag__dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background: currentColor;
}
</style>
