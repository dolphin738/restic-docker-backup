/** E08–E11 策略。P1 仅默认策略可写：
 * - POST 已有默认策略 → 409 POLICY_WRITE_CLOSED；
 * - PUT 非默认策略 → 409 POLICY_WRITE_CLOSED；
 * - DELETE 默认策略 → 409 DEFAULT_POLICY_IMMUTABLE，非默认 → 409 POLICY_WRITE_CLOSED。
 */

import { request } from './http'
import type { OkResponse, Page, PolicyOut, PolicyUpsertRequest } from './types'

export function listPolicies(limit = 50, offset = 0): Promise<Page<PolicyOut>> {
  return request<Page<PolicyOut>>('/api/policies', { query: { limit, offset } })
}

export function createPolicy(payload: PolicyUpsertRequest): Promise<PolicyOut> {
  return request<PolicyOut>('/api/policies', { method: 'POST', body: payload })
}

export function updatePolicy(policyId: number, payload: PolicyUpsertRequest): Promise<PolicyOut> {
  return request<PolicyOut>(`/api/policies/${policyId}`, { method: 'PUT', body: payload })
}

/** P1 两条删除分支均返回 409，成功路径不可达；保留调用位保持契约完整。 */
export function deletePolicy(policyId: number): Promise<OkResponse> {
  return request<OkResponse>(`/api/policies/${policyId}`, { method: 'DELETE' })
}
