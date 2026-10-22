/** 展示格式化：字节数（1024 进制单位）。
 *
 * 存在理由（规范 §3.2 单一实现点）：同一套字节格式化曾散落多份（仪表盘容量、
 * 恢复页预览数据量），改口径必漏一处。现统一收口到此。
 *
 * 纪律：纯函数（无组件、无 IO、无 i18n 依赖），便于单测；单位符号与小数点位数
 * 就在此定义，调用方不得再自行拼一份。
 */

const SIZE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

/** 字节数 → 人类可读（1024 进制，B/KiB/MiB/GiB/TiB；整数档不带小数）。 */
export function formatBytes(value: number): string {
  let size = value
  let index = 0
  while (size >= 1024 && index < SIZE_UNITS.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${SIZE_UNITS[index]}`
}

/** 空值安全版：null / undefined / 非有限值 → fallback。
 *  命名与 `utils/datetime.ts` 的 `formatDateTimeOr` 对齐；fallback 由调用方注入
 *  i18n 文案，本文件不依赖 i18n 实例。 */
export function formatBytesOr(value: number | null | undefined, fallback: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return fallback
  }
  return formatBytes(value)
}
