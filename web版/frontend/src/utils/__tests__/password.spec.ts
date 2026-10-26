/** `generatePassword` 单测（全站唯一密码生成器）：长度/字符集/强度保证/无偏采样/边界。 */

import { describe, expect, it } from 'vitest'
import {
  PASSWORD_CHAR_CLASSES,
  PASSWORD_CHARS,
  PASSWORD_LENGTH,
  generatePassword,
} from '../password'
import { estimatePasswordStrength } from '../passwordStrength'

describe('generatePassword', () => {
  it('默认生成 32 位且字符全部落在默认字符集内（全站统一口径）', () => {
    expect(PASSWORD_LENGTH).toBe(32)
    const pw = generatePassword()
    expect(pw).toHaveLength(32)
    for (const ch of pw) {
      expect(PASSWORD_CHARS).toContain(ch)
    }
  })

  it('不含易混淆字符 0/O/1/l/I（要手抄备份，形近字符是实际出错源）', () => {
    for (let i = 0; i < 30; i += 1) {
      expect(generatePassword()).not.toMatch(/[0O1lI]/)
    }
  })

  it('每类字符至少出现一个（保证强度条恒为「强」，不偶发掉档）', () => {
    for (let i = 0; i < 30; i += 1) {
      const pw = generatePassword()
      for (const cls of PASSWORD_CHAR_CLASSES) {
        expect([...pw].some((ch) => cls.includes(ch))).toBe(true)
      }
    }
  })

  it('生成的密码强度恒为最高档（score = 4）', () => {
    for (let i = 0; i < 20; i += 1) {
      expect(estimatePasswordStrength(generatePassword()).score).toBe(4)
    }
  })

  it('连续生成不重复（32 位 × 65 字符集，碰撞概率可忽略）', () => {
    const set = new Set(Array.from({ length: 20 }, () => generatePassword()))
    expect(set.size).toBe(20)
  })

  it('支持自定义长度与字符集（自定义字符集不做类别保证）', () => {
    expect(generatePassword(8)).toHaveLength(8)
    expect(generatePassword(10, 'abc')).toMatch(/^[abc]{10}$/)
    expect(generatePassword(40)).toHaveLength(40)
  })

  it('长度小于类别数时按类轮流取（不超长）', () => {
    expect(generatePassword(2)).toHaveLength(2)
    expect(generatePassword(3)).toHaveLength(3)
  })

  it('边界：长度 0 或空字符集返回空串（不抛异常）', () => {
    expect(generatePassword(0)).toBe('')
    expect(generatePassword(-1)).toBe('')
    expect(generatePassword(5, '')).toBe('')
  })

  it('拒绝采样覆盖全部字符：小字符集下多次生成的并集应覆盖整个字符集', () => {
    const chars = 'abcdefghijklmnopqrstuvwxyz'
    const seen = new Set<string>()
    for (let i = 0; i < 60; i += 1) {
      for (const ch of generatePassword(chars.length * 4, chars)) {
        seen.add(ch)
      }
    }
    expect(seen.size).toBe(chars.length)
  })

  it('分布无明显偏斜：每个字符出现次数在合理区间（非模偏置的粗检）', () => {
    const chars = 'abcdef'
    const counts = new Map<string, number>()
    for (let i = 0; i < 40; i += 1) {
      for (const ch of generatePassword(30, chars)) {
        counts.set(ch, (counts.get(ch) ?? 0) + 1)
      }
    }
    const values = [...counts.values()]
    const min = Math.min(...values)
    const max = Math.max(...values)
    // 1200 个样本 / 6 类 → 期望 ~200；允许 ±40% 抖动，远超此幅度即提示偏斜
    expect(min).toBeGreaterThan(120)
    expect(max).toBeLessThan(280)
  })
})
