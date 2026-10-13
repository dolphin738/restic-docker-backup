/** dry-run 预览门判定护栏（S-3）：F1（截断禁首跑）与 S-1（stale 预览禁首跑）的契约锁定。
 *
 * TaskWizard 的「确认并立即首跑」门禁 = `isDryRunOk`；本文件锁死其与服务端契约
 * （E36 仅在「无 error 且未截断」时清 dry_run_pending）的前端同口径行为。
 */

import { describe, expect, it } from 'vitest'

import { dryRunBlockReason, isDryRunOk } from '@/components/task/dryRunGate'
import type { DryRunResponse } from '@/api/types'

function makeResult(overrides: Partial<DryRunResponse> = {}): DryRunResponse {
  return {
    items: [],
    warnings: [],
    truncated: false,
    error: null,
    ...overrides,
  }
}

describe('isDryRunOk', () => {
  it('未预览（result=null）不放行', () => {
    expect(isDryRunOk(null)).toBe(false)
  })

  it('预览出错（error 非 null）不放行', () => {
    expect(isDryRunOk(makeResult({ error: 'restic 退出码 1' }))).toBe(false)
  })

  it('截断不放行（F1：盲区下禁止确认首跑）', () => {
    expect(isDryRunOk(makeResult({ truncated: true }))).toBe(false)
  })

  it('无 error 且未截断 → 放行', () => {
    expect(isDryRunOk(makeResult())).toBe(true)
  })

  it('stale 预览即使结果成功也不放行（S-1：预览后源集合已变更）', () => {
    expect(isDryRunOk(makeResult(), true)).toBe(false)
  })

  it('stale 与失败结果叠加仍不放行（判定不因叠加而翻转）', () => {
    expect(isDryRunOk(makeResult({ truncated: true }), true)).toBe(false)
  })
})

describe('dryRunBlockReason', () => {
  it('stale 优先于截断（用户改源后须重扫，截断提示已无意义）', () => {
    expect(dryRunBlockReason(makeResult({ truncated: true }), true)).toBe('stale')
  })

  it('截断 → truncated', () => {
    expect(dryRunBlockReason(makeResult({ truncated: true }), false)).toBe('truncated')
  })

  it('未预览/失败等其余拦截 → hint', () => {
    expect(dryRunBlockReason(null, false)).toBe('hint')
    expect(dryRunBlockReason(makeResult({ error: 'x' }), false)).toBe('hint')
  })
})
