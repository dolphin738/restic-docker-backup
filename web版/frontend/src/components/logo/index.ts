/**
 * 品牌 Logo 内联 SVG 组件（统一导出）。
 *
 * 来源：frontend/public/logo.svg（方形徽标）与 frontend/public/logo-horizontal.svg（横向组合）。
 * 二者均从静态 SVG 重构为可复用内联组件，避免重复 repo 与二进制资产、便于主题/尺寸控制。
 *
 * 用法：
 *   import { LogoMark, LogoHorizontal } from '@/components/logo'
 *   <LogoMark class="brand__logo" :alt="t('common.appName')" />
 *
 * 说明：public/logo.svg 仍作为 index.html 的 favicon 静态引用保留（favicon 无法用组件替代）。
 */
export { default as LogoMark } from './LogoMark.vue'
export { default as LogoHorizontal } from './LogoHorizontal.vue'
