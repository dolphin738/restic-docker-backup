/** E17 快照列表（limit/offset 分页，可选 tag 精确过滤）+ E18 快照文件树（不透明游标，只透传）+
 * E21 forget 预览（R-016 预览-确认流前半，服务端恒 --dry-run 只读）+
 * P3 快照内文件搜索（R6，恢复向导 select-files 搜索框）。 */

import { passCursor, request } from './http'
import type {
  ForgetPreviewRequest,
  ForgetPreviewResponse,
  Page,
  SnapshotFileSearchResponse,
  SnapshotFilesResponse,
  SnapshotOut,
  TagSnapshotsRequest,
  TagSnapshotsResponse,
} from './types'

/** tag 为空串/undefined = 不过滤（R-026-10：按任务标签筛选；**包含**语义——快照 tags 数组含该 tag 即命中，与 E17 服务端口径一致）。
 * host 为空串/undefined = 不过滤（R-023 多主机视图：按快照主机标识过滤，与当前 X-Repo-Id 仓库叠加生效）。 */
export function listSnapshots(
  limit = 50,
  offset = 0,
  tag?: string,
  host?: string,
  /** 只看某个指定仓库（含**归档仓库**）：覆盖全局 X-Repo-Id 头（删除保护方案 D13）。 */
  repoId?: string,
): Promise<Page<SnapshotOut>> {
  return request<Page<SnapshotOut>>('/api/snapshots', {
    query: { limit, offset, tag, host },
    headers: repoId ? { 'X-Repo-Id': repoId } : undefined,
  })
}

/** P3 快照内文件搜索（R6）：服务端对展示路径做子串匹配 + 游标分页，30s 缓存。
 *  纪律：**前端绝不拉全量再过滤**（V-3）；关键词非空由调用方保证（后端纯空白 422，
 *  空关键词等价于全量拉取，正是本端点要禁止的用法）。 */
export function searchSnapshotFiles(
  snapshotId: string,
  query: string,
  cursor?: string | null,
  limit = 200,
): Promise<SnapshotFileSearchResponse> {
  return request<SnapshotFileSearchResponse>(
    `/api/snapshots/${encodeURIComponent(snapshotId)}/files/search`,
    { method: 'POST', body: { query, cursor: passCursor(cursor), limit } },
  )
}

/** path 为展示形态（如 /etc），服务端负责还原为快照内路径；cursor 原样透传。 */
export function listSnapshotFiles(
  snapshotId: string,
  path?: string,
  cursor?: string | null,
  limit = 200,
  /** 同 :func:`listSnapshots`：看归档仓库时必须带上它的 id。 */
  repoId?: string,
): Promise<SnapshotFilesResponse> {
  return request<SnapshotFilesResponse>(`/api/snapshots/${encodeURIComponent(snapshotId)}/files`, {
    query: { path, cursor: passCursor(cursor), limit },
    headers: repoId ? { 'X-Repo-Id': repoId } : undefined,
  })
}

/** E21 forget 预览：返回将被裁剪的快照列表（只读，不落任何裁剪）。至少一个 keep_* 必填。 */
export function previewForget(payload: ForgetPreviewRequest): Promise<ForgetPreviewResponse> {
  return request<ForgetPreviewResponse>('/api/forget/preview', { method: 'POST', body: payload })
}

/** A3 快照打/去标签：restic tag --add/--remove（独占锁修改快照）。返回改动条数 + 新旧 id 映射。 */
export function tagSnapshots(payload: TagSnapshotsRequest): Promise<TagSnapshotsResponse> {
  return request<TagSnapshotsResponse>('/api/snapshots/tag', { method: 'POST', body: payload })
}
