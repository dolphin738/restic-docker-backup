/** 菜单图标：零依赖内联 SVG（24×24 线性图标，stroke=currentColor）。
 * 裁决依据：docs/archive/UI视觉对齐Duplicati设计方案.md §4.2-2 —— Naive UI 无内置图标集，不新增 @vicons 依赖。
 * 前 5 个对应导航项（R-026/Q2 退役备份源/策略后 7→5），IconMenu（汉堡）供 <960px 顶栏菜单按钮使用。
 */

import { h, type FunctionalComponent, type SVGAttributes } from 'vue'

/** 线性图标公共属性：24 视口 + currentColor 描边；尺寸由 NIcon 控制，颜色随文字色继承。 */
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

/** 仪表盘：四宫格。 */
export const IconDashboard: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 3, y: 3, width: 7.5, height: 7.5, rx: 1.5 }),
    h('rect', { x: 13.5, y: 3, width: 7.5, height: 7.5, rx: 1.5 }),
    h('rect', { x: 3, y: 13.5, width: 7.5, height: 7.5, rx: 1.5 }),
    h('rect', { x: 13.5, y: 13.5, width: 7.5, height: 7.5, rx: 1.5 }),
  ])

/** 仓库配置：数据库圆柱。 */
export const IconRepo: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('ellipse', { cx: 12, cy: 5.5, rx: 8, ry: 3 }),
    h('path', { d: 'M4 5.5v13c0 1.66 3.58 3 8 3s8-1.34 8-3v-13' }),
    h('path', { d: 'M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3' }),
  ])

/** 任务历史：活动线（脉冲）。 */
export const IconJobs: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M3 12h4l2.5-7 4 14 2.5-7H21' })])

/** 快照浏览：层叠方块。 */
export const IconSnapshots: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M12 3l9 4.9-9 4.9-9-4.9z' }),
    h('path', { d: 'M3 12.6l9 4.9 9-4.9' }),
    h('path', { d: 'M3 17.4l9 4.9 9-4.9' }),
  ])

/** 恢复向导：回转箭头。 */
export const IconRestore: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8' }),
    h('path', { d: 'M3 3v5h5' }),
  ])

/** 通知设置：铃铛。 */
export const IconBell: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8' }),
    h('path', { d: 'M10.3 21a2 2 0 0 0 3.4 0' }),
  ])

/** 系统设置：齿轮（feather settings 线稿）。 */
export const IconSettings: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 12, cy: 12, r: 3 }),
    h('path', {
      d:
        'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06' +
        'a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09' +
        'a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83' +
        'l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09' +
        'a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83' +
        'l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09' +
        'a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83' +
        'l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09' +
        'a1.65 1.65 0 0 0-1.51 1z',
    }),
  ])

/** 审计日志：文档 + 行（append-only 台账意象）。 */
export const IconAudit: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
    h('path', { d: 'M14 2v6h6' }),
    h('path', { d: 'M16 13H8' }),
    h('path', { d: 'M16 17H8' }),
  ])

/** 菜单按钮：汉堡（<960px 顶栏抽屉入口）。 */
export const IconMenu: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M4 6.5h16M4 12h16M4 17.5h16' })])

/** 报表：柱状图（feather bar-chart 线稿；R-024 成功率报表入口）。 */
export const IconReports: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M6 20V10' }),
    h('path', { d: 'M12 20V4' }),
    h('path', { d: 'M18 20v-6' }),
  ])
