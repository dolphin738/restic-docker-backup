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

/** E12 触发任务响应（202）。 */
export interface JobCreateResponse {
  id: string
  status: JobStatus
  type: JobType
}

/** E27 任务日志尾部。 */
export interface JobLogResponse {
  id: string
  log_path: string
  tail_bytes: number
  content: string
  truncated: boolean
}

/** E29 仓库连通性测试（失败不静默：ok=false + reason）。 */
export interface RepoTestResponse {
  ok: boolean
  latency_ms: number
  reason: string | null
}

/** E06 配置项：敏感项只回 has_value + source，不回明文。 */
export interface ConfigItem {
  key: string
  value: string | null
  has_value: boolean
  source: 'ui' | 'env'
  sensitive: boolean
}

/** E06 配置读。 */
export interface ConfigResponse {
  items: ConfigItem[]
  passfile_status: string
}

/** E05 目录浏览条目（display_path 为原路径语义）。 */
export interface SourceItem {
  name: string
  type: string
  display_path: string
}

/** E05 目录浏览响应（游标分页）。 */
export interface SourceBrowseResponse {
  path: string
  items: SourceItem[]
  next_cursor: string | null
}

/** E32 内置排除集（只读；items/core 为容器前缀形态，展示层按模式剥前缀，不存不拼）。 */
export interface ExclusionsResponse {
  mode: RunMode
  items: string[]
  core: string[]
  total: number
}

/** E31 dry-run 预览条目。 */
export interface DryRunItem {
  path: string
  display_path: string
  size: number | null
}

/** E31 黄色警告（伪文件系统目录名，不拦截）。 */
export interface DryRunWarning {
  type: string
  path: string
}

/** E31 dry-run 响应。 */
export interface DryRunResponse {
  items: DryRunItem[]
  warnings: DryRunWarning[]
  truncated: boolean
  error: string | null
}

/** E08 策略输出。 */
export interface PolicyOut {
  id: number
  name: string
  is_default: boolean
  enabled: boolean
  tag: string | null
  sources: string[]
  excludes: string[]
  keep_last: number
  keep_hourly: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
  group_by: string
  cron: string | null
  one_file_system: boolean
  compression: string
  check_sample_ratio: number
  monthly_full_day: number
  prune_interval_days: number
  limit_up: number
  limit_down: number
  last_run_at: string | null
  created_at: string
  updated_at: string
}

/** E09/E10 策略写请求体（is_default 由服务端控制，不接受客户端指定）。 */
export interface PolicyUpsertRequest {
  name: string
  enabled: boolean
  tag: string | null
  sources: string[]
  excludes: string[]
  keep_last: number
  keep_hourly: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
  group_by: string
  cron: string | null
  one_file_system: boolean | null
  compression: string
  check_sample_ratio: number
  monthly_full_day: number
  prune_interval_days: number
  limit_up: number
  limit_down: number
}

/** E17 快照条目（paths 为原路径语义；P1 size 恒为 null）。 */
export interface SnapshotOut {
  id: string
  time: string | null
  host: string | null
  tags: string[]
  paths: string[]
  size: number | null
}

/** E18 文件树条目（display_path 为原路径语义）。 */
export interface SnapshotFileOut {
  name: string
  type: string
  path: string
  display_path: string
  size: number | null
  mtime: string | null
}

/** E18 文件树响应（游标分页）。 */
export interface SnapshotFilesResponse {
  snapshot_id: string
  path: string
  items: SnapshotFileOut[]
  next_cursor: string | null
  truncated: boolean
}

/** E19 恢复响应（202）：restore_target 由服务端计算的时间戳子目录，前端不自己拼。 */
export interface RestoreResponse {
  job_id: string
  restore_target: string
}
