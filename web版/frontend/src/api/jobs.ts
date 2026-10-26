/** 任务端点：E12 触发 / E13 列表（P1 活跃 + P2 全量历史）/ E14 详情 / E16 取消 / E27 日志尾部。
 * SSE 不走 fetch，见 composables/useJobStream.ts。
 */

import { request } from './http'
import type {
  CheckDetailResponse,
  JobCleanupResponse,
  JobCreateResponse,
  JobLogResponse,
  JobOut,
  JobStatus,
  JobType,
  OkResponse,
  Page,
} from './types'

/** P1 最小版：后端只开放 running/pending 过滤，逗号分隔。
 * 注意：E13 P2 完整版（listJobsHistory）缺省=不过滤，两者语义不同，勿混用。 */
export function listActiveJobs(limit = 20, offset = 0): Promise<Page<JobOut>> {
  return request<Page<JobOut>>('/api/jobs', {
    query: { status: 'running,pending', limit, offset },
  })
}

/** E13 完整版查询参数（P2 批次 A）：缺省语义=不过滤（后端 _parse_statuses 缺省返回 None）。 */
export interface JobHistoryQuery {
  /** 任务类型（JOB_TYPES 白名单单值）；undefined = 不过滤。 */
  type?: JobType
  /** 状态白名单（全状态含终态），逗号拼接为 status 参数；空数组 = 不过滤。 */
  statuses?: JobStatus[]
  /** 按备份任务筛选；undefined = 不过滤。 */
  taskId?: number
  /** started_at 下界（ISO 8601，含时区；服务端归一 UTC 比较）。 */
  startedFrom?: string
  /** started_at 上界（ISO 8601）。 */
  startedTo?: string
  /** 单页条数，上限 500（后端 HISTORY_QUERY_MAX_LIMIT）。 */
  limit?: number
  offset?: number
}

/** E13 完整版：全状态/类型/任务/时间范围筛选，历史视图专用。
 * 与 listActiveJobs（显式 status=running,pending）分开使用，避免缺省语义混淆。 */
export function listJobsHistory(params: JobHistoryQuery = {}): Promise<Page<JobOut>> {
  return request<Page<JobOut>>('/api/jobs', {
    query: {
      type: params.type,
      status: params.statuses && params.statuses.length > 0 ? params.statuses.join(',') : undefined,
      task_id: params.taskId,
      started_from: params.startedFrom,
      started_to: params.startedTo,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
}

export function getJob(jobId: string): Promise<JobOut> {
  return request<JobOut>(`/api/jobs/${encodeURIComponent(jobId)}`)
}

/** E22 check 任务详情（批次 B，R-017）：= E14 + check_view（errors 列表 + summary 指标，
 *  V4 定稿无进度集）；job 非 check 类型 → 404/422。silent：详情抽屉内联展示错误，避免双重提示。 */
export function getCheckDetail(jobId: string): Promise<CheckDetailResponse> {
  return request<CheckDetailResponse>(`/api/checks/${encodeURIComponent(jobId)}`, { silent: true })
}

/** 触发任务（manual）；同仓库已有 running/pending → 409 JOB_CONFLICT，不排队，
 * 409 data 带 running_task_name（US-026-04）。
 * Q1 选删（2026-10-15）：E12 请求契约已无 policy_id——后端 `_Base` extra=forbid，
 * 传 policy_id 即 422 VALIDATION_ERROR（test_e12_policy_id_entry_closed_422 锁死
 * 入口永不重开），故此处不提供该选项。 */
export function createJob(
  type: JobType,
  options: { taskId?: number; snapshotId?: string; params?: Record<string, unknown> } = {},
): Promise<JobCreateResponse> {
  return request<JobCreateResponse>('/api/jobs', {
    method: 'POST',
    body: {
      type,
      ...(options.taskId === undefined ? {} : { task_id: options.taskId }),
      ...(options.snapshotId === undefined ? {} : { snapshot_id: options.snapshotId }),
      ...(options.params === undefined ? {} : { params: options.params }),
    },
  })
}

/** 非 running/pending → 409 NOT_CANCELLABLE。 */
/** E16 取消：默认 SIGTERM → 30s 宽限 → SIGKILL。
 *
 * `force=true` = P7 D2 的 **Abort 二段**（Duplicati isStopping→abort）：跳过宽限直接 SIGKILL，
 * 服务端审计记 `job.abort`。终态仍是 cancelled，不新增状态。
 */
export function cancelJob(jobId: string, force = false): Promise<OkResponse> {
  return request<OkResponse>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    query: force ? { force: 'true' } : undefined,
  })
}

/** 日志尾部：不传 tail_bytes 取服务端默认 64 KiB；响应 truncated + tail_bytes（实际字节）
 * 用于"加载更多"逐级放大（上限 1 MiB，超出 422）。 */
export function getJobLog(jobId: string, tailBytes?: number): Promise<JobLogResponse> {
  return request<JobLogResponse>(`/api/jobs/${encodeURIComponent(jobId)}/log`, {
    query: { tail_bytes: tailBytes },
  })
}

/** 任务历史立即清理（裁决5：仅清 jobs 终态行与任务日志，不触碰审计日志）。
 * silent：错误（如 jobs_days<=0 由后端拒绝/零删除）由页面层内联展示，避免双重提示。 */
export function cleanupJobs(): Promise<JobCleanupResponse> {
  return request<JobCleanupResponse>('/api/jobs/cleanup', { method: 'POST', silent: true })
}
