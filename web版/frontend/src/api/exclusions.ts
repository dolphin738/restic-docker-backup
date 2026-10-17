/** E32 内置排除集（只读）：备份源页退役后迁至此处，唯一消费方为任务向导步骤 2。
 * 契约不变（GET /api/exclusions）；display_items/display_core 为剥好前缀的展示形态。
 * A0 模式收敛后服务端只有一种运行形态，无 mode 入参。
 */

import { request } from './http'
import type { ExclusionsResponse } from './types'

export function getExclusions(): Promise<ExclusionsResponse> {
  return request<ExclusionsResponse>('/api/exclusions')
}
