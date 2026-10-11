/** E33 容器文件系统浏览（仓库配置页「浏览路径」）。
 *
 * 路径语义（与 E05 不同）：本端点回**容器绝对路径**（无展示形态换算），
 * 因为 repo.url 本身就是容器形态（/repo、用户自挂的 /mnt/...），PathMapper 的
 * 模式根换算（/host|/sources）在这里不适用。
 */

import { passCursor, request } from './http'
import type { FsBrowseResponse } from './types'

export function browseFs(
  path = '/',
  cursor?: string | null,
  limit = 200,
): Promise<FsBrowseResponse> {
  return request<FsBrowseResponse>('/api/fs/browse', {
    query: { path, cursor: passCursor(cursor), limit },
  })
}
