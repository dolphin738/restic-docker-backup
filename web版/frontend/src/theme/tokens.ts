/** 设计令牌：全项目唯一色值来源（对齐 Duplicati 视觉，规范原文已归档 docs/archive/UI视觉对齐Duplicati设计方案.md §4.1）。
 *
 * 纪律：
 * - 其他文件不得硬编码此处色值（单一事实来源）；
 * - 字体裁决为系统栈先行（§4.1.1 方案 b），故不设 fontFamily；
 * - warning/error 为 2026-10-13 裁决值（AA 对比度），非 Duplicati 原值。
 */

import type { GlobalThemeOverrides } from 'naive-ui'

/** 亮色主题覆盖：Duplicati default.css 提取值 + AA 对比度裁决值。 */
export const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#277db0',
    primaryColorHover: '#4ca4d7',
    primaryColorPressed: '#1f6590',
    primaryColorSuppl: '#4ca4d7',
    infoColor: '#277db0',
    infoColorHover: '#4ca4d7',
    infoColorPressed: '#1f6590',
    infoColorSuppl: '#4ca4d7',
    successColor: '#568301',
    successColorHover: '#6da301',
    successColorPressed: '#3f5d01',
    warningColor: '#b87a00',
    warningColorHover: '#d18f00',
    warningColorPressed: '#966500',
    errorColor: '#c03636',
    errorColorHover: '#d04545',
    errorColorPressed: '#a02a2a',
    textColorBase: '#505050',
    textColor1: '#000000',
    textColor2: '#505050',
    textColor3: '#838383',
    bodyColor: '#ffffff',
    cardColor: '#ffffff',
    modalColor: '#ffffff',
    popoverColor: '#ffffff',
    tableColor: '#ffffff',
    borderColor: '#d8d8d8',
    borderRadius: '5px',
    borderRadiusSmall: '3px',
  },
}

/** 暗色主题覆盖：dark.css 提取值；边框偏离 Duplicati 原值（#454545 保可读性，§4.1 表注）。 */
export const darkThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#2a89c0',
    primaryColorHover: '#5bacdb',
    primaryColorPressed: '#22709f',
    primaryColorSuppl: '#5bacdb',
    infoColor: '#2a89c0',
    infoColorHover: '#5bacdb',
    infoColorPressed: '#22709f',
    infoColorSuppl: '#5bacdb',
    successColor: '#81c601',
    successColorHover: '#9bd221',
    successColorPressed: '#5e8c01',
    warningColor: '#e8a33d',
    warningColorHover: '#f0b45a',
    warningColorPressed: '#c08630',
    errorColor: '#e06c6c',
    errorColorHover: '#ec8080',
    errorColorPressed: '#c25555',
    textColorBase: '#b0b0b0',
    textColor1: '#f0f0f0',
    textColor2: '#c8c8c8',
    textColor3: '#909090',
    bodyColor: '#1a1a1a',
    cardColor: '#212121',
    modalColor: '#212121',
    popoverColor: '#232323',
    tableColor: '#1f1f1f',
    borderColor: '#454545',
    borderRadius: '5px',
    borderRadiusSmall: '3px',
  },
}
