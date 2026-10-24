/** 设计令牌：全项目唯一色值 / 等宽字体 / 间距节奏 / 圆角与密度档位来源。
 *
 * 基线：视觉令牌唯一权威 = UI全量改造方案.md §6 视觉设计基线（原《UI界面设计方案》，2026-10-23 作废归档，内容并入 §6）；docs/UI全量改造方案.md v1.5 管第二轮功能面
 * （文档管理规范 v1.18：2026-10-23 合并后本文件为 UI 视觉与功能面唯一权威，原分工关系终止）。
 *
 * P5（§4.7 统一批次表，2026-10-22）起新增组件级覆盖：口径 = §4.6.4 彻底档唯一数值表，
 * 亮/暗两份同批同键（__tests__/tokens.spec.ts 护栏）；卡片三档阴影只落 theme/skin.css
 * （本文件不重复定义阴影值）。
 *
 * 纪律：
 * - 其他文件不得硬编码此处色值（单一事实来源）；
 * - 界面字体维持系统栈（App.vue #app 规则，不引入 webfont）；等宽场景一律引用 fontFamilyMono
 *   （启动时注入为全局 CSS 变量 --font-family-mono，portal 内容同样可达，见 main.ts）；
 * - 色值按 WCAG 2.1 AA 对比度实测选取（UI全量改造方案 §6.2 实测表）；
 * - success/warning/error 的 hover/pressed/suppl 与次级面 hover/pressed 为同色系邻近档派生值（UI全量改造方案只给定基准色 / 次级面基色 #F5F6FA）。
 */

import type { GlobalThemeOverrides } from 'naive-ui'

// ─── P5 彻底档（§4.6.4）：控件密度 ───────────────────────────────────────────

/** 按钮/输入四档行高：naive 默认 22/28/34/40 不在 4 的倍数节奏上，
 *  彻底档取 28/32/36/40（方案「行高 32/36/40」三个主档 + tiny 补齐 4px 阶梯）。 */
const CONTROL_HEIGHTS = {
  heightTiny: '28px',
  heightSmall: '32px',
  heightMedium: '36px',
  heightLarge: '40px',
} as const

/** 控件圆角（按钮/输入）：§4.6.4 彻底档 10px。
 *
 *  Select 族对齐（owner 2026-10-22 裁决，原「待仲裁」偏差已修复）：naive 2.43 的
 *  Select / Cascader / TreeSelect 的 trigger 是 InternalSelection（peer 主题），
 *  self(vars) 从 common 透传 borderRadius，公开覆盖键 Select: { borderRadius } 到不了
 *  peer（实测无效）→ 经 skin.css 的 --rw-radius-control 单类规则对齐（.n-base-selection），
 *  spec 护栏校验跨文件同源。AutoComplete 不受影响：其 trigger 直接复用 Input 组件
 *  （auto-complete/src/AutoComplete.mjs import input/src/Input.mjs），本就随 Input=10。 */
const CONTROL_RADIUS = '10px'

/** 卡片 / 弹层圆角（Card / Dialog；n-modal preset=card 的内层卡片随 Card）。 */
const CARD_RADIUS = '16px'

/** 标签药丸圆角：§4.6.4 彻底档 999px（round prop 的 calc(height/2) 修饰仍优先）。 */
const TAG_RADIUS = '999px'

/** 表格行高 40 的推导值：行高 = 9（上 padding）+ 21（lineHeight 1.5 × 14px）+ 9（下 padding）+ 1（分隔线）。
 *
 *  thPadding 必须保持**单值**：naive 在 expand 列用了 `margin: calc(var(--n-th-padding) * -1)`、
 *  在 sortable+filterable 表头用了 `padding-right: calc(var(--n-th-padding) + 36px)`，
 *  两值（如 '9px 8px'）会令 calc 失效、静默丢声明。横向 padding 因此在单值约束下随纵向
 *  定为 9px（small 8→9、medium/large 12→9）；th/td 同值保持列对齐。 */
const TABLE_CELL_PADDING = '9px'

/** 表格正文行高 1.6→1.5：为「行高 40」让出 1.4px（22.4→21），配合 9px 上下 padding 精确命中 40。
 *  line-height 挂在 .n-data-table-wrapper 上，th/td 继承。 */
const TABLE_LINE_HEIGHT = '1.5'

/** 标签「无边框实心五态」：默认态 + 五语义态边框全部置无。
 *
 *  n-tag 的边框渲染在绝对定位的 __border 子元素上（border: var(--n-border)），
 *  置 'none' 即消除视觉边框而保留元素；底色仍取 colorBordered{Type}（hue@.1 浅底），
 *  「实心浅底 + 彩色文字 + 药丸」形态不变。 */
const NO_TAG_BORDER = {
  border: 'none',
  borderPrimary: 'none',
  borderInfo: 'none',
  borderSuccess: 'none',
  borderWarning: 'none',
  borderError: 'none',
} as const

/** 组件级形态/密度覆盖：与主题色无关，亮/暗两份同批同值（spec 校验两份键集一致）。
 *  Card 不在此列——其圆角随两份主题各自给出（阴影三档统一只落 skin.css）。 */
