<script setup lang="ts">
/** 全站向导统一步骤条（P20：自 `restore/RestoreStepper.vue` 提升为共用件）。
 *
 * 形态来源：Duplicati `restore-flow.component.html` 的 `sh-stepper`
 * （`routerLinkActive` 高亮 + 可点击跳步）与 `.scss:44-47` 的 `.inactive`
 * （`pointer-events:none`）；步骤序号圆形徽标 + 文案是本项目的统一语言，
 * 恢复向导 / 任务向导（新建·编辑）/ 首次引导 / 三步确认门四处共用同一份实现。
 *
 * 与 `n-steps` 的差异：`n-steps` **只读**（不可跳步、不可聚焦），本组件每步是
 * 原生 `<button>`——Enter/Space 与 Tab 天然可达，满足 A11y 增量要求。
 *
 * V-2 跳步校验：`enabled[key] === false` 的步骤置 `is-inactive`
 * （`pointer-events:none` + `tabindex=-1` + `aria-disabled`），
 * 即**鼠标与键盘双向不可达**，避免跳步跳过必填项。
 *
 * ⚠️ 对比度（2026-10-26 实测取证 `.e2e/p20-stepper-contrast.cjs`）：
 * 裸 `<button>` 的 UA 默认 `color: buttontext` **恒为黑**，暗色主题下黑字压 #14161C
 * 只有 1.16:1（截图「恢复选项/执行与结果看不清」的真因）。故本组件**显式**取自持
 * `--rw-text-*` / `--rw-on-accent`，既不靠继承（index.html 为防首屏白闪把 #app 文本色
 * 钉成浅色，亮色主题下会反噬）也不靠 `--n-*`（只存在于 naive 组件根，普通 div 取不到）。
 * 旧实现用 `opacity:.5` 表示不可跳，暗色下同样掉到 AA 以下——改为降级文本色（text-2）。
 */

defineProps<{
  /** 步骤定义（key 与向导内部 step 对齐）；description 可选（首启引导用）。 */
  steps: Array<{ key: number; title: string; description?: string }>
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

<!-- ⚠️ 根元素必须唯一且是 <nav>：调用方的 tabindex / aria-label / @keydown 靠 attribute
     透传落到它身上。模板里 nav 之前**不能有任何节点（含 HTML 注释）**——注释会让模板变成
     片段根，Vue 对片段根既不透传事件监听也把 $el 退化成占位节点（实测：@keydown 静默丢失，
     任务向导的数字键跳步就此失效；VTU 里 w.element.tagName 由 NAV 变 DIV 即此征兆）。 -->
<template>
  <nav class="wizard-stepper" :aria-label="ariaLabel">
    <ol class="wizard-stepper__list">
      <li v-for="(item, index) in steps" :key="item.key" class="wizard-stepper__item">
        <button
          type="button"
          class="wizard-stepper__btn"
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
          <span class="wizard-stepper__index">{{ index + 1 }}</span>
          <span class="wizard-stepper__text">
            <span class="wizard-stepper__title">{{ item.title }}</span>
            <span v-if="item.description" class="wizard-stepper__desc">{{ item.description }}</span>
          </span>
        </button>
      </li>
    </ol>
  </nav>
</template>

<style scoped>
.wizard-stepper__list {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
  flex-wrap: wrap;
}

.wizard-stepper__item {
  display: flex;
  align-items: center;
}

/* 连接线：除最后一步外都画。取自持 --rw-border（naive 的 --n-border-color 只在组件根上存在，
   这里解析不到会静默退回灰色半透明，暗色下几乎看不见——tokens.spec ⑥ 禁裸 --n-*）。 */
.wizard-stepper__item:not(:last-child)::after {
  content: '';
  width: 24px;
  height: 1px;
  margin-left: 8px;
  background: var(--rw-border);
}

.wizard-stepper__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font: inherit;
  /* 显式取主题文本色：裸 button 的 UA 默认 buttontext 恒黑，暗色主题下不可读（P20 实测） */
  color: var(--rw-text-1);
  cursor: pointer;
  transition: background-color 0.15s var(--rw-ease);
}

.wizard-stepper__btn:hover:not(.is-inactive) {
  background: var(--rw-surface-hover);
}

.wizard-stepper__index {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1;
  background: var(--rw-surface-hover);
  color: inherit;
}

.wizard-stepper__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.wizard-stepper__title {
  font-size: 13px;
  white-space: nowrap;
}

/* 步骤描述（首启引导等）：弱化小字，与标题拉开层级；不设 nowrap（长描述换行） */
.wizard-stepper__desc {
  font-size: 12px;
  color: var(--rw-text-2);
}

.wizard-stepper__btn.is-current {
  font-weight: 600;
}

.wizard-stepper__btn.is-current .wizard-stepper__index {
  background: var(--rw-primary);
  color: var(--rw-on-accent);
}

.wizard-stepper__btn.is-done .wizard-stepper__index {
  background: var(--rw-success);
  color: var(--rw-on-accent);
}

/* inactive：沿用 Duplicati 模版语义——鼠标与键盘双向不可达。
   弱化手段是**降级文本色**而非 opacity：opacity .5 会把暗色下的浅字压到 1.09:1
   （P20 实测，AA 远不达标），text-2 在亮/暗两主题下均 ≥ 4.5。 */
.wizard-stepper__btn.is-inactive {
  color: var(--rw-text-2);
  cursor: default;
  pointer-events: none;
}

.wizard-stepper__btn:focus-visible {
  outline: 2px solid var(--rw-focus);
  outline-offset: 2px;
}
</style>
