/** 全站图标库（单一文件，P15 由 HomeIcons / MenuIcons / BackendIcons 三文件合并）。
 * 零依赖自绘线性图标（无图标库，禁止散落——新增图标只进本文件）。
 *
 * 裁决依据：docs/UI全量改造方案.md §4.7 批次表
 *   - P4（L3 图标层）：统一描边 1.5 / 尺寸 20；
 *   - P15（owner 2026-10-23 裁决 ①重绘 ②合并 ③全上 ⑤留库）：
 *     MenuIcons 16 个与 BackendIcons 8 个由 24 网格旧画法**重绘**为 20 网格标准
 *     （旧画法就此淘汰；stroke 在 20 网格下渲染 1.5px，旧 24 网格仅 1.25px）；
 *     三文件合并为本文件，BACKEND_ICONS 映射与 backendIcon 随迁；
 *     **全量导出保留当图标库**——当前未被页面引用的图标（如 IconPlay /
 *     IconSearch / IconFilter…）是留库资产，不因「暂无消费方」删除，
 *     后续页面图标化（P15-c）与迭代直接取用。
 *
 * 画法标准（icons.spec.ts 机械护栏）：
 *   - viewBox '0 0 20 20' / fill none / stroke currentColor / stroke-width 1.5；
 *   - 颜色一律继承文字色（亮/暗同源，零硬编码色值）；尺寸由 <n-icon :size> 控制。
 * 消费方式：`<n-icon :size="20"><IconXxx /></n-icon>`。
 */

import { h, type FunctionalComponent, type SVGAttributes } from 'vue'
import type { Component } from 'vue'

/** 线性图标公共属性：20 视口 + currentColor 描边 1.5；尺寸由 NIcon 控制。 */
const LINE_ICON_BASE: SVGAttributes = {
  width: 20,
  height: 20,
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  'stroke-width': 1.5,
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round',
}

// ---------------------------------------------------------------- 主页：行动卡与状态条（P7）

/** 新建备份：加号圆（主页双行动卡 Backups→/add-backup；任务页新增按钮复用）。 */
export const IconPlusCircle: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 10, cy: 10, r: 8 }),
    h('path', { d: 'M10 6.8v6.4M6.8 10h6.4' }),
  ])

/** 开始运行：三角（任务操作区 Start；running 态由 Stop 接管）。 */
export const IconPlay: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M7.6 4.9v10.2l8-5.1z' })])

/** 停止：圆角方块（Stop→Abort 二段的第一段，等价既有 cancelJob）。 */
export const IconStop: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('rect', { x: 6.3, y: 6.3, width: 7.4, height: 7.4, rx: 1.4 })])

/** 中止：八边形 + 叉（Stop 点击后的第二段直杀，grace=0；restic.py cancel 已有）。 */
export const IconAbort: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M7.3 3.2h5.4L16.8 7.3v5.4L12.7 16.8H7.3L3.2 12.7V7.3z' }),
    h('path', { d: 'M8.3 8.3l3.7 3.7M12 8.3l-3.7 3.7' }),
  ])

/** 活动：脉冲线（全局进度条 / KPI 带「进行中」格）。 */
export const IconActivity: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M2 10.5h3.2l2-5.2 3.6 10.4 2-5.2H18' })])

// ---------------------------------------------------------------- 主页：工具栏（P7）

/** 列表视图：三行 + 圆点（视图切换按钮组，localStorage 持久化）。 */
export const IconList: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M7.5 6H16.5M7.5 10H16.5M7.5 14H16.5' }),
    h('circle', { cx: 4.5, cy: 6, r: 0.9 }),
    h('circle', { cx: 4.5, cy: 10, r: 0.9 }),
    h('circle', { cx: 4.5, cy: 14, r: 0.9 }),
  ])

/** 详情视图：四宫格（视图切换按钮组，sh-table 9 列）。 */
export const IconGrid: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 2.5, y: 2.5, width: 6.5, height: 6.5, rx: 1.4 }),
    h('rect', { x: 11, y: 2.5, width: 6.5, height: 6.5, rx: 1.4 }),
    h('rect', { x: 2.5, y: 11, width: 6.5, height: 6.5, rx: 1.4 }),
    h('rect', { x: 11, y: 11, width: 6.5, height: 6.5, rx: 1.4 }),
  ])

