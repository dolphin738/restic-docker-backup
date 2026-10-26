/** G4 口令强度估算的护栏用例（纯函数，无 DOM 依赖）。 */
import { describe, expect, it } from 'vitest'
// 生成器已收口到 utils/password.ts（2026-10-27 全站统一 32 位），
// 本文件只剩强度估算；生成相关的用例见 utils/__tests__/password.spec.ts。
import { estimatePasswordStrength } from '@/utils/passwordStrength'

describe('estimatePasswordStrength', () => {
  it('空口令计 0', () => {
    expect(estimatePasswordStrength('')).toEqual({ score: 0, classes: 0, length: 0 })
  })

  it('低于 10 位一律计 0（长度是门槛，类别再多也不放行）', () => {
    expect(estimatePasswordStrength('Ab1!efgh').score).toBe(0)
  })

  it('刚达门槛的纯小写口令计 1（弱）', () => {
    expect(estimatePasswordStrength('abcdefghij').score).toBe(1)
  })

  it('三类字符上探一档', () => {
    // 10 位 + 小写/大写/数字 = 3 类
    expect(estimatePasswordStrength('abcdefghI1').score).toBe(2)
  })

  it('四类字符且 ≥12 位封顶 4', () => {
    expect(estimatePasswordStrength('abcdefghI1!xyz').score).toBe(4)
  })

  it('≥16 位给基线 2（长度换档）', () => {
    expect(estimatePasswordStrength('a'.repeat(16)).score).toBe(2)
  })

  it('分数恒在 0–4 之间（不会因极端输入越界）', () => {
    const long = 'Aa1!' + 'x'.repeat(200)
    const { score } = estimatePasswordStrength(long)
    expect(score).toBeGreaterThanOrEqual(0)
    expect(score).toBeLessThanOrEqual(4)
  })

  it('classes 统计四类字符', () => {
    expect(estimatePasswordStrength('aA1!').classes).toBe(4)
  })
})
