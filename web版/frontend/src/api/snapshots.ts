/** E17 快照列表（limit/offset 分页）+ E18 快照文件树（不透明游标，只透传）。 */

import { passCursor, request } from './http'
import type { Page, SnapshotFilesResponse, SnapshotOut } from './types'

export function listSnapshots(limit = 50, offset = 0): Promise<Page<SnapshotOut>> {
  return request<Page<SnapshotOut>>('/api/snapshots', { query: { limit, offset } })
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
