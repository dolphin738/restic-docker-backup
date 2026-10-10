/** E04 健康检查（免鉴权）：供登录页判断后端是否就绪。 */

import { request } from './http'
import type { HealthResponse } from './types'

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/api/health', { silent: true })
}