/** 时钟：钟面 + 指针（时间口径 Relative/Actual 切换；任务卡耗时 / 上次运行复用）。 */
export const IconClock: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 10, cy: 10, r: 8 }),
    h('path', { d: 'M10 5.8V10l3 1.9' }),
  ])

/** 筛选：漏斗（排序下拉触发图标，#sortIcons 三态之 funnel）。 */
export const IconFilter: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M3.2 4.5h13.6L11.4 10.6v5.3l-2.8 1.4v-6.7z' })])

/** 升序：上箭头（#sortIcons 三态之 asc）。 */
export const IconSortAsc: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M10 16.5V4M6.2 7.8L10 4l3.8 3.8' })])

/** 降序：下箭头（#sortIcons 三态之 desc）。 */
export const IconSortDesc: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M10 3.5V16M6.2 12.2L10 16l3.8-3.8' })])

// ---------------------------------------------------------------- 主页：任务卡与三点菜单（P7）

/** 版本数：层叠（任务卡版本数 chip = 该任务快照数，P1 snapshot_count）。 */
export const IconLayers: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M10 2.8l7.5 4.2L10 11.2 2.5 7z' }),
    h('path', { d: 'M2.5 11.7L10 15.9l7.5-4.2' }),
    h('path', { d: 'M2.5 15.3L10 19.5l7.5-4.2' }),
  ])

/** 下次运行：日历（任务卡 next_run_at，cron 推导值）。 */
export const IconCalendar: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 3, y: 4.5, width: 14, height: 13, rx: 2 }),
    h('path', { d: 'M3 8.8h14' }),
    h('path', { d: 'M7 2.5v4M13 2.5v4' }),
  ])

/** 成功：对勾圆（任务成功态 / Verify 成功 2s 图标态 / 恢复进度页完成态圆环 R9）。 */
export const IconCheckCircle: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 10, cy: 10, r: 8 }),
    h('path', { d: 'M6.8 10.3l2.2 2.2 4.2-4.7' }),
  ])

/** 编辑：铅笔（三点菜单 Operations 编辑项）。 */
export const IconEdit: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M16.5 3.5a2.4 2.4 0 1 1 3.4 3.4L7.9 18.9l-5 1.1 1.1-5z' }),
  ])

/** 删除：垃圾桶（三点菜单 Advanced 删除项，ConfirmGate 前置）。 */
export const IconTrash: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M3.2 6h13.6' }),
    h('path', { d: 'M7.6 6V4.3A1.8 1.8 0 0 1 9.4 2.5h1.2a1.8 1.8 0 0 1 1.8 1.8V6' }),
    h('path', { d: 'M15.4 6l-.9 11.4a1.8 1.8 0 0 1-1.8 1.6H7.3a1.8 1.8 0 0 1-1.8-1.6L4.6 6' }),
    h('path', { d: 'M8.6 9.6v6M11.4 9.6v6' }),
  ])

/** 导出：下箭头 + 托架（单任务配置导出，P2 端点；全局导出在设置页复用）。 */
export const IconDownload: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M10 3v9.5' }),
    h('path', { d: 'M6.5 9L10 12.5 13.5 9' }),
    h('path', { d: 'M4 14.5v2a1.5 1.5 0 0 0 1.5 1.5h9a1.5 1.5 0 0 0 1.5-1.5v-2' }),
  ])

/** 校验：盾牌对勾（三点菜单 Reporting Verify files → createJob('check')）。 */
export const IconShieldCheck: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M10 2.8l6.2 2.3v5.1c0 4.2-2.7 6.7-6.2 7.8-3.5-1.1-6.2-3.6-6.2-7.8V5.1z' }),
    h('path', { d: 'M7.2 9.9l2 2 3.6-4' }),
  ])

/** 警告：三角 + 感叹（failed / interrupted 态的菜单与列表提示；StatusTag 圆点之外的图形态）。 */
export const IconAlertTriangle: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M10 3.4L2.9 16.6h14.2z' }),
    h('path', { d: 'M10 7.6v4.2' }),
    h('path', { d: 'M10 13.9h.01' }),
  ])

// ---------------------------------------------------------------- 恢复入口页与向导（P8–P11）

/** 备份列表：双机柜（恢复入口页「从已有备份恢复」行动卡；仓库类型 chip 复用）。 */
export const IconServer: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 3, y: 3, width: 14, height: 6, rx: 1.6 }),
    h('rect', { x: 3, y: 11, width: 14, height: 6, rx: 1.6 }),
    h('path', { d: 'M6.2 6h.01M6.2 14h.01' }),
  ])

