<script setup lang="ts">
/** 三点菜单（P6 L2 共用形态层；方案 §4.5.6 / §4.7 P6 批次，验收门「菜单分组渲染」spec 的对策组件）。
 *
 * 形态来源：Duplicati home 备份行 ⋯ 菜单（ngclient：Operations / Advanced / Reporting
 * 分组 + 组间 divider；行内操作承载，不承载页面导航）。
 *
 * 消费位：P7 主页 list 视图行操作（Restore / Export / Delete / Verify / Log 分组落下）。
 *
 * 条目图标态（方案 §4.5.6「loading/success 图标态」）：loading → 小号 spinner 且条目禁用；
 * success → 勾选图标。二者由本组件统一渲染，消费方只给布尔态。
 * 菜单分组标题样式（弱化小字）只在 skin.css 定义（S-4 单一 skin 文件）。
 *
 * 触发器形态（owner 2026-10-24）：与同行「开始 / 编辑」两键**同一形态档**——
 * size="small" + 有边框的 default 档 + 三点图标，不再是 tiny quaternary 的裸「⋯」字符
 * （旧形态无边框、尺寸更小，与相邻的按钮明显不同档）。
 */

import { NButton, NDropdown, NIcon, NSpin } from 'naive-ui'
import type { Component, VNodeChild } from 'vue'
import { computed, h } from 'vue'
import { IconCheckCircle, IconMore } from '@/components/icons/icons'

export interface MenuAction {
  key: string
  label: string
  /** 条目图标（常态；loading/success 态优先于它渲染）。 */
  icon?: Component
  disabled?: boolean
  /** 进行态：spinner 图标 + 条目禁用（防重复提交）。 */
  loading?: boolean
  /** 成功态：勾选图标（一次性反馈，如「已导出」）。 */
  success?: boolean
}

export interface MenuGroup {
  /** 组标题；缺省该组无标题（首组常无标题，对齐 Duplicati）。 */
  label?: string
  actions: MenuAction[]
}

const props = defineProps<{
  groups: MenuGroup[]
  ariaLabel?: string
}>()

const emit = defineEmits<{
  (e: 'select', key: string): void
}>()

function actionIcon(action: MenuAction): (() => VNodeChild) | undefined {
  if (action.loading) {
    return () => h(NSpin, { size: 14 })
  }
  if (action.success) {
    return () => h(NIcon, { size: 16 }, { default: () => h(IconCheckCircle) })
  }
  if (action.icon) {
    return () => h(NIcon, { size: 16 }, { default: () => h(action.icon as Component) })
  }
  return undefined
}

/** 组装 dropdown options：组（type=group + children）+ 组间 divider。
 *  key 加前缀防与消费方业务 key 相撞（group/divider 不参与 select）。 */
const options = computed(() =>
  props.groups.flatMap((group, gi) => {
    const groupOption = {
      type: 'group' as const,
      key: `__group_${gi}`,
      label: group.label,
      children: group.actions.map((action) => ({
        label: action.label,
        key: action.key,
        disabled: action.disabled || action.loading,
        icon: actionIcon(action),
      })),
    }
    const divider =
      gi < props.groups.length - 1 ? [{ type: 'divider' as const, key: `__div_${gi}` }] : []
    return [groupOption, ...divider]
  }),
)

function onSelect(key: string): void {
  // 分组标题 / divider 的占位 key 不回传消费方
  if (key.startsWith('__')) {
    return
  }
  emit('select', key)
}
</script>

<template>
  <n-dropdown trigger="click" :options="options" @select="onSelect">
    <n-button size="small" :aria-label="ariaLabel">
      <template #icon>
        <n-icon :component="IconMore" />
      </template>
    </n-button>
  </n-dropdown>
</template>
