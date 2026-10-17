/** E17 快照列表（limit/offset 分页，可选 tag 精确过滤）+ E18 快照文件树（不透明游标，只透传）+
 * E21 forget 预览（R-016 预览-确认流前半，服务端恒 --dry-run 只读）。 */

import { passCursor, request } from './http'
import type {
  ForgetPreviewRequest,
  ForgetPreviewResponse,
  Page,
  SnapshotFilesResponse,
  SnapshotOut,
  TagSnapshotsRequest,
  TagSnapshotsResponse,
} from './types'

/** tag 为空串/undefined = 不过滤（R-026-10：按任务标签筛选；**包含**语义——快照 tags 数组含该 tag 即命中，与 E17 服务端口径一致）。
 * host 为空串/undefined = 不过滤（R-023 多主机视图：按快照主机标识过滤，与当前 X-Repo-Id 仓库叠加生效）。 */
export function listSnapshots(limit = 50, offset = 0, tag?: string, host?: string): Promise<Page<SnapshotOut>> {
  return request<Page<SnapshotOut>>('/api/snapshots', { query: { limit, offset, tag, host } })
}

/** path 为展示形态（如 /etc），服务端负责还原为快照内路径；cursor 原样透传。 */
export function listSnapshotFiles(
  snapshotId: string,
  path?: string,
  cursor?: string | null,
  limit = 200,
): Promise<SnapshotFilesResponse> {
  return request<SnapshotFilesResponse>(`/api/snapshots/${encodeURIComponent(snapshotId)}/files`, {
    query: { path, cursor: passCursor(cursor), limit },
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