const controlShapeOverrides: GlobalThemeOverrides = {
  Button: {
    ...CONTROL_HEIGHTS,
    borderRadiusTiny: CONTROL_RADIUS,
    borderRadiusSmall: CONTROL_RADIUS,
    borderRadiusMedium: CONTROL_RADIUS,
    borderRadiusLarge: CONTROL_RADIUS,
  },
  Input: { ...CONTROL_HEIGHTS, borderRadius: CONTROL_RADIUS },
  Tag: { borderRadius: TAG_RADIUS, ...NO_TAG_BORDER },
  Dialog: { borderRadius: CARD_RADIUS },
  DataTable: {
    lineHeight: TABLE_LINE_HEIGHT,
    thPaddingSmall: TABLE_CELL_PADDING,
    thPaddingMedium: TABLE_CELL_PADDING,
    thPaddingLarge: TABLE_CELL_PADDING,
    tdPaddingSmall: TABLE_CELL_PADDING,
    tdPaddingMedium: TABLE_CELL_PADDING,
    tdPaddingLarge: TABLE_CELL_PADDING,
  },
}

// ─── P5 彻底档（§4.6.4）：间距节奏 ──────────────────────────────────────────

/** 间距节奏六档（对应模版 p2r() 节奏）：页面/共用组件一律取此档，禁止各写各的。
 *  skin.css 的 `--rw-space-1..6` 变量与 `.rw-*` 工具类与此常量同源
 *  （__tests__/tokens.spec.ts 跨文件同步护栏，防两处漂移）。 */
export const spacing = {
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 24,
  6: 32,
} as const

// ─── 源数据目录树视口高度（px） ────────────────────────────────────────────

/** 源树视口 = **内容驱动**（行数 × 行高 36），用本组常量夹住上下限（owner 2026-10-24）。
 *  取 spacing[6]（32px）的倍数：×11 ≈ 10 行保底（空目录也有树盒）、×24 ≈ 21 行封顶
 *  （超出内部滚动，树盒永不出现死空白，也不追右栏高度——Duplicati 左树同形态）。
 *  曾用 CSS height:100% + ResizeObserver  stretch 方案，百分比链在 flex 自动高度
 *  容器里不可靠（盒子被撑高），已废；高度改由 JS 算好后以内联样式下发。 */
export const sourceTreeViewMinHeight = spacing[6] * 11 // 352px
export const sourceTreeViewMaxHeight = spacing[6] * 24 // 768px

/** 亮色主题覆盖：UI全量改造方案 §6（原《UI界面设计方案》） §2.1（AA 对比度实测值）+ §4.6.4 彻底档组件级形态。 */
export const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#3D5BD9',
    primaryColorHover: '#5B7CFA',
    primaryColorPressed: '#2F49B3',
    primaryColorSuppl: '#5B7CFA',
    infoColor: '#3D5BD9',
    infoColorHover: '#5B7CFA',
    infoColorPressed: '#2F49B3',
    infoColorSuppl: '#5B7CFA',
    successColor: '#15803D',
    successColorHover: '#16A34A',
    successColorPressed: '#166534',
    successColorSuppl: '#16A34A',
    warningColor: '#B45309',
    warningColorHover: '#D97706',
    warningColorPressed: '#92400E',
    warningColorSuppl: '#D97706',
    errorColor: '#B91C1C',
    errorColorHover: '#DC2626',
    errorColorPressed: '#991B1B',
    errorColorSuppl: '#DC2626',
    textColorBase: '#475569',
    textColor1: '#0F172A',
    textColor2: '#475569',
    textColor3: '#64748B',
    bodyColor: '#ffffff',
    cardColor: '#ffffff',
    modalColor: '#ffffff',
    popoverColor: '#ffffff',
    tableColor: '#ffffff',
    borderColor: '#E2E5EE',
    actionColor: '#F5F6FA',
    hoverColor: '#F5F6FA',
    pressedColor: '#E9ECF6',
    borderRadius: '12px',
    borderRadiusSmall: '8px',
  },
  Card: { borderRadius: CARD_RADIUS },
  ...controlShapeOverrides,
}

/** 暗色主题覆盖：UI全量改造方案 §6（原《UI界面设计方案》） §2.2（独立调校，非反色）；
 * success/warning/error 三组色值方案未列改动，沿用既有暗色值；
 * 组件级形态与亮色同批同值（§4.6.4「亮/暗同批改」）。 */
export const darkThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#7C93F5',
    primaryColorHover: '#96A8F8',
    primaryColorPressed: '#5F79E8',
    primaryColorSuppl: '#96A8F8',
    infoColor: '#7C93F5',
    infoColorHover: '#96A8F8',
    infoColorPressed: '#5F79E8',
    infoColorSuppl: '#96A8F8',
    successColor: '#81c601',
    successColorHover: '#9bd221',
    successColorPressed: '#5e8c01',
    successColorSuppl: '#9bd221',
    warningColor: '#e8a33d',
    warningColorHover: '#f0b45a',
    warningColorPressed: '#c08630',
    warningColorSuppl: '#f0b45a',
    errorColor: '#e06c6c',
    errorColorHover: '#ec8080',
    errorColorPressed: '#c25555',
    errorColorSuppl: '#ec8080',
    textColorBase: '#C3C9D9',
    textColor1: '#EEF1F8',
    textColor2: '#C3C9D9',
    textColor3: '#7D8598',
    bodyColor: '#14161C',
    cardColor: '#1C1F27',
    modalColor: '#1C1F27',
    popoverColor: '#232733',
    tableColor: '#191C24',
    borderColor: '#31364A',
    borderRadius: '12px',
    borderRadiusSmall: '8px',
  },
  Card: { borderRadius: CARD_RADIUS },
  ...controlShapeOverrides,
}

/** 数据 / 路径 / id / cron 等宽字体栈（UI全量改造方案 §6（原《UI界面设计方案》） §3.1）。
 *
 * 原 4 处硬编码（ScheduleRetentionForm / audit / jobs / repo）统一收口到本常量；
 * 界面正文字体不设常量，维持 App.vue 的系统栈规则。
 */
export const fontFamilyMono = "Consolas, 'Cascadia Mono', monospace"
