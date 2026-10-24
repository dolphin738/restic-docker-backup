import { request } from '@/api/http'
import type { PrunePreviewResponse } from '@/api/types'

/** R-016 独立回收预览：restic prune --dry-run 只读探测，回答「仓库里有没有需要 prune 的数据」。 */
export function previewPrune(): Promise<PrunePreviewResponse> {
  return request<PrunePreviewResponse>('/api/prune/preview', { method: 'POST' })
}
