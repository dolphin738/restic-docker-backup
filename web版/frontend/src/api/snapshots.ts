/** E17 快照列表（limit/offset 分页，可选 tag 精确过滤）+ E18 快照文件树（不透明游标，只透传）。 */

import { passCursor, request } from './http'
import type { Page, SnapshotFilesResponse, SnapshotOut } from './types'

/** tag 为空串/undefined = 不过滤（R-026-10：按任务标签筛选；**包含**语义——快照 tags 数组含该 tag 即命中，与 E17 服务端口径一致）。 */
export function listSnapshots(limit = 50, offset = 0, tag?: string): Promise<Page<SnapshotOut>> {
  return request<Page<SnapshotOut>>('/api/snapshots', { query: { limit, offset, tag } })
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
