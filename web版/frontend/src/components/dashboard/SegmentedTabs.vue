<script setup lang="ts">
/** 分段控件（P6 L2 共用形态层；方案 §4.7 P6 批次，验收门「分段选中态」spec 的对策组件）。
 *
 * 从设置页私有分段条提升为共用组件（S-4：禁止逐页各写一份）。原设置页形态 =
 * NButton 主/次态横排 + #sec-<id> 哈希深链（replaceState，不触发 vue-router）；
 * 本组件把该形态与深链能力通用化，页面只提供 items 与 v-model。
 *
 * 消费位：P6 设置页（替换私有实现）；P8–P11 恢复向导步骤条可复用。
 *
 * a11y：容器 role=group，每按钮 aria-pressed 表达选中态（按钮原生可聚焦）；
 * 深链读档仅认 items 内 id（未知 id 忽略——载荷防御，沿用原设置页口径）。
 */

import { NButton, NSpace } from 'naive-ui'
import { onMounted, watch } from 'vue'

export interface SegmentedItem {
  id: string
  label: string
}

const props = withDefaults(
  defineProps<{
    items: SegmentedItem[]
    modelValue: string
    /** 给定时启用 #<hashPrefix>-<id> 深链（history.replaceState，不触发路由跳转）。 */
    hashPrefix?: string
    size?: 'small' | 'medium'
    ariaLabel?: string
  }>(),
  { size: 'small' },
)

const emit = defineEmits<{
  (e: 'update:modelValue', id: string): void
  (e: 'change', id: string): void
}>()

function select(id: string): void {
  // 未知 id 忽略（分段条按钮事件载荷防御）；已选中则不重复发射
  if (id === props.modelValue || !props.items.some((item) => item.id === id)) {
    return
  }
  emit('update:modelValue', id)
  emit('change', id)
}

function syncHash(id: string): void {
  if (!props.hashPrefix || typeof history === 'undefined') {
    return
  }
  history.replaceState(null, '', `#${props.hashPrefix}-${id}`)
}

onMounted(() => {
  if (!props.hashPrefix || typeof location === 'undefined') {
    return
  }
  // 深链直达：带 #<prefix>-<id> 打开（书签 / 分享链接）时落到对应分段。
  // 无 hash 或未知 id 时保持调用方初值、不主动补写地址栏——与原设置页私有实现
  // 逐字等价（切换时才经 watch → syncHash 写栏）。
  const hash = location.hash.replace(`#${props.hashPrefix}-`, '')
  if (props.items.some((item) => item.id === hash)) {
    select(hash)
  }
})

// 外部改 modelValue（含本组件 emit 的往返）同样同步哈希，保证地址栏与态一致
watch(
  () => props.modelValue,
  (id) => syncHash(id),
)
</script>

<template>
  <n-space :size="6" :wrap="false" class="rw-segmented" role="group" :aria-label="ariaLabel">
    <n-button
      v-for="item in items"
      :key="item.id"
      :size="size"
      :type="modelValue === item.id ? 'primary' : 'default'"
      :secondary="modelValue !== item.id"
      :aria-pressed="modelValue === item.id ? 'true' : 'false'"
      @click="select(item.id)"
    >
      {{ item.label }}
    </n-button>
  </n-space>
</template>
