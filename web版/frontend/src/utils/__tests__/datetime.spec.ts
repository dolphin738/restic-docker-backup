/** formatDateTime / parseDateValue 单测（缺陷 C 收口工具）。
 *
 * 重点覆盖三类真实输入：
 * 1. restic 直出的纳秒级 ISO 串（缺陷 C 真机样例）；
 * 2. 后端 datetime.isoformat() 微秒串；
 * 3. 空 / 非法的回落路径。
 */

import { afterEach, describe, expect, it } from 'vitest'
import {
  DATETIME_FALLBACK,
  formatDateTime,
  formatDateTimeOr,
  parseDateValue,
  setDisplayTimezone,
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

describe('E39 展示时区（config.timezone 单一事实源）', () => {
  // 模块级状态会被后续用例继承，故每个用例后复位（afterEach 兜底 + 显式调用双保险）
  afterEach(() => {
    setDisplayTimezone(null)
  })

  it('注入时区后按该 IANA 时区渲染（UTC 串 → +9 的 Seoul 时间）', () => {
    setDisplayTimezone('Asia/Seoul')
    // 2026-10-25T07:40:02Z + 9h = 16:40:02（UTC 串不跨日）
    expect(formatDateTime('2026-10-25T07:40:02Z')).toBe('2026-10-25 16:40:02')
  })

  it('跨日场景：UTC 23:30 → Seoul 次日 08:30', () => {
    setDisplayTimezone('Asia/Seoul')
    expect(formatDateTime('2026-10-25T23:30:00Z')).toBe('2026-10-26 08:30:00')
  })

  it('带偏移的输入串仍按展示时区换算（+08:00 串 → Seoul）', () => {
    setDisplayTimezone('Asia/Seoul')
    // 2026-10-13T23:09:20+08:00 = 15:09:20Z → Seoul 次日 00:09:20
    expect(formatDateTime('2026-10-13T23:09:20+08:00')).toBe('2026-10-14 00:09:20')
  })

  it('UTC 时区渲染（+0，回归：不引入偏移）', () => {
    setDisplayTimezone('UTC')
    expect(formatDateTime('2026-10-25T07:40:02Z')).toBe('2026-10-25 07:40:02')
  })

  it('复位为 null 后回落浏览器本地时区（与 new Date 本地 getter 一致）', () => {
    setDisplayTimezone('Asia/Seoul')
    setDisplayTimezone(null)
    const value = '2026-10-25T07:40:02Z'
    const local = new Date(value)
    const expected = `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')} ${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}:${String(local.getSeconds()).padStart(2, '0')}`
    expect(formatDateTime(value)).toBe(expected)
  })

  it('坏时区名静默回落浏览器本地（与后端 resolve_timezone fail-safe 同向）', () => {
    setDisplayTimezone('Asia/Seoul')
    setDisplayTimezone('Not/A_Zone')
    const value = '2026-10-25T07:40:02Z'
    const local = new Date(value)
    const expected = `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')} ${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}:${String(local.getSeconds()).padStart(2, '0')}`
    expect(formatDateTime(value)).toBe(expected)
  })

  it('空串/空白同样复位为浏览器本地', () => {
    setDisplayTimezone('Asia/Seoul')
    setDisplayTimezone('   ')
    const value = '2026-10-25T07:40:02Z'
    const local = new Date(value)
    const expected = `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, '0')}-${String(local.getDate()).padStart(2, '0')} ${String(local.getHours()).padStart(2, '0')}:${String(local.getMinutes()).padStart(2, '0')}:${String(local.getSeconds()).padStart(2, '0')}`
    expect(formatDateTime(value)).toBe(expected)
  })

  it('formatDateTimeOr 与 formatDateTime 同步跟随展示时区', () => {
    setDisplayTimezone('Asia/Seoul')
    expect(formatDateTimeOr('2026-10-25T07:40:02Z', '无')).toBe('2026-10-25 16:40:02')
  })
})
