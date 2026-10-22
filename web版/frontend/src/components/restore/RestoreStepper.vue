<script setup lang="ts">
/** 恢复向导步骤条（P9 / 方案 §4.8.5 R4）。
 *
 * 形态来源：Duplicati `restore-flow.component.html` 的 `sh-stepper`
 * （`routerLinkActive` 高亮 + 可点击跳步）与 `.scss:44-47` 的 `.inactive`
 * （`pointer-events:none + opacity .5`）。
 *
 * 与 `n-steps` 的差异：`n-steps` **只读**（不可跳步、不可聚焦），本组件每步是
 * 原生 `<button>`——Enter/Space 与 Tab 天然可达，满足 A11y 增量要求。
 *
 * V-2 跳步校验：`enabled[key] === false` 的步骤置 `is-inactive`
 * （`pointer-events:none` + `opacity .5` + `tabindex=-1` + `aria-disabled`），
 * 即**鼠标与键盘双向不可达**，避免跳步跳过必填项。
 */

defineProps<{
  /** 步骤定义（key 与向导内部 step 对齐）。 */
  steps: Array<{ key: number; title: string }>
  /** 当前步骤 key。 */
  current: number
  /** key → 是否可跳；缺省视为全可跳。false = inactive。 */
  enabled?: Record<number, boolean>
  /** 无障碍标签（由调用方注入 i18n，本组件不依赖 i18n 实例，便于单测）。 */
  ariaLabel?: string
}>()

const emit = defineEmits<{
  (e: 'select', key: number): void
}>()

function isEnabled(key: number, enabled?: Record<number, boolean>): boolean {
  return enabled ? enabled[key] !== false : true
}

function select(key: number, enabled?: Record<number, boolean>): void {
  if (!isEnabled(key, enabled)) {
    return
  }
  emit('select', key)
}
</script>

<template>
  <nav class="restore-stepper" :aria-label="ariaLabel">
    <ol class="restore-stepper__list">
      <li v-for="(item, index) in steps" :key="item.key" class="restore-stepper__item">
        <button
          type="button"
          class="restore-stepper__btn"
          :class="{
            'is-current': item.key === current,
            'is-done': item.key < current,
            'is-inactive': !isEnabled(item.key, enabled),
          }"
          :aria-current="item.key === current ? 'step' : undefined"
          :aria-disabled="!isEnabled(item.key, enabled) ? 'true' : undefined"
          :tabindex="isEnabled(item.key, enabled) ? 0 : -1"
          @click="select(item.key, enabled)"
        >
          <span class="restore-stepper__index">{{ index + 1 }}</span>
          <span class="restore-stepper__title">{{ item.title }}</span>
        </button>
      </li>
    </ol>
  </nav>
</template>

<style scoped>
.restore-stepper__list {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
  flex-wrap: wrap;
}

.restore-stepper__item {
  display: flex;
  align-items: center;
}

/* 连接线：除最后一步外都画（配色跟随主题，不硬编码） */
.restore-stepper__item:not(:last-child)::after {
  content: '';
  width: 24px;
  height: 1px;
  margin-left: 8px;
  background: var(--n-border-color, rgba(128, 128, 128, 0.35));
}

.restore-stepper__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font: inherit;
  cursor: pointer;
  /* 不设 color：继承当前主题文本色（暗色皮肤挂在 documentElement，继承有效） */
}

.restore-stepper__index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1;
  background: var(--n-action-color, rgba(128, 128, 128, 0.16));
}

.restore-stepper__title {
  font-size: 13px;
}

.restore-stepper__btn.is-current {
  font-weight: 600;
}

.restore-stepper__btn.is-current .restore-stepper__index {
  background: var(--n-color-primary, #3d5bd9);
  color: #fff;
}

.restore-stepper__btn.is-done .restore-stepper__index {
  background: var(--n-color-success, #15803d);
  color: #fff;
}

/* inactive：沿用 Duplicati 模版语义——鼠标与键盘双向不可达 */
.restore-stepper__btn.is-inactive {
  opacity: 0.5;
  pointer-events: none;
}

.restore-stepper__btn:focus-visible {
  outline: 2px solid var(--n-color-primary, #3d5bd9);
  outline-offset: 2px;
}
</style>
