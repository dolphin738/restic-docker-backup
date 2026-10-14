/** 系统设置域数据层（批次 A：E25 缓存维护；批次 B：dump.* 与 behavior.* 键面经 E07 提交）。
 *
 * dump 表单/行为开关的读写均走既有 E06/E07 config 通道（键级校验与 dump.password
 * 三态口径在服务端 config 路由钩子，前端只拼 patch）。
 */

import { request } from './http'
import { putConfig } from './config'
import type { CacheUsageResponse, OkResponse } from './types'

/** E25 缓存占用：du 在服务端 to_thread + 30s 超时 + 30s 结果缓存；超时回 truncated 部分值。 */
export function getCacheUsage(): Promise<CacheUsageResponse> {
  return request<CacheUsageResponse>('/api/cache')
}

/** E25 清空缓存（危险操作，审计 `cache.clear`）。
 * 护栏：存在 running/pending 任务 → 409 JOB_CONFLICT（data 带 running_job_id）。
 * silent：错误由页面层统一处理（409 友好提示 + 其余错误显式弹出），消除双重 toast。 */
export function clearCache(): Promise<OkResponse> {
  return request<OkResponse>('/api/cache/clear', { method: 'POST', silent: true })
}

/** 批次 B：dump.* 与 behavior.* 键面提交（E07 patch 通道；`config.update` 审计由服务端埋点）。
 * 值一律字符串（开关 '1'/'0'、数字十进制串）；null 表示清空该键；
 * dump.password 三态：undefined=不变 / ''=清除 / 非空=设置（由页面层拼 patch）。 */
export function saveDumpBehaviorConfig(patch: Record<string, string | null>): Promise<OkResponse> {
  return putConfig(patch)
}
