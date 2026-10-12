/** E32 内置排除集（只读）：备份源页退役后迁至此处，唯一消费方为任务向导步骤 2。
 * 契约不变（GET /api/exclusions）；display_items/display_core 为剥好前缀的展示形态。
 */

import { request } from './http'
import type { ExclusionsResponse, RunMode } from './types'

/** mode 缺省取服务端当前运行模式。 */
export function getExclusions(mode?: RunMode): Promise<ExclusionsResponse> {
  return request<ExclusionsResponse>('/api/exclusions', { query: { mode } })
}
