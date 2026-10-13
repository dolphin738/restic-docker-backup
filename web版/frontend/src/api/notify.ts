/** 通知端点（R-018）：E23 配置读写 / E24 测试消息。
 * 敏感 URL 三态语义见 types.ts NotifyConfigPatch——留空（undefined）= 不修改。
 */

import { request } from './http'
import type { NotifyConfigPatch, NotifyConfigResponse, NotifyTestResponse, OkResponse } from './types'

export function getNotifyConfig(): Promise<NotifyConfigResponse> {
  return request<NotifyConfigResponse>('/api/notify')
}

export function updateNotifyConfig(patch: NotifyConfigPatch): Promise<OkResponse> {
  return request<OkResponse>('/api/notify', { method: 'PUT', body: patch })
}

/** 向全部已配置渠道发测试消息；逐渠道结果（失败必须带原因）。 */
export function testNotify(): Promise<NotifyTestResponse> {
  return request<NotifyTestResponse>('/api/notify/test', { method: 'POST' })
}
