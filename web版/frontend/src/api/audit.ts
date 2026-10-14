/** E26 审计日志查询（R-020，P2 收尾批次 A）。
 *
 * 只读端点（S6：append-only，无 PUT/DELETE）；查询本身不留痕。
 * `action` 为动作名前缀（如 `restore.`），通配符由服务端转义（防 LIKE 注入越权）。
 */

import { request } from './http'
import type { AuditPageResponse, AuditResult } from './types'

/** E26 查询参数；时间边界为 ISO 8601（服务端归一 UTC 后比较，C8）。 */
export interface AuditQuery {
  /** 动作名前缀；undefined = 不过滤。 */
  action?: string
  /** 结果过滤：ok/denied/failed；undefined = 不过滤。 */
  result?: AuditResult
  /** ts 下界（ISO 8601）。 */
  since?: string
  /** ts 上界（ISO 8601）。 */
  until?: string
  /** 单页条数，上限 500（HISTORY_QUERY_MAX_LIMIT）。 */
  limit?: number
  offset?: number
}

/** E26 审计日志分页查询；响应附 actions（去重动作名枚举，筛选下拉数据源）。 */
export function queryAudit(params: AuditQuery = {}): Promise<AuditPageResponse> {
  return request<AuditPageResponse>('/api/audit', {
    query: {
      action: params.action,
      result: params.result,
      since: params.since,
      until: params.until,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
}
