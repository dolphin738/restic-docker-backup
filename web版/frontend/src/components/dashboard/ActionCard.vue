<script setup lang="ts">
/** 行动卡（P6 L2 共用形态层；方案 §4.5.6 / §4.7 P6 批次）。
 *
 * 形态来源：Duplicati 主页双行动卡（Backups / Restores）与 destinations 页 add-new 卡
 * （ngclient destinations.html：sh-card.add-new = plus-circle 图标 + 标题 + 描述，
 * 整卡可点 + Enter/Space，低频路径刻意用弱视觉权重）。
 *
 * 消费位：P6 仓库页「新建仓库」入口（替换卡右上角按钮）；P7 主页行动卡行。
 *
 * 交互纪律：
 * - 整卡点击 = 主动作；右按钮同一动作（@click.stop 防双触发），不引入第二语义；
 * - 禁用态整卡失焦、不可点、无 pressed 反馈；
 * - 形态类（hover / pressed / focus-visible）只在 skin.css 定义（S-4 单一 skin 文件）。
 */

import { NButton, NCard, NIcon, NText } from 'naive-ui'
import type { Component } from 'vue'

defineProps<{
  /** 左侧图标（图标库 `components/icons/icons.ts` 的成员，FunctionalComponent<SVGAttributes>）。 */
  icon?: Component
  title: string
  description?: string
  /** 右按钮文案；缺省不渲染按钮（整卡仍可点）。 */
  actionLabel?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'click'): void
}>()

function activate(): void {
  emit('click')
}
</script>

<template>
  <n-card
    class="rw-action-card"
    :class="{ 'rw-action-card--disabled': disabled }"
    :bordered="true"
    role="button"
    :tabindex="disabled ? -1 : 0"
    :aria-disabled="disabled ? 'true' : undefined"
    @click="!disabled && activate()"
    @keydown.enter="!disabled && activate()"
    @keydown.space.prevent="!disabled && activate()"
  >
    <div class="rw-action-card__body">
      <n-icon v-if="icon" :size="24" class="rw-action-card__icon">
        <component :is="icon" />
      </n-icon>
      <div class="rw-action-card__text">
        <n-text strong class="rw-action-card__title">{{ title }}</n-text>
        <n-text v-if="description" depth="3" class="rw-action-card__desc">{{ description }}</n-text>
      </div>
      <n-button
        v-if="actionLabel"
        size="small"
        secondary
        :disabled="disabled"
        class="rw-action-card__action"
        @click.stop="activate"
      >
        {{ actionLabel }}
      </n-button>
    </div>
  </n-card>
</template>
