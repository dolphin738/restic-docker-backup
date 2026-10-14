/** E19 恢复到时间戳子目录：请求体有 snapshot_id、target 与可选 paths（B1 文件级恢复）。
 * paths 为**展示形态**（如 /etc）；缺省/空数组 = 整快照恢复（兼容既有行为）。
 *
 * R-015 原位恢复（E20 预览 + E19 扩展）同在本文件出口：
 * - target 落 `/restore`（时间戳白名单）→ 传统流，不带任何覆盖语义参数（零回归）；
 * - target 落 rw 档 `host_root` 下的原目录 → 原位流，必须带 `overwrite_mode="always"` 与
 *   `keyword`（快照短 id 前 8 位）。两条路径的判定由页面按所选模式决定，后端再兜底校验。
 */

import { request } from './http'
import type { RestorePreviewResponse, RestoreResponse } from './types'

/** P1 target 恒为恢复白名单根 `/restore`；时间戳子目录由服务端计算并随 restore_target 返回，
 * 前端**不要**自己拼时间戳或子目录名。 */
export const RESTORE_TARGET_ROOT = '/restore'

/** 原位恢复的目标根（rw 档 `host_root`）：与 RESTORE_TARGET_ROOT 同为容器形态白名单根，
 * 前端只在其后拼接快照 paths 的展示形态路径（如 /host + /etc/nginx）。 */
export const INPLACE_TARGET_ROOT = '/host'

/** E20 覆盖预览的可选分页参数；cursor 为服务端下发的不透明游标（首页 null）。 */
export interface RestorePreviewOptions {
  limit?: number
  cursor?: string | null
}

/** R-015 原位流的两个可选字段；两者只在原位路径下传，时间戳路径下传会被后端 422 拒绝。 */
export interface RestoreInPlaceOptions {
  /** 固定 'always'（Q3 裁决：界面无模式选择项，比对职责在预览层）。 */
  overwrite_mode?: 'always'
  /** 确认关键词 = 快照短 id 前 8 位（Q2 裁决），后端校验。 */
  keyword?: string
}

/** E20 覆盖预览（只读）：快照内容 × 目标磁盘现状的比对清单，游标分页。 */
export function previewRestore(
  snapshotId: string,
  target: string,
  options: RestorePreviewOptions = {},
): Promise<RestorePreviewResponse> {
  return request<RestorePreviewResponse>('/api/restore/preview', {
    method: 'POST',
    body: {
      snapshot_id: snapshotId,
      target,
      ...(options.limit === undefined ? {} : { limit: options.limit }),
      // 首页传 null，续拉传上一页回传的 next_cursor（原样透传，不解析）
      cursor: options.cursor ?? null,
    },
  })
}

export function restoreSnapshot(
  snapshotId: string,
  target: string,
  paths?: string[],
  options: RestoreInPlaceOptions = {},
): Promise<RestoreResponse> {
  return request<RestoreResponse>('/api/restore', {
    method: 'POST',
    body: {
      snapshot_id: snapshotId,
      target,
      // 全不选 = 整快照：空数组不下传，走服务端缺省分支
      ...(paths && paths.length > 0 ? { paths } : {}),
      ...(options.overwrite_mode === undefined ? {} : { overwrite_mode: options.overwrite_mode }),
      ...(options.keyword === undefined ? {} : { keyword: options.keyword }),
    },
  })
}
