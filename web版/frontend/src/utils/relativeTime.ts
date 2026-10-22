/** 相对时间格式化（P7 D4：Duplicati 主页「Relative / Actual」切换）。

 * 与 `datetime.ts` 的分工：`datetime.ts` 负责绝对时间（YYYY-MM-DD HH:mm:ss）展示，
 * 本模块负责「N 分钟前 / 后天」这类相对表述。两者都由视图切换统一驱动，
 * 新增展示位一律复用，不要再手写一遍格式化。

 * 设计要点：纯函数、不依赖 i18n，便于单测；展示文案由消费方用 i18n 拼装
 * （`RelativeTimeResult` 只携带「量 + 单位 + 方向 + 是否回落绝对」等结构化信息）。
 */

import { parseDateValue } from '@/utils/datetime'

/** 相对时间结构化结果（与具体语种解耦）。 */
export type RelativeTimeResult =
  | { kind: 'none' }
  | { kind: 'justNow' } // 过去 60 秒内
  | { kind: 'soon' } // 未来 60 秒内
  | { kind: 'unit'; unit: 'min' | 'hour' | 'day'; amount: number; future: boolean }
  | { kind: 'absolute'; date: Date } // 超过 30 天，回落绝对时间

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
/** 超过该阈值（30 天）回落绝对时间，避免「365 天前」无意义。 */
const ABSOLUTE_THRESHOLD = 30 * DAY

/**
 * 把时间值解析为相对表述的结构化结果。
 *
 * @param value ISO 串 / 时间戳（毫秒） / Date / null / undefined。
 * @param now 参照「现在」（默认 Date.now()，便于测试注入）。
 * @returns 见 `RelativeTimeResult`。
 */
export function relativeTime(
  value: string | number | Date | null | undefined,
  now: number = Date.now(),
): RelativeTimeResult {
  const date = parseDateValue(value)
  if (date === null) {
    return { kind: 'none' }
  }
  // 以「整分」为最小粒度裁掉秒级抖动（避免「1 分钟前」反复跳成「0 分钟前」）。
  const diff = date.getTime() - now
  const abs = Math.abs(diff)
  const future = diff > 0

  if (abs < MINUTE) {
    return future ? { kind: 'soon' } : { kind: 'justNow' }
  }
  if (abs >= ABSOLUTE_THRESHOLD) {
    return { kind: 'absolute', date }
  }
  if (abs < HOUR) {
    return { kind: 'unit', unit: 'min', amount: Math.max(1, Math.round(abs / MINUTE)), future }
  }
  if (abs < DAY) {
    return { kind: 'unit', unit: 'hour', amount: Math.max(1, Math.round(abs / HOUR)), future }
  }
  return { kind: 'unit', unit: 'day', amount: Math.max(1, Math.round(abs / DAY)), future }
}
