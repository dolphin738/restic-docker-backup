import { describe, expect, it } from 'vitest'
import {
  cronToSimple,
  defaultSimpleSchedule,
  simpleToCron,
  type SimpleSchedule,
} from '../cronConverter'

describe('simpleToCron', () => {
  it('hourly', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'hourly', minute: 15 })).toBe('15 * * * *')
  })
  it('daily', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'daily', minute: 0, hour: 2 })).toBe('0 2 * * *')
  })
  it('weekly single', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'weekly', minute: 0, hour: 3, daysOfWeek: [1] })).toBe('0 3 * * 1')
  })
  it('weekly multi sorted', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'weekly', minute: 30, hour: 3, daysOfWeek: [5, 1, 3] })).toBe('30 3 * * 1,3,5')
  })
  it('monthly single', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'monthly', minute: 0, hour: 4, daysOfMonth: [1] })).toBe('0 4 1 * *')
  })
  it('monthly multi', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'monthly', minute: 0, hour: 4, daysOfMonth: [15, 1] })).toBe('0 4 1,15 * *')
  })
  it('yearly', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'yearly', minute: 0, hour: 0, daysOfMonth: [1], month: 1 })).toBe('0 0 1 1 *')
  })
  it('every_n_hours', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'every_n_hours', minute: 0, intervalHours: 6 })).toBe('0 */6 * * *')
  })
  it('every_n_days', () => {
    expect(simpleToCron({ ...defaultSimpleSchedule(), frequency: 'every_n_days', minute: 0, hour: 2, intervalDays: 3 })).toBe('0 2 */3 * *')
  })
})

describe('cronToSimple', () => {
  it('hourly', () => {
    expect(cronToSimple('15 * * * *')).toMatchObject({ frequency: 'hourly', minute: 15 })
  })
  it('daily', () => {
    expect(cronToSimple('0 2 * * *')).toMatchObject({ frequency: 'daily', minute: 0, hour: 2 })
  })
  it('weekly Monday is dow=1 (standard crontab)', () => {
    expect(cronToSimple('0 3 * * 1')).toMatchObject({ frequency: 'weekly', hour: 3, daysOfWeek: [1] })
  })
  it('weekly multi', () => {
    expect(cronToSimple('30 3 * * 1,3,5')).toMatchObject({ frequency: 'weekly', daysOfWeek: [1, 3, 5] })
  })
  it('monthly', () => {
    expect(cronToSimple('0 4 1 * *')).toMatchObject({ frequency: 'monthly', daysOfMonth: [1] })
  })
  it('monthly multi', () => {
    expect(cronToSimple('0 4 1,15 * *')).toMatchObject({ frequency: 'monthly', daysOfMonth: [1, 15] })
  })
  it('yearly', () => {
    expect(cronToSimple('0 0 1 1 *')).toMatchObject({ frequency: 'yearly', month: 1, daysOfMonth: [1] })
  })
  it('every_n_hours', () => {
    expect(cronToSimple('0 */6 * * *')).toMatchObject({ frequency: 'every_n_hours', intervalHours: 6 })
  })
  it('every_n_days', () => {
    expect(cronToSimple('0 2 */3 * *')).toMatchObject({ frequency: 'every_n_days', hour: 2, intervalDays: 3 })
  })
})

describe('round-trip (generator subset is always reversible)', () => {
  // 每个 spec 必须是「完整归一态」：解析器对非相关维度恒返回空列表/默认值，
  // 故 round-trip 输入不得携带 defaultSimpleSchedule 的残留字段。
  const base = defaultSimpleSchedule()
  const cases: Array<[string, SimpleSchedule]> = [
    ['15 * * * *', { ...base, frequency: 'hourly', minute: 15, hour: 0, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 1 }],
    ['0 2 * * *', { ...base, frequency: 'daily', minute: 0, hour: 2, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 1 }],
    ['0 3 * * 1', { ...base, frequency: 'weekly', minute: 0, hour: 3, daysOfWeek: [1], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 1 }],
    ['30 3 * * 1,3,5', { ...base, frequency: 'weekly', minute: 30, hour: 3, daysOfWeek: [1, 3, 5], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 1 }],
    ['0 4 1 * *', { ...base, frequency: 'monthly', minute: 0, hour: 4, daysOfMonth: [1], daysOfWeek: [], month: 1, intervalHours: 1, intervalDays: 1 }],
    ['0 4 1,15 * *', { ...base, frequency: 'monthly', minute: 0, hour: 4, daysOfMonth: [1, 15], daysOfWeek: [], month: 1, intervalHours: 1, intervalDays: 1 }],
    ['0 0 1 1 *', { ...base, frequency: 'yearly', minute: 0, hour: 0, daysOfMonth: [1], daysOfWeek: [], month: 1, intervalHours: 1, intervalDays: 1 }],
    ['0 */6 * * *', { ...base, frequency: 'every_n_hours', minute: 0, hour: 0, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: 6, intervalDays: 1 }],
    ['0 2 */3 * *', { ...base, frequency: 'every_n_days', minute: 0, hour: 2, daysOfWeek: [], daysOfMonth: [], month: 1, intervalHours: 1, intervalDays: 3 }],
  ]
  it.each(cases)('cronToSimple(simpleToCron(%s)) == spec', (_cron, spec) => {
    expect(cronToSimple(simpleToCron(spec))).toEqual(spec)
  })
})

describe('cronToSimple falls back to advanced mode (returns null)', () => {
  // dom 与 dow 同时非通配 = OR 语义，超出受控子集
  it('0 2 1 * 1 (dom AND dow)', () => {
    expect(cronToSimple('0 2 1 * 1')).toBeNull()
  })
  // 步长落在错误位置
  it('0 2 * */2 * (month step)', () => {
    expect(cronToSimple('0 2 * */2 *')).toBeNull()
  })
  it('0 2 1,15 * 3 (month number with dom list is yearly only; dom list + month list invalid)', () => {
    expect(cronToSimple('0 2 1,15 * 3')).toBeNull()
  })
  // 范围写法不在受控子集
  it('0 3 * * 1-5 (range)', () => {
    expect(cronToSimple('0 3 * * 1-5')).toBeNull()
  })
  // 字段数非 5
  it('0 2 * * (4 fields)', () => {
    expect(cronToSimple('0 2 * *')).toBeNull()
  })
  // 值越界
  it('0 25 * * * (hour out of range)', () => {
    expect(cronToSimple('0 25 * * *')).toBeNull()
  })
  it('0 2 * * 8 (dow out of range)', () => {
    expect(cronToSimple('0 2 * * 8')).toBeNull()
  })
  it('60 2 * * * (minute out of range)', () => {
    expect(cronToSimple('60 2 * * *')).toBeNull()
  })
  // 表达式含名称写法
  it('0 2 * * MON (named dow)', () => {
    expect(cronToSimple('0 2 * * MON')).toBeNull()
  })
})

describe('defaultSimpleSchedule', () => {
  it('daily 02:00', () => {
    const d = defaultSimpleSchedule()
    expect(d.frequency).toBe('daily')
    expect(d.hour).toBe(2)
    expect(d.minute).toBe(0)
  })
})
