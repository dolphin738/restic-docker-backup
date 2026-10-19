/** 存储后端图标集：零依赖（24×24 线性图标，stroke=currentColor）。
 * 裁决依据：docs/archive/UI视觉对齐Duplicati设计方案.md §4.2-2 —— Naive UI 无内置图标集，不新增 @vicons 依赖。
 * 用途：仓库列表「目标位置」行卡（对齐 Duplicati destination-list-item 的左侧类型图标）
 * 与新建仓库弹窗类型卡（对齐 f-05 存储类型选择器）。单色 currentColor 随文字色继承，亮/暗同源。
 */

import { h, type FunctionalComponent, type SVGAttributes } from 'vue'

/** 线性图标公共属性：24 视口 + currentColor 描边；尺寸由 NIcon 控制。 */
const LINE_ICON_BASE: SVGAttributes = {
  width: 24,
  height: 24,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': 1.5,
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round',
}

/** 本地路径：文件夹（feather folder 线稿）。 */
export const IconLocal: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', {
      d: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
    }),
  ])

/** S3：云（feather cloud 线稿）。 */
export const IconS3: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z' }),
  ])

/** Backblaze B2：存储箱（feather package 线稿，对象存储意象）。 */
export const IconB2: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', {
      d:
        'M16.5 9.4l-9-5.19M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z',
    }),
    h('path', { d: 'M3.27 6.96L12 12.01l8.73-5.05' }),
    h('path', { d: 'M12 22.08V12' }),
  ])

/** SFTP：终端（命令行 + 光标，免密 ssh 意象）。 */
export const IconSftp: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M4 17l6-6-6-6' }),
    h('path', { d: 'M12 19h8' }),
  ])

/** Azure Blob：数据库圆柱（存储容器意象）。 */
export const IconAzure: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('ellipse', { cx: 12, cy: 5.5, rx: 8, ry: 3 }),
    h('path', { d: 'M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13' }),
    h('path', { d: 'M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3' }),
  ])

/** Google Cloud Storage：地球（feather globe 线稿）。 */
export const IconGcs: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 12, cy: 12, r: 9 }),
    h('path', { d: 'M3 12h18' }),
    h('path', { d: 'M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18z' }),
  ])

/** REST 服务器：机柜（feather server 线稿）。 */
export const IconRest: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 3, y: 4, width: 18, height: 7, rx: 2 }),
    h('rect', { x: 3, y: 13, width: 18, height: 7, rx: 2 }),
    h('path', { d: 'M7 7.5h.01M7 16.5h.01' }),
  ])

/** rclone：双向同步（feather refresh-cw 线稿，万能远端意象）。 */
export const IconRclone: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M23 4v6h-6' }),
    h('path', { d: 'M1 20v-6h6' }),
    h('path', { d: 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10' }),
    h('path', { d: 'M1 14l4.64 4.36A9 9 0 0 0 20.49 15' }),
  ])

/** 新增目标位置：加号圆（Duplicati add-new 卡的 plus-circle 意象）。 */
export const IconPlusCircle: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 12, cy: 12, r: 9 }),
    h('path', { d: 'M12 8v8' }),
    h('path', { d: 'M8 12h8' }),
  ])

/** 后端类型键 → 图标组件（仓库行卡 / 类型选择卡共用；未知类型回落本地路径）。 */
import type { Component } from 'vue'

export const BACKEND_ICONS: Record<string, Component> = {
  local: IconLocal,
  s3: IconS3,
  b2: IconB2,
  sftp: IconSftp,
  azure: IconAzure,
  gcs: IconGcs,
  rest: IconRest,
  rclone: IconRclone,
}

export function backendIcon(type: string): Component {
  return BACKEND_ICONS[type] ?? IconLocal
}
