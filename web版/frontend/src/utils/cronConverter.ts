/**
 * cron 双向转换器（A6 调度双模式核心，纯函数、无副作用、可单测）。
 *
 * 设计约束：
 * - 仅覆盖「简单模式」可表达的受控子集；任何超出该子集的 cron 一律解析为 null，
 *   交由高级模式直编——保证「生成器只产出可反向解析的表达式，编辑回显永不丢信息」。
 * - 星期字段使用**标准 crontab 语义**（0=周日 … 6=周六），与后端 scheduler.py 的
 *   `_normalize_cron` 星期修正护栏（APScheduler 0=周一 → 标准语义）对齐；生成时写标准值，
 *   后端会再做 (n+6)%7 换算，前端无需关心 APScheduler 内部偏移。
 * - 后端零改动：DB 仍存 5 字段 cron 字符串，调度语义全复用。
 */

export type SimpleFrequency =
  | 'hourly'
  | 'daily'
  | 'weekly'
  | 'monthly'
  | 'yearly'
  | 'every_n_hours'
  | 'every_n_days'

export interface SimpleSchedule {
  frequency: SimpleFrequency
  /** 0-59 */
  minute: number
  /** 0-23（daily/weekly/monthly/yearly） */
  hour: number
  /** 0-6（周日=0 … 周六=6），weekly 多选 */
  daysOfWeek: number[]
  /** 1-31，monthly 多选 */
  daysOfMonth: number[]
  /** 1-12，yearly */
  month: number
  /** ≥1，every_n_hours */
  intervalHours: number
  /** ≥1，every_n_days */
  intervalDays: number
}

const DOW_WILDCARD = '*'
const DOM_WILDCARD = '*'
const MONTH_WILDCARD = '*'

function isInt(value: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(value)) return false
  const n = Number(value)
  return n >= min && n <= max
}

/** 解析逗号分隔的整数列表（允许单个值），逐元素范围校验，返回升序去重数组；非法 → null。 */
function parseNumberList(field: string, min: number, max: number): number[] | null {
  if (field === '*') return null // 通配不是列表
  const parts = field.split(',').map((p) => p.trim())
  if (parts.length === 0) return null
  const set = new Set<number>()
  for (const part of parts) {
    if (!isInt(part, min, max)) return null
    set.add(Number(part))
  }
  return [...set].sort((a, b) => a - b)
}

/** 解析步长写法（如 `* /N`）；仅当为步长且 N≥1 合法时返回 N，否则 null。 */
function parseStep(field: string): number | null {
  const match = /^[*]\/(\d+)$/.exec(field)
  if (!match) return null
  const n = Number(match[1])
  return n >= 1 ? n : null
}

/** 简单模式 → 标准 cron 字符串（受控子集）。 */
export function simpleToCron(s: SimpleSchedule): string {
  const m = String(s.minute)
  switch (s.frequency) {
    case 'hourly':
      return `${m} * * * *`
    case 'daily':
      return `${m} ${s.hour} * * *`
    case 'weekly': {
      const dow = s.daysOfWeek.slice().sort((a, b) => a - b).join(',') || '0'
      return `${m} ${s.hour} * * ${dow}`
    }
    case 'monthly': {
      const dom = s.daysOfMonth.slice().sort((a, b) => a - b).join(',') || '1'
      return `${m} ${s.hour} ${dom} * *`
    }
    case 'yearly': {
      const dom = s.daysOfMonth.slice().sort((a, b) => a - b).join(',') || '1'
      return `${m} ${s.hour} ${dom} ${s.month} *`
    }
    case 'every_n_hours':
      return `${m} */${s.intervalHours} * * *`
    case 'every_n_days':
      return `${m} ${s.hour} */${s.intervalDays} * *`
    default:
      return `${m} ${s.hour} * * *`
  }
}

