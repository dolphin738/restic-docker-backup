/** E19 恢复到时间戳子目录：请求体有 snapshot_id、target 与可选 paths（B1 文件级恢复）。
 * paths 为**展示形态**（如 /etc）；缺省/空数组 = 整快照恢复（兼容既有行为）。
 */

import { request } from './http'
import type { RestoreResponse } from './types'

/** P1 target 恒为恢复白名单根 `/restore`；时间戳子目录由服务端计算并随 restore_target 返回，
 * 前端**不要**自己拼时间戳或子目录名。 */
export const RESTORE_TARGET_ROOT = '/restore'

export function restoreSnapshot(
  snapshotId: string,
  target: string,
  paths?: string[],
): Promise<RestoreResponse> {
  return request<RestoreResponse>('/api/restore', {
    method: 'POST',
    body: {
      snapshot_id: snapshotId,
      target,
      // 全不选 = 整快照：空数组不下传，走服务端缺省分支
      ...(paths && paths.length > 0 ? { paths } : {}),
    },
  })
}
