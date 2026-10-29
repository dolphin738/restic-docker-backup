/** E26 审计日志查询（R-020，P2 收尾批次 A）。
 *
 * 只读端点（S6：append-only，无 PUT/DELETE）；查询本身不留痕。
 * `action` 为动作名前缀（如 `restore.`），通配符由服务端转义（防 LIKE 注入越权）。
 */

import { getActivePinia } from 'pinia'
import { request } from './http'
import { useUiStore } from '@/stores/ui'
import type { AuditPageResponse, AuditResult } from './types'

/** 审计压缩归档文件（R-020 裁决3）：``/api/audit/archive`` 响应项。 */
export interface AuditArchiveItem {
  name: string
  size_bytes: number
  mtime_iso: string
}

function currentRepoId(): string {
  if (getActivePinia()) {
    return useUiStore().currentRepoId || 'default'
  }
  return 'default'
}

/** E26 查询参数；时间边界为 ISO 8601（服务端归一 UTC 后比较，C8）。 */
export interface AuditQuery {
  /** 动作名前缀；undefined = 不过滤。与 `actions` 互斥，同时给以 `actions` 为准。 */
  action?: string
  /** 动作名前缀**列表**（服务端取并集）：归档页「操作日志」按动作清单查询
   *  （repo.purge / repo.id_release / rclone.config_restore 不共享单一前缀）。 */
  actions?: string[]
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
      action: params.actions?.length ? params.actions.join(',') : params.action,
      result: params.result,
      since: params.since,
      until: params.until,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
}

/** R-020 裁决3：列出审计压缩归档文件（只读）。 */
export function listAuditArchives(): Promise<AuditArchiveItem[]> {
  return request<AuditArchiveItem[]>('/api/audit/archive')
}

/** E26 导出当前筛选为 NDJSON（流式响应，媒体类型 ``application/x-ndjson``）。
 * 返回原始文本（每行一条 JSON），由页面层转 Blob 触发下载（S6 只读、不留痕）。 */
export async function exportAuditNdjson(params: AuditQuery = {}): Promise<string> {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries({
    action: params.action,
    result: params.result,
    since: params.since,
    until: params.until,
  })) {
    if (value !== undefined && value !== '') {
      search.set(key, String(value))
    }
  }
  const qs = search.toString()
  const url = qs ? `/api/audit/export?${qs}` : '/api/audit/export'
  const response = await fetch(url, {
    credentials: 'include',
    headers: { 'X-Repo-Id': currentRepoId() },
  })
  if (!response.ok) {
    throw new Error(`audit export failed: ${response.status}`)
  }
  return response.text()
}
