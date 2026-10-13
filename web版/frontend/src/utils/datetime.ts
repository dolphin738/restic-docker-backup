/** 时间展示格式化工具（缺陷 C 收口）。
 *
 * 背景：后端与 restic 直出的时间串为 ISO-8601 原始形态，带纳秒级小数秒与 UTC 偏移
 * （例如 `2026-10-13T23:09:20.702791169+08:00`），直接渲染到表格/选择器上观感粗糙、
 * 列宽失控，且纳秒精度对用户没有意义。
 *
 * 纪律：本模块是「面向用户」的时间展示唯一收口点，新增展示位一律复用，不要再手写一遍格式化。
 *
 * 刻意不覆盖的边界（不要改）：作业日志 / SSE 日志流保留后端原始串，
 * 便于排障时与日志行、后端记录逐字比对。
 */

/** 展示回落值：无时间或解析失败时统一显示的长破折号（与表格其它空值口径一致）。 */
export const DATETIME_FALLBACK = '—'

/** 单/双位数补零：4 → '04'、12 → '12'。 */
function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}

/**
 * 把时间值安全解析为 Date；无法解析时返回 null，永不抛异常。
 *
 * 说明：`new Date()` 对 `2026-10-13T23:09:20.702791169+08:00` 这类纳秒串的行为
 * 在各 JS 引擎间并不一致（纳秒位可能被整体丢弃，也可能整串判为 Invalid Date）。
 * 这里先把小数秒截断到毫秒（3 位）再交给 Date 解析，既跨引擎一致，
 * 又完整保留 UTC 偏移语义（仍按原偏移入参，输出时再转本地时区）。
 *
 * @param value ISO 串 / 时间戳（毫秒） / Date / null / undefined。
 * @returns 解析成功的 Date；入参为空或非法时返回 null。
 */
export function parseDateValue(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined) {
    return null
  }
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      return null
    }
    const fromNumber = new Date(value)
    return Number.isNaN(fromNumber.getTime()) ? null : fromNumber
  }

  const raw = value.trim()
  if (raw === '') {
    return null
  }
  // 小数秒截断到毫秒：`.702791169` → `.702`；无小数秒时原样透传。
  const normalized = raw.replace(/(\.\d{3})\d+/, '$1')
  const parsed = new Date(normalized)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/**
 * 格式化为本地时区的 `YYYY-MM-DD HH:mm:ss`。
 *
 * @param value ISO 串 / 时间戳（毫秒） / Date / null / undefined。
 * @returns 可读时间；入参为空或解析失败时回落 `'—'`。
 */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  const date = parseDateValue(value)
  if (date === null) {
    return DATETIME_FALLBACK
  }
  const year = String(date.getFullYear())
  const month = pad2(date.getMonth() + 1)
  const day = pad2(date.getDate())
  const hours = pad2(date.getHours())
  const minutes = pad2(date.getMinutes())
  const seconds = pad2(date.getSeconds())
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
}

/**
 * 带自定义回落文案的格式化：用于「尚未运行 / 仅手动」这类需要语义兜底、而非统一 `'—'` 的元信息位。
 *
 * @param value ISO 串 / 时间戳（毫秒） / Date / null / undefined。
 * @param fallback 入参为空或解析失败时显示的文案（可走 i18n）。
 * @returns 可读时间或自定义回落文案。
 */
export function formatDateTimeOr(
  value: string | number | Date | null | undefined,
  fallback: string,
): string {
  const date = parseDateValue(value)
  if (date === null) {
    return fallback
  }
  return formatDateTime(date)
}
