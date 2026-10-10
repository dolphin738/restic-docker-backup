/** 任务端点（E13 进行中列表 / E14 详情）。SSE 不走 fetch，见 composables/useJobStream.ts。 */

import { request } from './http'
import type { JobOut, Page } from './types'

/** P1 最小版：后端只开放 running/pending 过滤，逗号分隔。 */
export function listActiveJobs(limit = 20, offset = 0): Promise<Page<JobOut>> {
  return request<Page<JobOut>>('/api/jobs', {
    query: { status: 'running,pending', limit, offset },
  })
}

export function getJob(jobId: string): Promise<JobOut> {
  return request<JobOut>(`/api/jobs/${encodeURIComponent(jobId)}`)
}
