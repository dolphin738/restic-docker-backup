/** E33 容器文件系统浏览（仓库配置页「浏览路径」）。
 *
 * 路径语义（与 E05 不同）：本端点回**容器绝对路径**（无展示形态换算），
 * 因为 repo.url 本身就是容器形态（/repo、用户自挂的 /mnt/...），PathMapper 的
 * 模式根换算（/sources）在这里不适用。
 */

import { passCursor, request } from './http'
import type { FsBrowseResponse, FsMkdirResponse } from './types'

export function browseFs(
  path = '/',
  cursor?: string | null,
  limit = 200,
): Promise<FsBrowseResponse> {
  return request<FsBrowseResponse>('/api/fs/browse', {
    query: { path, cursor: passCursor(cursor), limit },
  })
}

/** E33 创建目录（容器视角单级）：父目录必须已存在，目录已存在则幂等返回 created=false。
 *  失败（相对路径 / 系统目录 / 父目录缺失 / 同名文件）统一由 http 层转成 AppError 提示。 */
export function mkdirFs(path: string): Promise<FsMkdirResponse> {
  return request<FsMkdirResponse>('/api/fs/mkdir', { method: 'POST', body: { path } })
}