/**
 * 标准 cron → 简单模式（无法表达返回 null，交给高级模式）。
 * 仅接受受控子集；任何步长写法落到非约定位置、范围/列表混入 OR 语义（dom 与 dow 同时非 *）、
 * 字段数非 5、值越界等一律返回 null。
 */
export function cronToSimple(cron: string): SimpleSchedule | null {
  const fields = cron.trim().split(/\s+/)
  if (fields.length !== 5) return null
  const [mField, hField, domField, monthField, dowField] = fields

  // 分钟、小时：受控子集里恒为单一整数
  if (!isInt(mField, 0, 59)) return null
  const minute = Number(mField)

  // 小时：daily/weekly/monthly/yearly/every_n_days 为单一整数；every_n_hours 为 */N；hourly 为 *
  const hourStep = parseStep(hField)
  const hourIsWild = hField === DOW_WILDCARD
  const hourIsNum = isInt(hField, 0, 23)
  const hourNum = hourIsNum ? Number(hField) : 0

  // 日/月/星期通配判定
  const domIsWild = domField === DOM_WILDCARD
  const monthIsWild = monthField === MONTH_WILDCARD
  const dowIsWild = dowField === DOW_WILDCARD

  const domStep = parseStep(domField)
  const domList = domIsWild ? null : parseNumberList(domField, 1, 31)
  const monthNum = isInt(monthField, 1, 12) ? Number(monthField) : 0
  const dowList = dowIsWild ? null : parseNumberList(dowField, 0, 6)

  // ---- 频率判定（互斥、顺序敏感）----
  // 每小时：h=* 且 dom/month/dow 全通配
  if (hourIsWild && domIsWild && monthIsWild && dowIsWild) {
    return { frequency: 'hourly', minute, hour: 0, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 1 }
  }
  // 每 N 小时：h=*/N 且其余通配
  if (hourStep !== null && domIsWild && monthIsWild && dowIsWild) {
    return { frequency: 'every_n_hours', minute, hour: 0, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: hourStep, intervalDays: 1 }
  }
  // 每天：h=数字 且 dom/month/dow 全通配
  if (hourIsNum && domIsWild && monthIsWild && dowIsWild) {
    return { frequency: 'daily', minute, hour: hourNum, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 1 }
  }
  // 每 N 天：h=数字、dom=*/N、month/dow 通配
  if (hourIsNum && domStep !== null && monthIsWild && dowIsWild) {
    return { frequency: 'every_n_days', minute, hour: hourNum, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: domStep }
  }
  // 每周：h=数字、dow 列表、dom/month 通配
  if (hourIsNum && dowList !== null && domIsWild && monthIsWild) {
    return { frequency: 'weekly', minute, hour: hourNum, daysOfWeek: dowList, daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 1 }
  }
  // 每月：h=数字、dom 列表、month/dow 通配
  if (hourIsNum && domList !== null && monthIsWild && dowIsWild) {
    return { frequency: 'monthly', minute, hour: hourNum, daysOfMonth: domList, daysOfWeek: [], month: 1, intervalHours: 1, intervalDays: 1 }
  }
  // 每年：h=数字、dom 列表、month 数字、dow 通配
  if (hourIsNum && domList !== null && !monthIsWild && dowIsWild && monthNum >= 1) {
    return { frequency: 'yearly', minute, hour: hourNum, daysOfMonth: domList, daysOfWeek: [], month: monthNum, intervalHours: 1, intervalDays: 1 }
  }
  // 其余（含 dom 与 dow 同时非通配的 OR 语义）= 超出受控子集
  return null
}

/** 默认简单调度：每天 02:00（与既有 daily 模板一致）。 */
export function defaultSimpleSchedule(): SimpleSchedule {
  return {
    frequency: 'daily',
    minute: 0,
    hour: 2,
    daysOfWeek: [1],
    daysOfMonth: [1],
    month: 1,
    intervalHours: 1,
    intervalDays: 1,
  }
}
