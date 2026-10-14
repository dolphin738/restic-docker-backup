/** 系统设置域数据层（P2 收尾批次 A）。
 *
 * 当前覆盖：E25 缓存维护（GET /api/cache + POST /api/cache/clear）。
 * dump / behavior 分区（批次 B，T06）随后扩展本文件。
 */

import { request } from './http'
import type { CacheUsageResponse, OkResponse } from './types'

/** E25 缓存占用：du 在服务端 to_thread + 30s 超时 + 30s 结果缓存；超时回 truncated 部分值。 */
export function getCacheUsage(): Promise<CacheUsageResponse> {
  return request<CacheUsageResponse>('/api/cache')
}

/** E25 清空缓存（危险操作，审计 `cache.clear`）。
 * 护栏：存在 running/pending 任务 → 409 JOB_CONFLICT（data 带 running_job_id）；
 * 前端须捕获该码给出「有任务进行中」的友好提示。 */
export function clearCache(): Promise<OkResponse> {
  return request<OkResponse>('/api/cache/clear', { method: 'POST' })
}
