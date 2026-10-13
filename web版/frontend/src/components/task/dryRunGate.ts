/** dry-run 预览门判定（F1 + S-1 契约的单一实现点，TaskWizard 消费）。
 *
 * 服务端契约（R-026-08）：E36 仅在「无 error **且未截断**」时才清 dry_run_pending——
 * 截断 = 只看到部分扫描范围（DRY_RUN_MAX_ITEMS 上限），服务端保守保持 pending=1，
 * 前端同口径禁止在盲区下「确认并立即首跑」（F1，QA 复验项）。
 *
 * S-1（stale 预览窗口）：编辑流下预览成功**之后**又改动 sources/excludes，
 * 旧预览不再代表当前源集合 → `stale=true` 使判定失效，须重新预览。
 */

import type { DryRunResponse } from '@/api/types'

/** 预览结果是否放行「确认并立即首跑」。 */
export function isDryRunOk(result: DryRunResponse | null, stale = false): boolean {
  return !stale && result !== null && result.error === null && !result.truncated
}

export type DryRunBlockReason = 'hint' | 'truncated' | 'stale'

/** 门禁拦截原因（供 UI 映射 i18n 提示文案；放行时调用方不会读取）。 */
export function dryRunBlockReason(result: DryRunResponse | null, stale = false): DryRunBlockReason {
  if (stale) {
    return 'stale'
  }
  if (result?.truncated === true) {
    return 'truncated'
  }
  return 'hint'
}
