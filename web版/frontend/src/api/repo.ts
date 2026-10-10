/** E30 仓库状态（服务端 30s 进程内缓存，前端不得秒级轮询）。 */

import { request } from './http'
import type { RepoStatusResponse } from './types'

export function getRepoStatus(): Promise<RepoStatusResponse> {
  return request<RepoStatusResponse>('/api/repo/status')
}
