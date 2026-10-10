/** 任务端点：E12 触发 / E13 列表 / E14 详情 / E16 取消 / E27 日志尾部。
 * SSE 不走 fetch，见 composables/useJobStream.ts。
 */

import { request } from './http'
import type { JobCreateResponse, JobLogResponse, JobOut, JobType, OkResponse, Page } from './types'

/** P1 最小版：后端只开放 running/pending 过滤，逗号分隔。 */
export function listActiveJobs(limit = 20, offset = 0): Promise<Page<JobOut>> {
  return request<Page<JobOut>>('/api/jobs', {
    query: { status: 'running,pending', limit, offset },
  })
}

export function getJob(jobId: string): Promise<JobOut> {
  return request<JobOut>(`/api/jobs/${encodeURIComponent(jobId)}`)
}

/** 触发任务（manual）；同仓库已有 running/pending → 409 JOB_CONFLICT，不排队。 */
export function createJob(
  type: JobType,
  options: { policyId?: number; snapshotId?: string; params?: Record<string, unknown> } = {},
): Promise<JobCreateResponse> {
  return request<JobCreateResponse>('/api/jobs', {
    method: 'POST',
    body: {
      type,
      ...(options.policyId === undefined ? {} : { policy_id: options.policyId }),
      ...(options.snapshotId === undefined ? {} : { snapshot_id: options.snapshotId }),
      ...(options.params === undefined ? {} : { params: options.params }),
    },
  })
}

/** 非 running/pending → 409 NOT_CANCELLABLE。 */
export function cancelJob(jobId: string): Promise<OkResponse> {
  return request<OkResponse>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
}

/** 日志尾部：不传 tail_bytes 取服务端默认 64 KiB；响应 truncated + tail_bytes（实际字节）
 * 用于"加载更多"逐级放大（上限 1 MiB，超出 422）。 */
export function getJobLog(jobId: string, tailBytes?: number): Promise<JobLogResponse> {
  return request<JobLogResponse>(`/api/jobs/${encodeURIComponent(jobId)}/log`, {
    query: { tail_bytes: tailBytes },
  })
}
