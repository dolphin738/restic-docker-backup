/** E06/E07 配置读写 + E29 仓库连通性测试。 */

import { request } from './http'
import type { ConfigResponse, OkResponse, RepoTestResponse } from './types'

export function getConfig(): Promise<ConfigResponse> {
  return request<ConfigResponse>('/api/config')
}

/** 值传 null 表示清空该键；命中 env 覆盖键时后端整体拒绝（409 ENV_OVERRIDDEN，无部分写入）。 */
export function putConfig(patch: Record<string, string | null>): Promise<OkResponse> {
  return request<OkResponse>('/api/config', { method: 'PUT', body: { patch } })
}

/** 探测失败不静默：ok=false 时 reason 给可操作原因。 */
export function testRepo(): Promise<RepoTestResponse> {
  return request<RepoTestResponse>('/api/repo/test', { method: 'POST' })
}
