/** E05 目录浏览 + E31 dry-run 预览 + E32 内置排除集。
 *
 * 路径语义：全部收发**展示形态**（原路径 `/etc`），与 E18 的 `?path=` 约定一致；
 * 容器前缀（/host|/sources）由服务端 PathMapper 处理，前端不存不拼。
 */

import { passCursor, request } from './http'
import type { DryRunResponse, ExclusionsResponse, RunMode, SourceBrowseResponse } from './types'

export function browseSources(
  path?: string,
  cursor?: string | null,
  limit = 200,
): Promise<SourceBrowseResponse> {
  return request<SourceBrowseResponse>('/api/sources', {
    query: { path, cursor: passCursor(cursor), limit },
  })
}

/** mode 缺省取服务端当前运行模式。 */
export function getExclusions(mode?: RunMode): Promise<ExclusionsResponse> {
  return request<ExclusionsResponse>('/api/exclusions', { query: { mode } })
}

/** 预览扫描范围；命中伪文件系统目录名回 warnings（黄色，不拦截）。 */
export function dryRunSources(sources: string[], excludes: string[]): Promise<DryRunResponse> {
  return request<DryRunResponse>('/api/sources/dry-run', {
    method: 'POST',
    body: { sources, excludes },
  })
}