/** 目标位置：文件夹（options 分区「恢复到哪」；恢复目标 = 选已有仓库 / 原目录）。 */
export const IconFolder: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', {
      d: 'M3 6.6a1.8 1.8 0 0 1 1.8-1.8h3l1.8 2h5.6a1.8 1.8 0 0 1 1.8 1.8v6.8a1.8 1.8 0 0 1-1.8 1.8H4.8A1.8 1.8 0 0 1 3 15.4z',
    }),
  ])

/** 展开目录：开口文件夹（文件树 expanded 态；VirtualTree 节点图标）。 */
export const IconFolderOpen: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M3 7a1.8 1.8 0 0 1 1.8-1.8h3l1.8 2h5.4A1.8 1.8 0 0 1 17 9v1.4H3z' }),
    h('path', { d: 'M3 10.4h14l-1.5 6a1.8 1.8 0 0 1-1.8 1.4H5.3a1.8 1.8 0 0 1-1.8-1.4z' }),
  ])

/** 文件：文档 + 行（文件树叶子节点；进度页日志卡 / options 的 Metadata 分区复用）。 */
export const IconFile: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M11.2 2.5H6.2A1.7 1.7 0 0 0 4.5 4.2v11.6a1.7 1.7 0 0 0 1.7 1.7h7.6a1.7 1.7 0 0 0 1.7-1.7V6.8z' }),
    h('path', { d: 'M11.2 2.5v4.3h4.3' }),
    h('path', { d: 'M8 11.5h4M8 14.5h4' }),
  ])

/** 导入：上箭头 + 托架（恢复入口页「从配置文件恢复」行动卡 → importConfig）。 */
export const IconUpload: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M10 12.5V3.5' }),
    h('path', { d: 'M6.5 7L10 3.5 13.5 7' }),
    h('path', { d: 'M4 14v2.3A1.7 1.7 0 0 0 5.7 18h8.6a1.7 1.7 0 0 0 1.7-1.7V14' }),
  ])

/** 搜索：放大镜（select-files 搜索框；后端 P3 端点已过滤，前端不再全量拉取）。 */
export const IconSearch: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 9, cy: 9, r: 5.6 }),
    h('path', { d: 'M12.8 12.8L16.5 16.5' }),
  ])

/** 树展开：右尖括号（VirtualTree collapsed 节点；手势分工：符号只展开收起）。 */
export const IconChevronRight: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M8.5 5.5l4.5 4.5-4.5 4.5' })])

/** 树收起：下尖括号（VirtualTree expanded 节点）。 */
export const IconChevronDown: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M5.5 8l4.5 4.5L14.5 8' })])

/** 上一步：左箭头（向导步骤条返回 / flow 外壳离开确认前的回退）。 */
export const IconArrowLeft: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M16.5 10H3.5' }),
    h('path', { d: 'M7.5 5.5L3.5 10l4 4.5' }),
  ])

/** 下一步：右箭头（P16 恢复向导第二步页脚「提交」，Duplicati Submit 的 arrow-right 镜像）。 */
export const IconArrowRight: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M3.5 10H16.5' }),
    h('path', { d: 'M12.5 5.5L16.5 10l-4 4.5' }),
  ])

/** 离开：叉（向导外壳退出 / 对话框关闭；离开确认 R10）。 */
export const IconX: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M5.5 5.5l9 9M14.5 5.5l-9 9' })])

/** 加密：挂锁（encryption 步骤仓库密码输入；PasswordEntryModal 复用）。 */
export const IconLock: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 4.5, y: 9.2, width: 11, height: 7.8, rx: 2 }),
    h('path', { d: 'M7.6 9.2V6.6a2.4 2.4 0 0 1 4.8 0v2.6' }),
  ])

/** 覆盖：双文档（options 分区「已存在文件」开关）。 */
export const IconCopy: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 8.2, y: 8.2, width: 9.8, height: 9.8, rx: 1.6 }),
    h('path', {
      d: 'M5.5 14.7H4.2a1.5 1.5 0 0 1-1.5-1.5V4.8a1.5 1.5 0 0 1 1.5-1.5h8.6a1.5 1.5 0 0 1 1.5 1.5v.5',
    }),
  ])

