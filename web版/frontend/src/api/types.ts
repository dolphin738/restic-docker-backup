/** 与后端 app/schemas/api_schemas.py 一一对应的响应类型（§5.0 / §5.1）。 */

export type RunMode = 'host' | 'sources'
export type HostMountMode = 'ro' | 'rw'
export type JobStatus = 'pending' | 'running' | 'success' | 'failed' | 'interrupted' | 'cancelled'
export type JobType = 'backup' | 'restore' | 'forget' | 'check' | 'unlock' | 'init' | 'dump'

/** 统一错误响应体（所有非 2xx）。 */
export interface ErrorBody {
  code: string
  message: string
  data: Record<string, unknown> | null
}

/** 通用成功响应体（登出 / 改密）。 */
export interface OkResponse {
  code: string
}

/** 列表分页壳：?limit=&offset= → items/total/limit/offset（禁止前端自拼游标）。 */
export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

/** E04 健康检查。 */
export interface HealthResponse {
  status: string
  db: string
}

/** E03 会话信息。 */
export interface MeResponse {
  authenticated: boolean
  must_change_password: boolean
  mode: RunMode
  host_mount: HostMountMode
  user: string
}

/** 首启一次性随机密码。 */
export interface BootstrapResponse {
  password: string
  must_change_password: boolean
  warning: string
}

/** E14 任务详情。 */
export interface JobOut {
  id: string
  type: JobType
  status: JobStatus
  trigger: string
  policy_id: number | null
  snapshot_id: string | null
  bytes_done: number | null
  files_done: number | null
  skipped_count: number
  last_event_id: number
  log_path: string
  summary: string | null
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  created_at: string
}

/** E30 仓库状态（服务端 30s 缓存）。 */
export interface RepoCapacity {
  size: number | null
  snaps: number | null
}

export interface RepoStatusResponse {
  connected: boolean
  locked: boolean
  capacity: RepoCapacity
  last_error: string | null
}

/** SSE 事件负载：event: progress */
export interface JobProgressPayload {
  event_id?: number
  percent?: number | null
  bytes_done?: number | null
  total_bytes?: number | null
  files_done?: number | null
  total_files?: number | null
  skipped_delta?: number
}

/** SSE 事件负载：event: log（FileRecord 带 path/name，LogLine 带 text） */
export interface JobLogPayload {
  event_id?: number
  path?: string
  name?: string
  text?: string
}

/** SSE 终态负载：event: done / event: error */
export interface JobDonePayload {
  status: JobStatus
  exit_code: number | null
  snapshot_id: string | null
  skipped_count: number
  bytes_done: number | null
  files_done: number | null
  summary: string | null
}
