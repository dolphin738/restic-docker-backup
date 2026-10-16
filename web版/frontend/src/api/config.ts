/** E06/E07 配置读写（凭据按仓库命名空间 cred.{repo_id}.* 写入）。 */

import { request } from './http'
import type { ConfigResponse, OkResponse } from './types'

export function getConfig(): Promise<ConfigResponse> {
  return request<ConfigResponse>('/api/config')
}

/** 值传 null 表示清空该键；命中 env 覆盖键时后端整体拒绝（409 ENV_OVERRIDDEN，无部分写入）。 */
export function putConfig(patch: Record<string, string | null>): Promise<OkResponse> {
  return request<OkResponse>('/api/config', { method: 'PUT', body: { patch } })
}