/** 高级：滑杆（options 分区「高级」；restic 附加参数收口于白名单）。 */
export const IconSliders: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M4 6.5h2.4M9.6 6.5H16' }),
    h('circle', { cx: 8, cy: 6.5, r: 1.8 }),
    h('path', { d: 'M4 13.5h6.4M13.6 13.5H16' }),
    h('circle', { cx: 12, cy: 13.5, r: 1.8 }),
  ])

/** 说明：信息圆（options 的 Metadata 分区 / 各步帮助文案旁）。 */
export const IconInfo: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 10, cy: 10, r: 8 }),
    h('path', { d: 'M10 9.2h.01' }),
    h('path', { d: 'M10 11.8v3.4' }),
  ])

/* ================================================================
   导航 / 顶栏图标（原 MenuIcons.ts，P15 由 24 网格重绘为 20 网格标准）
   ================================================================ */

/** 仪表盘：四宫格。 */
export const IconDashboard: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 2.5, y: 2.5, width: 6.25, height: 6.25, rx: 1.25 }),
    h('rect', { x: 11.25, y: 2.5, width: 6.25, height: 6.25, rx: 1.25 }),
    h('rect', { x: 2.5, y: 11.25, width: 6.25, height: 6.25, rx: 1.25 }),
    h('rect', { x: 11.25, y: 11.25, width: 6.25, height: 6.25, rx: 1.25 }),
  ])

/** 仓库配置：数据库圆柱。 */
export const IconRepo: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('ellipse', { cx: 10, cy: 4.58, rx: 6.67, ry: 2.5 }),
    h('path', { d: 'M 3.33 4.58 v 10.83 c 0 1.38 2.98 2.5 6.67 2.5 s 6.67 -1.12 6.67 -2.5 v -10.83'}),
    h('path', { d: 'M 3.33 10 c 0 1.38 2.98 2.5 6.67 2.5 s 6.67 -1.12 6.67 -2.5'}),
  ])

/** 任务历史：活动线（脉冲）。 */
export const IconJobs: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 2.5 10 h 3.33 l 2.08 -5.83 3.33 11.67 2.08 -5.83 H 17.5'})
  ])

/** 快照浏览：层叠方块。 */
export const IconSnapshots: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 10 2.5 l 7.5 4.08 -7.5 4.08 -7.5 -4.08 z'}),
    h('path', { d: 'M 2.5 10.5 l 7.5 4.08 7.5 -4.08'}),
    h('path', { d: 'M 2.5 14.5 l 7.5 4.08 7.5 -4.08'}),
  ])

/** 恢复向导：回转箭头。 */
export const IconRestore: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 2.5 10 a 7.5 7.5 0 1 0 7.5 -7.5 8.12 8.12 0 0 0 -5.62 2.28 L 2.5 6.67'}),
    h('path', { d: 'M 2.5 2.5 v 4.17 h 4.17'}),
  ])

/** 通知设置：铃铛。 */
export const IconBell: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 15 6.67 a 5 5 0 1 0 -10 0 c 0 5.83 -2.5 6.67 -2.5 6.67 h 15 s -2.5 -0.83 -2.5 -6.67'}),
    h('path', { d: 'M 8.58 17.5 a 1.67 1.67 0 0 0 2.83 0'}),
  ])

/** 系统设置：齿轮（feather settings 线稿）。 */
export const IconSettings: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 10, cy: 10, r: 2.5 }),
    h('path', {
    d: 'M 16.17 12.5 a 1.38 1.38 0 0 0 0.28 1.52 l 0.05 0.05 a 1.67 1.67 0 1 1 -2.36 2.36 l -0.05 -0.05 a 1.38 1.38 0 0 0 -1.52 -0.28 1.38 1.38 0 0 0 -0.83 1.26 V 17.5 a 1.67 1.67 0 1 1 -3.33 0 v -0.07 a 1.38 1.38 0 0 0 -0.83 -1.26 1.38 1.38 0 0 0 -1.52 0.28 l -0.05 0.05 a 1.67 1.67 0 1 1 -2.36 -2.36 l 0.05 -0.05 a 1.38 1.38 0 0 0 0.28 -1.52 1.38 1.38 0 0 0 -1.26 -0.83 H 2.5 a 1.67 1.67 0 1 1 0 -3.33 h 0.07 a 1.38 1.38 0 0 0 1.26 -0.83 1.38 1.38 0 0 0 -0.28 -1.52 l -0.05 -0.05 a 1.67 1.67 0 1 1 2.36 -2.36 l 0.05 0.05 a 1.38 1.38 0 0 0 1.52 0.28 h 0 a 1.38 1.38 0 0 0 0.83 -1.26 V 2.5 a 1.67 1.67 0 1 1 3.33 0 v 0.07 a 1.38 1.38 0 0 0 0.83 1.26 h 0 a 1.38 1.38 0 0 0 1.52 -0.28 l 0.05 -0.05 a 1.67 1.67 0 1 1 2.36 2.36 l -0.05 0.05 a 1.38 1.38 0 0 0 -0.28 1.52 v 0 a 1.38 1.38 0 0 0 1.26 0.83 H 17.5 a 1.67 1.67 0 1 1 0 3.33 h -0.07 a 1.38 1.38 0 0 0 -1.26 0.83 z',
    }),
  ])

