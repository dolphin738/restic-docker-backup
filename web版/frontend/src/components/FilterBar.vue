<script setup lang="ts">
/** 筛选条（P6 形态层补员）：筛选卡外壳 + 控件插槽（默认 slot）+ 应用/重置按钮。
 *
 * 存在理由：任务历史页与快照页各自写了一份「筛选卡 + 应用/重置」，视觉与行为双重漂移
 * （快照页首版把下拉挤成 0 宽）→ 收敛为单一组件（S-4：形态层禁止页内各写一份）。
 *
 * 约定：
 * - 控件（select / date-picker…）由调用方经**默认插槽**提供——本组件只管外壳与按钮，
 *   不感知筛选字段语义；
 * - 控件宽度由调用方 CSS 定（**固定 width**，flex 容器里 max-width 会被按钮挤成 0 宽，
 *   两个页面各踩过一次）；
 * - draft 语义由调用方持有：本组件只 emit `apply` / `reset`，不做任何查询。
 */
import { NButton, NCard, NIcon, NSpace } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { IconFilter } from '@/components/icons/icons'

defineProps<{
  /** 应用按钮 loading（通常绑查询的 isFetching）。 */
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'apply'): void
  (e: 'reset'): void
}>()

const { t } = useI18n()
</script>

<template>
  <n-card size="small">
    <n-space align="center" :size="8" class="filterbar">
      <slot />
      <n-button size="small" type="primary" :loading="loading" @click="emit('apply')">
        <template #icon>
          <n-icon :size="16"><IconFilter /></n-icon>
        </template>
        {{ t('common.applyFilters') }}
      </n-button>
      <n-button size="small" quaternary @click="emit('reset')">
        {{ t('common.reset') }}
      </n-button>
    </n-space>
  </n-card>
</template>

<style scoped>
/* 控件多时换行（窄屏），与任务历史页 .jobs__filters 同语义 */
.filterbar {
  flex-wrap: wrap;
}
</style>
