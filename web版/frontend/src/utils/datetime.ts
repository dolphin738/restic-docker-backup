/** 时间展示格式化工具（缺陷 C 收口）。
 *
 * 背景：后端与 restic 直出的时间串为 ISO-8601 原始形态，带纳秒级小数秒与 UTC 偏移
 * （例如 `2026-10-13T23:09:20.702791169+08:00`），直接渲染到表格/选择器上观感粗糙、
 * 列宽失控，且纳秒精度对用户没有意义。
 *
 * 纪律：本模块是「面向用户」的时间展示唯一收口点，新增展示位一律复用，不要再手写一遍格式化。
 *
 * 展示时区（E39）：formatDateTime* 按 setDisplayTimezone 注入的时区渲染
 * （config.timezone 单一事实源，App 启动时经 /api/system/time 拉取）；未注入时
 * 回落浏览器本地时区。后端 UTC 存储 + 前端展示时区，两条线在此汇合。
 *
 * 刻意不覆盖的边界（不要改）：作业日志 / SSE 日志流保留后端原始串，
 * 便于排障时与日志行、后端记录逐字比对。
 */

/** 展示回落值：无时间或解析失败时统一显示的长破折号（与表格其它空值口径一致）。 */
export const DATETIME_FALLBACK = '—'

/** 展示时区（E39 统一口径：config.timezone 单一事实源）。
 *
 * null/undefined = 未注入，按**浏览器本地时区**渲染（历史行为，测试默认态）；
 * 注入后（App 启动经 /api/system/time 拉取，设置页保存时即时刷新）所有
 * formatDateTime* 输出按该 IANA 时区名渲染——「系统设置改时区 → 全站时间
 * 展示跟着变」，不再跟随用户机器时区。
 */
let displayTimezone: string | null = null

/**
 * 设置全局展示时区（IANA 名，如 `Asia/Seoul`）。
 *
 * 空值/坏时区名 = 复位为浏览器本地时区（与后端 resolve_timezone 的
 * fail-safe 方向一致：绝不因坏配置把全站时间渲染挂掉）。
 */
export function setDisplayTimezone(tz: string | null | undefined): void {
  const name = (tz ?? '').trim()
  if (!name) {
    displayTimezone = null
    return
  }
  try {
    // 用 Intl 探一次可解析性：坏名在 formatToParts 时才炸，错峰到设置时点
    new Intl.DateTimeFormat('en-US', { timeZone: name }).format(new Date(0))
    displayTimezone = name
  } catch {
    displayTimezone = null
  }
}

/** 单/双位数补零：4 → '04'、12 → '12'。 */
function pad2(value: number): number | string {
  return value < 10 ? `0${value}` : String(value)
}

/**
 * 按指定 IANA 时区把 Date 渲染为 `YYYY-MM-DD HH:mm:ss`（24 小时制）。
 *
 * 走 Intl.formatToParts 逐部取数而非 toString 切片：时区偏移随夏令时变化，
 * 手算 offset 会错；hourCycle 'h23' 保证 0–23（部分 locale 的默认 hour12
 * 会给出 12/24 这种边界值）。
 */
function formatInZone(date: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(date)
  const pick = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? '0'
  return `${pick('year')}-${pick('month')}-${pick('day')} ${pick('hour')}:${pick('minute')}:${pick('second')}`
}

/**
 * 把时间值安全解析为 Date；无法解析时返回 null，永不抛异常。
 *
 * 说明：`new Date()` 对 `2026-10-13T23:09:20.702791169+08:00` 这类纳秒串的行为
 * 在各 JS 引擎间并不一致（纳秒位可能被整体丢弃，也可能整串判为 Invalid Date）。
 * 这里先把小数秒截断到毫秒（3 位）再交给 Date 解析，既跨引擎一致，
 * 又完整保留 UTC 偏移语义（仍按原偏移入参，输出时再转展示时区）。
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
 * 格式化为展示时区的 `YYYY-MM-DD HH:mm:ss`。
 *
 * 展示时区经 setDisplayTimezone 注入（config.timezone）；未注入时按浏览器
 * 本地时区（历史行为）。
 *
 * @param value ISO 串 / 时间戳（毫秒） / Date / null / undefined。
 * @returns 可读时间；入参为空或解析失败时回落 `'—'`。
 */
export function formatDateTime(value: string | number | Date | null | undefined): string {
  const date = parseDateValue(value)
  if (date === null) {
    return DATETIME_FALLBACK
  }
  if (displayTimezone) {
    return formatInZone(date, displayTimezone)
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