/** 审计日志：文档 + 行（append-only 台账意象）。 */
export const IconAudit: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 11.67 1.67 H 5 a 1.67 1.67 0 0 0 -1.67 1.67 v 13.33 a 1.67 1.67 0 0 0 1.67 1.67 h 10 a 1.67 1.67 0 0 0 1.67 -1.67 V 6.67 z'}),
    h('path', { d: 'M 11.67 1.67 v 5 h 5'}),
    h('path', { d: 'M 13.33 10.83 H 6.67'}),
    h('path', { d: 'M 13.33 14.17 H 6.67'}),
  ])

/** 菜单按钮：汉堡（<960px 顶栏抽屉入口）。 */
export const IconMenu: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 3.33 5.42 h 13.33 M 3.33 10 h 13.33 M 3.33 14.58 h 13.33'})
  ])

/** 更多操作：横排三点（任务行三点菜单触发器；实心圆点故子图元单独 fill，描边规范仍在 svg 根。 */
export const IconMore: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 4.17, cy: 10, r: 1.25, fill: 'currentColor', stroke: 'none' }),
    h('circle', { cx: 10, cy: 10, r: 1.25, fill: 'currentColor', stroke: 'none' }),
    h('circle', { cx: 15.83, cy: 10, r: 1.25, fill: 'currentColor', stroke: 'none' }),
  ])

/** 报表：柱状图（feather bar-chart 线稿；R-024 成功率报表入口）。 */
export const IconReports: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 5 16.67 V 8.33'}),
    h('path', { d: 'M 10 16.67 V 3.33'}),
    h('path', { d: 'M 15 16.67 v -5'}),
  ])

/** 浅色主题：太阳（feather sun 线稿；顶栏主题下拉）。 */
export const IconSun: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 10, cy: 10, r: 3.33 }),
    h('path', { d: 'M 10 1.67 v 1.67'}),
    h('path', { d: 'M 10 16.67 v 1.67'}),
    h('path', { d: 'M 4.11 4.11 l 1.18 1.18'}),
    h('path', { d: 'M 14.72 14.72 l 1.18 1.18'}),
    h('path', { d: 'M 1.67 10 h 1.67'}),
    h('path', { d: 'M 16.67 10 h 1.67'}),
    h('path', { d: 'M 5.28 14.72 l -1.18 1.18'}),
    h('path', { d: 'M 15.89 4.11 l -1.18 1.18'}),
  ])

/** 深色主题：月亮（feather moon 线稿；顶栏主题下拉）。 */
export const IconMoon: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 17.5 10.66 A 7.5 7.5 0 1 1 9.34 2.5 5.83 5.83 0 0 0 17.5 10.66 z'})
  ])

/** 跟随系统主题：显示器（feather monitor 线稿；顶栏主题下拉）。 */
export const IconMonitor: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 1.67, y: 2.5, width: 16.67, height: 11.67, rx: 1.67 }),
    h('path', { d: 'M 6.67 17.5 h 6.67'}),
    h('path', { d: 'M 10 14.17 v 3.33'}),
  ])

/** 当前用户：头像 + 肩（feather user 线稿；顶栏用户菜单触发按钮）。 */
export const IconUser: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 16.67 17.5 v -1.67 a 3.33 3.33 0 0 0 -3.33 -3.33 H 6.67 a 3.33 3.33 0 0 0 -3.33 3.33 v 1.67'}),
    h('circle', { cx: 10, cy: 5.83, r: 3.33 }),
  ])

/** 勾选：对勾（feather check 线稿；主题下拉当前值标记）。 */
export const IconCheck: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 16.67 5 7.5 14.17 l -4.17 -4.17'})
  ])

