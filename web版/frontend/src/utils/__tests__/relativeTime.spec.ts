/** relativeTime 纯函数护栏（P7 D4：视图时间口径切换）。

 * 锁定语义：过去/未来 <60s 分别回落 justNow/soon；<60min 取整分钟；
 * <24h 取整小时；<30d 取整天；>=30d 回落 absolute（携带 Date）。
 * 不依赖 i18n，注入 now 保证确定性。
 */

import { describe, expect, it } from 'vitest'

import { relativeTime } from '@/utils/relativeTime'

const NOW = new Date('2026-10-22T12:00:00+08:00').getTime()

describe('relativeTime 相对时间', () => {
  it('空值回落 none', () => {
    expect(relativeTime(null, NOW)).toEqual({ kind: 'none' })
    expect(relativeTime(undefined, NOW)).toEqual({ kind: 'none' })
    expect(relativeTime('not-a-date', NOW)).toEqual({ kind: 'none' })
  })

  it('过去/未来 60 秒内 → justNow / soon', () => {
    expect(relativeTime(NOW - 30_000, NOW)).toEqual({ kind: 'justNow' })
    expect(relativeTime(NOW + 30_000, NOW)).toEqual({ kind: 'soon' })
  })

  it('跨过 60s 但不足 90s → 取整为 1 分钟', () => {
    // 75s = 1.25 分钟 → round 落 1；90s 已是 1.5 分钟 → round 落 2（见下条进位用例）
    expect(relativeTime(NOW - 75_000, NOW)).toEqual({ kind: 'unit', unit: 'min', amount: 1, future: false })
    expect(relativeTime(NOW + 75_000, NOW)).toEqual({ kind: 'unit', unit: 'min', amount: 1, future: true })
  })

  it('整分钟 / 跨小时进位', () => {
    expect(relativeTime(NOW - 5 * 60_000, NOW)).toEqual({ kind: 'unit', unit: 'min', amount: 5, future: false })
    expect(relativeTime(NOW - 90 * 60_000, NOW)).toEqual({ kind: 'unit', unit: 'hour', amount: 2, future: false })
    expect(relativeTime(NOW + 3 * 24 * 60 * 60_000 - 1, NOW)).toEqual({
      kind: 'unit',
      unit: 'day',
      amount: 3,
      future: true,
    })
  })

  it('>=30 天回落 absolute（携带 Date）', () => {
    const r = relativeTime(NOW - 40 * 24 * 60 * 60_000, NOW)
    expect(r.kind).toBe('absolute')
    if (r.kind === 'absolute') {
      expect(r.date.getTime()).toBe(NOW - 40 * 24 * 60 * 60_000)
    }
  })
})
