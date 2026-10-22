<script setup lang="ts">
/** 行卡片（P6 L2 共用形态层；方案 §4.5.6 / §4.7 P6 批次）。
 *
 * 形态来源：Duplicati home list 视图备份行（ngclient home：名称 + 类型 chip +
 * 快照数 chip / 指标区 / 操作区，整行点击 = 选中）。
 *
 * 消费位：P7 主页 list 视图任务行。
 *
 * 交互纪律（对齐仓库页 repo__dest 行卡已验证形态）：
 * - 整行点击 = select；选中行高亮且点击空转（已是选中态）；
 * - role=button + tabindex=0 + keydown.enter/space——避免 button 嵌 button 的非法 HTML
 *  （操作区由消费方经 #actions 插槽塞入，行内必有按钮）；
 * - 形态类（边框/圆角/阴影/选中高亮）只在 skin.css 定义（S-4 单一 skin 文件）。
 */

import { NText, NIcon } from 'naive-ui'
import type { Component } from 'vue'

defineProps<{
  /** 行名称（主标识）。 */
  name: string
  /** 左侧类型图标（BackendIcons 成员；缺省不渲染图标列）。 */
  icon?: Component
  /** 类型 chip 文案（如「本地」「S3」）。 */
  typeLabel?: string
  /** 快照数 chip 文案（如「12 个快照」）；缺省不渲染。 */
  snapshotLabel?: string
  /** 副描述行（地址 / 主机标识等）。 */
  description?: string
  /** 选中态（当前行）：高亮 + 点击空转。 */
  active?: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'select'): void
}>()

function activate(): void {
  emit('select')
}
</script>

<template>
  <div
    class="rw-row-card"
    :class="{ 'rw-row-card--active': active, 'rw-row-card--disabled': disabled }"
    role="button"
    :tabindex="disabled ? -1 : 0"
    :aria-current="active ? 'true' : undefined"
    :aria-disabled="disabled ? 'true' : undefined"
    @click="!disabled && !active && activate()"
    @keydown.enter="!disabled && !active && activate()"
    @keydown.space.prevent="!disabled && !active && activate()"
  >
    <n-icon v-if="icon" :size="20" class="rw-row-card__icon">
      <component :is="icon" />
    </n-icon>
    <div class="rw-row-card__body">
      <div class="rw-row-card__name">
        <n-text strong>{{ name }}</n-text>
        <span v-if="typeLabel" class="rw-chip">{{ typeLabel }}</span>
        <span v-if="snapshotLabel" class="rw-chip rw-chip--muted">{{ snapshotLabel }}</span>
      </div>
      <n-text v-if="description" depth="3" class="rw-row-card__desc">{{ description }}</n-text>
    </div>
    <div class="rw-row-card__actions">
      <slot name="actions" />
    </div>
  </div>
</template>