/** 登出：出门箭头（feather log-out 线稿；用户菜单登出项）。 */
export const IconLogout: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 7.5 17.5 H 4.17 a 1.67 1.67 0 0 1 -1.67 -1.67 V 4.17 a 1.67 1.67 0 0 1 1.67 -1.67 h 3.33'}),
    h('path', { d: 'M 13.33 14.17 l 4.17 -4.17 -4.17 -4.17'}),
    h('path', { d: 'M 17.5 10 H 7.5'}),
  ])


/* ================================================================
   存储后端类型图标（原 BackendIcons.ts，P15 由 24 网格重绘为 20 网格标准）
   ================================================================ */

/** 本地路径：文件夹（feather folder 线稿）。 */
export const IconLocal: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', {
    d: 'M 2.5 5.83 a 1.67 1.67 0 0 1 1.67 -1.67 h 3.33 l 1.67 1.67 h 6.67 a 1.67 1.67 0 0 1 1.67 1.67 v 7.5 a 1.67 1.67 0 0 1 -1.67 1.67 H 4.17 a 1.67 1.67 0 0 1 -1.67 -1.67 z',
    }),
  ])

/** S3：云（feather cloud 线稿）。 */
export const IconS3: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 15 8.33 h -1.05 A 6.67 6.67 0 1 0 7.5 16.67 h 7.5 a 4.17 4.17 0 0 0 0 -8.33 z'}),
  ])

/** Backblaze B2：存储箱（feather package 线稿，对象存储意象）。 */
export const IconB2: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', {
    d: 'M 13.75 7.83 l -7.5 -4.33 M 17.5 13.33 V 6.67 a 1.67 1.67 0 0 0 -0.83 -1.44 l -5.83 -3.33 a 1.67 1.67 0 0 0 -1.67 0 l -5.83 3.33 A 1.67 1.67 0 0 0 2.5 6.67 v 6.67 a 1.67 1.67 0 0 0 0.83 1.44 l 5.83 3.33 a 1.67 1.67 0 0 0 1.67 0 l 5.83 -3.33 A 1.67 1.67 0 0 0 17.5 13.33 z',
    }),
    h('path', { d: 'M 2.73 5.8 L 10 10.01 l 7.28 -4.21'}),
    h('path', { d: 'M 10 18.4 V 10'}),
  ])

/** SFTP：终端（命令行 + 光标，免密 ssh 意象）。 */
export const IconSftp: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 3.33 14.17 l 5 -5 -5 -5'}),
    h('path', { d: 'M 10 15.83 h 6.67'}),
  ])

/** Azure Blob：数据库圆柱（存储容器意象）。 */
export const IconAzure: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('ellipse', { cx: 10, cy: 4.58, rx: 6.67, ry: 2.5 }),
    h('path', { d: 'M 3.33 4.58 v 10.83 c 0 1.38 2.98 2.5 6.67 2.5 s 6.67 -1.12 6.67 -2.5 v -10.83'}),
    h('path', { d: 'M 3.33 10 c 0 1.38 2.98 2.5 6.67 2.5 s 6.67 -1.12 6.67 -2.5'}),
  ])

/** Google Cloud Storage：地球（feather globe 线稿）。 */
export const IconGcs: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('circle', { cx: 10, cy: 10, r: 7.5 }),
    h('path', { d: 'M 2.5 10 h 15'}),
    h('path', { d: 'M 10 2.5 a 12.5 12.5 0 0 1 0 15 a 12.5 12.5 0 0 1 0 -15 z'}),
  ])

/** REST 服务器：机柜（feather server 线稿）。 */
export const IconRest: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 2.5, y: 3.33, width: 15, height: 5.83, rx: 1.67 }),
    h('rect', { x: 2.5, y: 10.83, width: 15, height: 5.83, rx: 1.67 }),
    h('path', { d: 'M 5.83 6.25 h 0.01 M 5.83 13.75 h 0.01'}),
  ])

/** rclone：双向同步（feather refresh-cw 线稿，万能远端意象）。 */
export const IconRclone: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M 19.17 3.33 v 5 h -5'}),
    h('path', { d: 'M 0.83 16.67 v -5 h 5'}),
    h('path', { d: 'M 2.92 7.5 a 7.5 7.5 0 0 1 12.38 -2.8 L 19.17 8.33'}),
    h('path', { d: 'M 0.83 11.67 l 3.87 3.63 A 7.5 7.5 0 0 0 17.07 12.5'}),
  ])


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
