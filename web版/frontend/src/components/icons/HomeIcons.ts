/** 主页与恢复向导图标集：零依赖（20×20 线性图标，stroke=currentColor）。
 * 裁决依据：docs/UI全量改造方案.md §4.7 统一批次表 P4（L3 图标层）——
 * 「统一描边 1.5 / 尺寸 20，含主页与恢复向导所需全部图标；无图标库，全自绘；
 * 集中在单一文件，禁止散落」。
 *
 * 与既有两份图标文件的分工（本文件只新增，不改旧文件，旧消费方零影响）：
 * - `MenuIcons.ts`：导航 9 项 + 顶栏控件（24 网格，feather 线稿，P0–P4 视觉批次已落地）；
 * - `BackendIcons.ts`：仓库「目标位置」行卡 / 类型选择卡（24 网格）；
 * - 本文件：主页（P7）与恢复向导（P8–P11）新增表面，**设计网格 20×20**，
 *   描边 1.5 在 20 网格上的视觉重量与 24 网格渲染于 20px 时接近（1.5px vs 1.25px），
 *   同屏并用时以 NIcon 尺寸对齐即可，勿把两套网格的画法混进同一文件。
 *
 * 消费方式（与既有图标一致）：`<n-icon :size="20"><IconXxx /></n-icon>`，
 * 颜色随文字色继承（currentColor），亮/暗同源，零硬编码色值。
 */

import { h, type FunctionalComponent, type SVGAttributes } from 'vue'

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

/** 限速：仪表盘（全局状态条限速入口；后端 restic.py --limit-upload/download 已就绪）。 */
export const IconGauge: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('path', { d: 'M4.6 13.8a6.4 6.4 0 1 1 10.8 0' }),
    h('path', { d: 'M10 13.8l3.6-4.2' }),
    h('path', { d: 'M10 13.8h.01' }),
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

/** 离开：叉（向导外壳退出 / 对话框关闭；离开确认 R10）。 */
export const IconX: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [h('path', { d: 'M5.5 5.5l9 9M14.5 5.5l-9 9' })])

/** 加密：挂锁（encryption 步骤仓库密码输入；PasswordEntryModal 复用）。 */
export const IconLock: FunctionalComponent<SVGAttributes> = () =>
  h('svg', LINE_ICON_BASE, [
    h('rect', { x: 4.5, y: 9.2, width: 11, height: 7.8, rx: 2 }),
    h('path', { d: 'M7.6 9.2V6.6a2.4 2.4 0 0 1 4.8 0v2.6' }),
  ])

/** 覆盖：双文档（options 分区「已存在文件」overwrite 开关）。 */
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
