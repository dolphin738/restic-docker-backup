/** formatDateTime / parseDateValue 单测（缺陷 C 收口工具）。
 *
 * 重点覆盖三类真实输入：
 * 1. restic 直出的纳秒级 ISO 串（缺陷 C 真机样例）；
 * 2. 后端 datetime.isoformat() 微秒串；
 * 3. 空 / 非法的回落路径。
 */

import { describe, expect, it } from 'vitest'
import {
  DATETIME_FALLBACK,
  formatDateTime,
  formatDateTimeOr,
  parseDateValue,
} from '@/utils/datetime'

describe('parseDateValue', () => {
  it('null / undefined / 空串 / 空白串返回 null', () => {
    expect(parseDateValue(null)).toBeNull()
    expect(parseDateValue(undefined)).toBeNull()
    expect(parseDateValue('')).toBeNull()
    expect(parseDateValue('   ')).toBeNull()
  })

  it('restic 纳秒 ISO 串（缺陷 C 真机样例）可解析且保留偏移语义', () => {
    const parsed = parseDateValue('2026-10-13T23:09:20.702791169+08:00')
    expect(parsed).not.toBeNull()
    // 小数秒截断到毫秒后仍指向同一时刻：UTC 应为 15:09:20.702
    expect(parsed!.toISOString()).toBe('2026-10-13T15:09:20.702Z')
  })

  it('后端微秒 ISO 串可解析', () => {
    const parsed = parseDateValue('2026-10-13T23:09:20.702791+08:00')
    expect(parsed!.toISOString()).toBe('2026-10-13T15:09:20.702Z')
  })

  it('无小数秒的 ISO 串可解析', () => {
    const parsed = parseDateValue('2026-10-13T15:09:20Z')
    expect(parsed!.toISOString()).toBe('2026-10-13T15:09:20.000Z')
  })

  it('非法串返回 null，不抛异常', () => {
    expect(parseDateValue('not-a-date')).toBeNull()
    expect(parseDateValue('2026-13-99T99:99:99Z')).toBeNull()
  })

  it('Date 入参原样透传（Invalid Date 亦返回 null）', () => {
    const date = new Date('2026-10-13T15:09:20.702Z')
    expect(parseDateValue(date)).toBe(date)
    expect(parseDateValue(new Date('invalid'))).toBeNull()
  })
})

describe('formatDateTime', () => {
  it('按本地时区输出 YYYY-MM-DD HH:mm:ss', () => {
    // 固定时区避免 CI 机器差异：用 UTC 时间戳断言本地渲染
    const utc = new Date('2026-10-13T15:09:20.702Z')
    const expected = (() => {
      const p = (n: number) => String(n).padStart(2, '0')
      return (
        `${utc.getFullYear()}-${p(utc.getMonth() + 1)}-${p(utc.getDate())} ` +
        `${p(utc.getHours())}:${p(utc.getMinutes())}:${p(utc.getSeconds())}`
      )
    })()
    expect(formatDateTime('2026-10-13T15:09:20.702791169Z')).toBe(expected)
  })

  it('空 / 非法输入回落 —', () => {
    expect(formatDateTime(null)).toBe(DATETIME_FALLBACK)
    expect(formatDateTime(undefined)).toBe(DATETIME_FALLBACK)
    expect(formatDateTime('')).toBe(DATETIME_FALLBACK)
    expect(formatDateTime('garbage')).toBe(DATETIME_FALLBACK)
  })

  it('纳秒串不含纳秒/偏移尾巴', () => {
    const text = formatDateTime('2026-10-13T23:09:20.702791169+08:00')
    expect(text).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
  })
})

describe('formatDateTimeOr', () => {
  it('可解析时与 formatDateTime 一致', () => {
    expect(formatDateTimeOr('2026-10-13T15:09:20Z', '无')).toBe(formatDateTime('2026-10-13T15:09:20Z'))
  })

  it('不可解析时使用自定义回落文案', () => {
    expect(formatDateTimeOr(null, '无')).toBe('无')
    expect(formatDateTimeOr('', '仅手动')).toBe('仅手动')
    expect(formatDateTimeOr('nope', 'never run')).toBe('never run')
  })
})
