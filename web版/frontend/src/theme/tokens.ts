/** 设计令牌：全项目唯一色值 / 等宽字体常量来源。
 *
 * 基线：docs/UI界面设计方案.md v1.1（owner 2026-10-18 判定为 UI 唯一权威）；
 * 旧《UI全量改造方案》《UI视觉对齐Duplicati设计方案》已作废——靛蓝主色 #3D5BD9 取代 Duplicati 蓝。
 *
 * 纪律：
 * - 其他文件不得硬编码此处色值（单一事实来源）；
 * - 界面字体维持系统栈（App.vue #app 规则，不引入 webfont）；等宽场景一律引用 fontFamilyMono
 *   （启动时注入为全局 CSS 变量 --font-family-mono，portal 内容同样可达，见 main.ts）；
 * - 色值按 WCAG 2.1 AA 对比度实测选取（方案 §2.3 实测表）；
 * - success/warning/error 的 hover/pressed/suppl 与次级面 hover/pressed 为同色系邻近档派生值（方案只给定基准色 / 次级面基色 #F5F6FA）。
 */

import type { GlobalThemeOverrides } from 'naive-ui'

/** 亮色主题覆盖：UI界面设计方案 §2.1（AA 对比度实测值）。 */
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
    borderRadius: '8px',
    borderRadiusSmall: '6px',
  },
}

/** 暗色主题覆盖：UI界面设计方案 §2.2（独立调校，非反色）；
 * success/warning/error 三组色值方案未列改动，沿用既有暗色值。 */
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
    borderRadius: '8px',
    borderRadiusSmall: '6px',
  },
}

/** 数据 / 路径 / id / cron 等宽字体栈（UI界面设计方案 §3.1）。
 *
 * 原 4 处硬编码（ScheduleRetentionForm / audit / jobs / repo）统一收口到本常量；
 * 界面正文字体不设常量，维持 App.vue 的系统栈规则。
 */
export const fontFamilyMono = "Consolas, 'Cascadia Mono', monospace"
