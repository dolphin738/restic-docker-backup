<script setup lang="ts">
/** 恢复选项折叠卡（P16 / UI全量改造方案 §4.9）——复刻 Duplicati `app-toggle-card`：
 *  header 点击折叠、caret 旋转 180°（200ms）、内容 max-height 0↔1000px（250ms linear）；
 *  `disableToggle` = 强制展开（Duplicati 卡片①② 同款，禁折叠时无 caret、无可点击语义）。
 *
 *  表面复用 n-card（A+ 彻底档卡片 chrome：圆角/阴影/边框走 tokens.ts + skin.css 单一源），
 *  不新造卡片容器（S-4）。标题由调用方注入 i18n 文案，组件自身不依赖 i18n（便于单测）。
 */

import { NCard, NIcon } from 'naive-ui'
import { computed } from 'vue'
import { IconChevronDown } from '@/components/icons/icons'

const props = defineProps<{
  /** 卡片标题（调用方传 i18n 文案）。 */
  title: string
  /** true = 禁折叠恒展开（Duplicati disableToggle）。 */
  disableToggle?: boolean
}>()

/** 展开态（v-model:open）；disableToggle 时恒 true。 */
const open = defineModel<boolean>('open', { default: true })

const expanded = computed(() => props.disableToggle === true || open.value)

function toggle(): void {
  if (props.disableToggle === true) {
    return
  }
  open.value = !open.value
}
</script>

<template>
  <n-card size="small" class="restore-opt-card" :class="{ 'restore-opt-card--closed': !expanded }">
    <template #header>
      <!-- 可折叠态才是 button（键盘可达 + aria-expanded）；禁折叠态退化为纯标题行，
           不给「焦点可达却无动作」的假控件（a11y-baseline 口径）。 -->
      <button
        v-if="!disableToggle"
        type="button"
        class="restore-opt-card__btn"
        :aria-expanded="expanded ? 'true' : 'false'"
        @click="toggle"
      >
        <span class="restore-opt-card__title">{{ title }}</span>
        <n-icon class="restore-opt-card__caret" :class="{ 'is-open': expanded }">
          <IconChevronDown />
        </n-icon>
      </button>
      <span v-else class="restore-opt-card__title">{{ title }}</span>
    </template>

    <!-- 折叠容器：max-height 动画（Duplicati .content 同款数值）；收起时内容仍在 DOM
         （与 Duplicati 一致，便于测试断言），父级清掉内容槽底内边距避免留空条。 -->
    <div class="restore-opt-card__collapse" :class="{ 'is-open': expanded }">
      <div class="restore-opt-card__body">
        <slot />
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.restore-opt-card__btn {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.restore-opt-card__btn:focus-visible {
  outline: 2px solid var(--n-color-primary, #3d5bd9);
  outline-offset: 2px;
}

.restore-opt-card__title {
  font-size: 14px;
  font-weight: 500;
}

.restore-opt-card__caret {
  flex: none;
  transform: rotate(0);
  transform-origin: center;
  transition: transform 200ms ease;
}

.restore-opt-card__caret.is-open {
  transform: rotate(180deg);
}

/* Duplicati .content：flex 列 + gap 16，无顶内边距（header 自带内边距） */
.restore-opt-card__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 折叠动画：max-height 0↔1000px + 250ms linear（Duplicati toggle-card.component.scss 同值） */
.restore-opt-card__collapse {
  overflow: hidden;
  max-height: 0;
  transition: max-height 250ms linear;
}

.restore-opt-card__collapse.is-open {
  max-height: 1000px;
}

/* 收起态清掉 n-card 内容槽底内边距（--n-padding-bottom），否则折叠后留一条空白 */
.restore-opt-card--closed :deep(.n-card__content) {
  padding-bottom: 0;
}
</style>
