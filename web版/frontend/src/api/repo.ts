/** R-023 仓库管理（repos 表事实源）+ E30 仓库状态 + E29 连通性测试。
 *
 * 所有读写均经 http 层统一注入的 X-Repo-Id 头路由到对应仓库；
 * 凭据写入仍走 /api/config，但键名按仓库命名空间化为 cred.{repo_id}.*。
 */

import { request } from './http'
import type {
  KeyRotateResponse,
  ManualCopyRequest,
  ManualCopyResponse,
  OkResponse,
  RepoCreateRequest,
  RepoIdSuggestion,
  RcloneRemotesOut,
  RepoKeyRotateRequest,
  RepoOut,
  RepoPasswordRequest,
  RepoPasswordResponse,
  RepoStatusResponse,
  RepoTestResponse,
  RepoUpdateRequest,
} from './types'

/** 仓库列表（GET /api/repos）。 */
export function listRepos(): Promise<RepoOut[]> {
  return request<RepoOut[]>('/api/repos')
}

/** 新建仓库（POST /api/repos；id 可选，留空时服务端生成 repo-<ULID>）。 */
export function createRepo(body: RepoCreateRequest): Promise<RepoOut> {
  return request<RepoOut>('/api/repos', { method: 'POST', body })
}

/**
 * A8 服务端生成的候选仓库标识（GET /api/repos/_new_id）。
 * 供新建向导预填（对齐 Duplicati 自动起名）；不落库，用户可改可不改。
 */
export function suggestRepoId(): Promise<RepoIdSuggestion> {
  return request<RepoIdSuggestion>('/api/repos/_new_id')
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

/**
 * A1 rclone 只读探测（GET /api/rclone/remotes）：conf 状态 + 远端名清单。
 *
 * Q17 档位：conf 由宿主机 `rclone config` 生成、compose 挂载（必须 :rw——OAuth
 * token 过期时 rclone 回写 conf）。本接口不生成、不编辑、不展示 conf 内凭据。
 * 用途：rclone 仓库地址输入提示 + A5 类型选择卡 preset 清单。
 */
export function listRcloneRemotes(): Promise<RcloneRemotesOut> {
  return request<RcloneRemotesOut>('/api/rclone/remotes')
}

/**
 * R-023 设置仓库密码（POST /api/repos/{id}/password；**仅密码文件不存在时允许**）。
 *
 * `password` 为空 → 后端生成强随机密码并一次性回显（须提示用户立即备份）；
 * 非空 → 按该值写入，响应不回显。密码既是仓库密钥也是凭据加密密钥源，
 * 覆盖已存在的文件会让仓库不可读，故后端对已存在场景返回 422。
 */
export function setRepoPassword(
  id: string,
  body: RepoPasswordRequest = {},
): Promise<RepoPasswordResponse> {
  return request<RepoPasswordResponse>(`/api/repos/${encodeURIComponent(id)}/password`, {
    method: 'POST',
    body,
  })
}

/**
 * C4 仓库密钥轮换（POST /api/repos/rotate-key；restic key passwd）。
 *
 * 旧密码须仍有效（经当前口令文件提供）；成功后服务端同步重写口令文件。
 * 新密码只经请求体，响应不回显。
 */
export function rotateRepoKey(body: RepoKeyRotateRequest): Promise<KeyRotateResponse> {
  return request<KeyRotateResponse>('/api/repos/rotate-key', { method: 'POST', body })
}

/**
 * 重建默认仓库（POST /api/repos/{id}/rebuild）：清空全部快照并用新密码重新初始化。
 *
 * 仅本地文件系统后端 + 默认仓库可用；忘记密码时的自救通道（记得原密码应走「重设密码」）。
 * `password` 为空 → 后端生成强随机密码并一次性回显（须立即备份）；非空 → 按该值写入。
 */
export function rebuildRepo(id: string, body: RepoPasswordRequest = {}): Promise<RepoPasswordResponse> {
  return request<RepoPasswordResponse>(`/api/repos/${encodeURIComponent(id)}/rebuild`, {
    method: 'POST',
    body,
  })
}

/**
 * C4 手动 copy（POST /api/copy；搬家/建异地副本场景，增量只传新快照）。
 *
 * 两仓须均已配置地址与密码；exit_code -1 = 超时中断（增量可续传，重跑补齐）。
 */
export function manualCopy(body: ManualCopyRequest): Promise<ManualCopyResponse> {
  return request<ManualCopyResponse>('/api/copy', { method: 'POST', body })
}
