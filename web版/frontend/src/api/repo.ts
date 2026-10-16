/** R-023 仓库管理（repos 表事实源）+ E30 仓库状态 + E29 连通性测试。
 *
 * 所有读写均经 http 层统一注入的 X-Repo-Id 头路由到对应仓库；
 * 凭据写入仍走 /api/config，但键名按仓库命名空间化为 cred.{repo_id}.*。
 */

import { request } from './http'
import type {
  OkResponse,
  RepoCreateRequest,
  RepoOut,
  RepoStatusResponse,
  RepoTestResponse,
  RepoUpdateRequest,
} from './types'

/** 仓库列表（GET /api/repos）。 */
export function listRepos(): Promise<RepoOut[]> {
  return request<RepoOut[]>('/api/repos')
}

/** 新建仓库（POST /api/repos；id 在请求体，服务端做存在性校验）。 */
export function createRepo(body: RepoCreateRequest): Promise<RepoOut> {
  return request<RepoOut>('/api/repos', { method: 'POST', body })
}

/** 单仓库详情（GET /api/repos/{id}）。 */
export function getRepo(id: string): Promise<RepoOut> {
  return request<RepoOut>(`/api/repos/${encodeURIComponent(id)}`)
}

/** 更新仓库（PUT /api/repos/{id}；url/host_tag 三态：null=不变 / ""=清除 / 非空=设置）。 */
export function updateRepo(id: string, body: RepoUpdateRequest): Promise<RepoOut> {
  return request<RepoOut>(`/api/repos/${encodeURIComponent(id)}`, { method: 'PUT', body })
}

/** 删除仓库（DELETE /api/repos/{id}；default 由后端拒绝）。 */
export function deleteRepo(id: string): Promise<OkResponse> {
  return request<OkResponse>(`/api/repos/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** E30 仓库状态（服务端 30s 进程内缓存，前端不得秒级轮询）。 */
export function getRepoStatus(id: string): Promise<RepoStatusResponse> {
  return request<RepoStatusResponse>(`/api/repos/${encodeURIComponent(id)}/status`)
}

/** E29 仓库连通性测试（失败不静默：ok=false + reason）。 */
export function testRepo(id: string): Promise<RepoTestResponse> {
  return request<RepoTestResponse>(`/api/repos/${encodeURIComponent(id)}/test`, { method: 'POST' })
}
