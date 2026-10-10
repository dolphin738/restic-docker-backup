/** E19 恢复到时间戳子目录：请求体只有 snapshot_id 与 target。 */

import { request } from './http'
import type { RestoreResponse } from './types'

/** P1 target 恒为恢复白名单根 `/restore`；时间戳子目录由服务端计算并随 restore_target 返回，
 * 前端**不要**自己拼时间戳或子目录名。 */
export const RESTORE_TARGET_ROOT = '/restore'

export function restoreSnapshot(snapshotId: string, target: string): Promise<RestoreResponse> {
  return request<RestoreResponse>('/api/restore', {
    method: 'POST',
    body: { snapshot_id: snapshotId, target },
  })
}
