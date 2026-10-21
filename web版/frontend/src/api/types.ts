/** 与后端 app/schemas/api_schemas.py 一一对应的响应类型（§5.0 / §5.1）。 */

export type JobStatus = 'pending' | 'running' | 'success' | 'failed' | 'interrupted' | 'cancelled'
export type JobType = 'backup' | 'restore' | 'forget' | 'check' | 'unlock' | 'init' | 'dump' | 'repair' | 'recover'

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
  /** R-026：该 job 由哪个备份任务产生（legacy job 为 null；删除任务后置 null）。 */
  task_id: number | null
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

/** R-023 设置仓库密码请求（password 为空 = 后端生成强随机密码）。 */
export interface RepoPasswordRequest {
  /** 空/null → 后端生成；非空 → 按此值写入（响应不回显）。 */
  password?: string | null
  /** 密码文件已存在时也覆盖——后端仅在仓库当前打不开（密码错误等）时接受。 */
  force?: boolean
}

/** R-023 设置仓库密码结果（password 仅 generated 时一次性回显）。 */
export interface RepoPasswordResponse {
  ok: boolean
  /** 本次是否为后端生成（决定 password 是否回显）。 */
  generated: boolean
  /** 仅 generated=true 时非空：**一次性展示**，不落库、不落日志。 */
  password: string | null
  /** 容器内路径（供运维在宿主机挂载点定位该文件）。 */
  path: string
}

export interface RepoStatusResponse {
  /** repo.url 是否已配置（L3：未配置与连接失败分色——warning / error）。 */
  configured: boolean
  connected: boolean
  locked: boolean
  capacity: RepoCapacity
  last_error: string | null
  /** 结构化失败分类（locked/password/not_initialized/credentials/network/timeout/unknown）；
   *  connected=true 时为 null——行内「初始化 / 重设密码」按钮判定的唯一依据。 */
  failure_kind: string | null
}

/** R-023 仓库输出（repos 表事实源；type 派生不落库，passfile_status 只读派生）。 */
export interface RepoOut {
  id: string
  name: string
  type: string
  url: string | null
  host_tag: string | null
  url_source: string
  host_tag_source: string
  passfile_status: string
  created_at: string
}

/** R-023 新建仓库请求（url/host_tag 可空，日后再补）。 */
export interface RepoCreateRequest {
  /** A8：可选；留空（null/空串）时服务端生成 r<YYMMDD>-xxxxxx。 */
  id?: string | null
  name: string
  url?: string | null
  host_tag?: string | null
}

/** A8 服务端生成的候选仓库标识（r<YYMMDD>-<6 位随机>），供新建向导预填。 */
export interface RepoIdSuggestion {
  id: string
}

/** R-023 更新仓库请求（三态：null=不变 / ""=清除 / 非空=设置）。 */
export interface RepoUpdateRequest {
  name?: string | null
  url?: string | null
  host_tag?: string | null
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
  /** 结构化失败分类（同 RepoStatusResponse.failure_kind）；成功时为 null。 */
  failure_kind: string | null
}

/** A1 rclone 只读探测（Q17：不生成、不编辑、不展示 conf 内凭据）。 */
export interface RcloneRemotesOut {
  /** rclone 二进制可用且探测成功（与 conf 是否存在无关）。 */
  available: boolean
  /** conf 文件在容器内真实存在。 */
  conf_present: boolean
  /** 生效的 conf 路径（RCLONE_CONFIG 设置值；未设置为 null）。 */
  conf_path: string | null
  /** 远端名清单（已去行尾冒号）。 */
  remotes: string[]
  /** 不可用/缺 conf 时的可操作原因。 */
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

/** E33 文件系统条目：path 为容器绝对路径，is_mountpoint 标注其为挂载点。 */
export interface FsItem {
  name: string
  type: string
  path: string
  is_mountpoint: boolean
}

/** E33 浏览响应；mounts 为容器挂载点列表（根目录请求才有，供快跳使用）。 */
export interface FsBrowseResponse {
  path: string
  items: FsItem[]
  mounts: string[]
  next_cursor: string | null
}

/** E32 内置排除集（只读）。items/core 为容器前缀形态（wire 兼容保留）；
 * display_items/display_core 是剥好前缀的展示形态，前端只用这俩。 */
export interface ExclusionsResponse {
  items: string[]
  core: string[]
  display_items: string[]
  display_core: string[]
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

// ---------------------------------------------------------------- 多备份任务（R-026，增量设计 §2）

/** E34 卡片状态（派生，无状态机列）：paused > 活跃 job > 最近 backup 终态 > never_run。 */
export type TaskCardStatus =
  | 'paused'
  | 'running'
  | 'pending'
  | 'success'
  | 'failed'
  | 'interrupted'
  | 'cancelled'
  | 'never_run'

/** E34 任务卡片输出（仪表盘列表）。 */
export interface TaskCard {
  id: number
  name: string
  enabled: boolean
  /** 自动生成只读：task-<id>。 */
  tag: string
  cron: string | null
  status: TaskCardStatus
  last_run_at: string | null
  /** 最近一次 backup job 终态（success/failed/interrupted/cancelled）。 */
  last_result: string | null
  /** null = 仅手动或已暂停。 */
  next_run_at: string | null
  /** 有活跃 job 时非空（卡片内联进度/取消/日志入口）。 */
  running_job_id: string | null
  /** true 时 UI 拦截 Start，引导去 dry-run 预览（R-026-08）。 */
  dry_run_pending: boolean
  /** 保留清理到期提示（只提示，不自动执行）。 */
  prune_due: boolean
  updated_at: string
}

/** E34b 任务详情/编辑预填（sources/excludes 为容器形态，display_* 为展示形态，前端只用 display_*）。 */
export interface BackupTask {
  id: number
  repo_id: string
  name: string
  enabled: boolean
  tag: string
  sources: string[]
  display_sources: string[]
  excludes: string[]
  display_excludes: string[]
  keep_last: number
  keep_hourly: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
  /** A2 时间线保留（restic duration，如 1y5m7d2h；空串 = 未启用）。 */
  keep_within: string
  keep_within_hourly: string
  keep_within_daily: string
  keep_within_weekly: string
  keep_within_monthly: string
  keep_within_yearly: string
  /** A2 --keep-tag 清单（命中任一的快照保留；空数组 = 未启用）。 */
  keep_tags: string[]
  group_by: string
  cron: string | null
  one_file_system: boolean
  compression: string
  check_sample_ratio: number
  monthly_full_day: number
  prune_interval_days: number
  limit_up: number
  limit_down: number
  /** C3 调度触发失败后的最大重试次数（0 = 不重试，上限 5）。 */
  retry_count: number
  /** C4 多目的地：备份成功后镜像到的仓库 id（空数组 = 单目的地）。 */
  mirror_repo_ids: string[]
  dry_run_pending: boolean
  last_run_at: string | null
  created_at: string
  updated_at: string
}

/** E35/E35b 任务写请求体（全量语义，无 tag 字段——服务端生成）。 */
export interface TaskUpsertRequest {
  name: string
  enabled: boolean
  /** 展示形态（如 /etc）；服务端归一为容器形态入库。 */
  sources: string[]
  excludes: string[]
  keep_last: number
  keep_hourly: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  keep_yearly: number
  /** A2 时间线保留（restic duration，如 1y5m7d2h；空串 = 未启用）。 */
  keep_within: string
  keep_within_hourly: string
  keep_within_daily: string
  keep_within_weekly: string
  keep_within_monthly: string
  keep_within_yearly: string
  /** A2 --keep-tag 清单（命中任一的快照保留；空数组 = 未启用）。 */
  keep_tags: string[]
  group_by: string
  /** 空串按缺省传 null（仅手动）。 */
  cron: string | null
  one_file_system: boolean | null
  compression: string
  check_sample_ratio: number
  monthly_full_day: number
  prune_interval_days: number
  limit_up: number
  limit_down: number
  /** C3 调度触发失败后的最大重试次数（0 = 不重试，上限 5）。 */
  retry_count?: number
  /** C4 多目的地：备份成功后镜像到的仓库 id（空数组 = 单目的地）。 */
  mirror_repo_ids?: string[]
}

/** E36 任务域 dry-run 请求（均可选，缺省取任务存量值）。 */
export interface TaskDryRunRequest {
  sources?: string[]
  excludes?: string[]
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

/** E21 forget 预览中的单条快照（paths 为原路径语义，与 E17 口径一致）。 */
export interface ForgetPreviewSnapshot {
  id: string
  time: string | null
  host: string | null
  tags: string[]
  paths: string[]
}

/** E21 forget 预览响应：remove 为将被裁剪的快照；params 为参数回显（确认提交 E12 原样透传）。 */
export interface ForgetPreviewResponse {
  remove: ForgetPreviewSnapshot[]
  remove_count: number
  keep_count: number
  group_count: number
  params: Record<string, unknown>
  truncated: boolean
}

/** E21 forget 预览请求（keep_* 不设 = 不启用该维度；至少一个必填，服务端 422 兜底）。 */
export interface ForgetPreviewRequest {
  tag?: string
  group_by?: string
  keep_last?: number | null
  keep_hourly?: number | null
  keep_daily?: number | null
  keep_weekly?: number | null
  keep_monthly?: number | null
  keep_yearly?: number | null
  /** A2 时间线保留（restic duration，如 1y5m7d2h；空串/不传 = 未启用）。 */
  keep_within?: string | null
  keep_within_hourly?: string | null
  keep_within_daily?: string | null
  keep_within_weekly?: string | null
  keep_within_monthly?: string | null
  keep_within_yearly?: string | null
  /** A2 --keep-tag 清单（命中任一的快照保留；空数组 = 未启用）。 */
  keep_tags?: string[]
  prune?: boolean
}

/** A3 单条被改标签的快照：旧 id → 新 id（restic tag 改标签会生成新快照对象）。 */
export interface TagChanged {
  old_snapshot_id: string
  new_snapshot_id: string
}

/** A3 打/去标签响应：改动条数 + 逐条新旧 id 映射。 */
export interface TagSnapshotsResponse {
  changed_count: number
  changed: TagChanged[]
  truncated: boolean
}

/** A3 快照打/去标签请求（restic tag --add/--remove）。 */
export interface TagSnapshotsRequest {
  /** 目标快照 ID 列表（与 tag_filter/host 二选一；都空则服务端 422 拒，避免改全部）。 */
  snapshot_ids?: string[]
  /** 要添加的标签（打标签）。 */
  add?: string[]
  /** 要移除的标签（去标签）。 */
  remove?: string[]
  /** 按现有 tag 过滤目标快照（--tag 选择语义，可多选）。 */
  tag_filter?: string[] | null
  /** 按 host 过滤目标快照（--host）。 */
  host?: string | null
}

/** E19 恢复响应（202）：restore_target 由服务端计算的时间戳子目录，前端不自己拼。 */
export interface RestoreResponse {
  job_id: string
  restore_target: string
}

// ---------------------------------------------------------------- 批次 D（R-015 原位恢复）

/** E20 覆盖预览条目：同一路径在快照侧与磁盘侧的 mtime / size 双侧对照。
 *
 * `path` 是容器形态（如 /sources/etc/nginx），`display_path` 才是原路径语义（如 /etc/nginx）——
 * 前端一律只用 `display_path`（S1：容器形态路径不直接面向用户）。
 * 时间串为后端直出的 ISO-8601 原文；`snapshot_size` 为 null 表示快照侧未给出大小。
 */
export interface RestorePreviewItem {
  path: string
  display_path: string
  snapshot_mtime: string | null
  disk_mtime: string | null
  snapshot_size: number | null
  disk_size: number
}

/** E20 覆盖预览响应（游标分页）。
 *
 * `next_cursor` 是服务端下发的不透明游标：前端只透传不自造、不解析（与 E18/E33 同纪律）。
 * `overwrite_count` / `total_bytes` 是**整次预览**的汇总值（非当前页），随每页一起回传。
 * `total_bytes` 语义 = **将写入磁盘的字节**（快照侧 snapshot_size 求和，restic 缺失计 0），
 * 不是被覆盖文件的磁盘侧大小之和；UI 口径「本次原位恢复将写入约 N 字节」。
 */
export interface RestorePreviewResponse {
  overwrite_count: number
  items: RestorePreviewItem[]
  total_bytes: number
  next_cursor: string | null
  truncated: boolean
}

/** E23 通知配置（R-018）：敏感 URL 只回 *_set 布尔，不回明文（与 E06 cred.* 同口径）。 */
export interface NotifyConfigResponse {
  healthchecks_url_set: boolean
  webhook_url_set: boolean
  webhook_template: string
  meow_nickname: string
  /** MeoW 消息显示类型（API §3.2 查询参数 msgType）：text 纯文本 / html 渲染 HTML。 */
  meow_msg_type: 'text' | 'html'
  /** 仅 msgType=html 生效的显示高度（像素，查询参数 htmlHeight）。 */
  meow_html_height: number
  /** 可选：通知跳转链接（API §3.2 Body 字段 url）。 */
  meow_url: string
  /** 可选：通知图标 URL（API §3.2 Body 字段 imgUrl，建议 216×216 PNG）。 */
  meow_img_url: string
  /** MeoW 套餐等级（决定本地限流档位：free/premium/lifetime）。 */
  meow_plan: 'free' | 'premium' | 'lifetime'
  on_success: boolean
  skipped_threshold: number
  webhook_redact: boolean
  meow_redact: boolean
  /** C2 SMTP 邮件（密码只回 mail_password_set）。 */
  mail_host: string
  mail_port: number
  mail_username: string
  mail_password_set: boolean
  mail_from: string
  mail_to: string
  mail_security: 'none' | 'starttls' | 'tls'
  mail_redact: boolean
}

/** E23 写入：URL 字段三态（undefined=不变 / ''=清除 / 非空=设置），其余 undefined=不变。 */
export interface NotifyConfigPatch {
  healthchecks_url?: string
  webhook_url?: string
  webhook_template?: string
  meow_nickname?: string
  meow_msg_type?: 'text' | 'html'
  meow_html_height?: number
  meow_url?: string
  meow_img_url?: string
  meow_plan?: 'free' | 'premium' | 'lifetime'
  on_success?: boolean
  skipped_threshold?: number
  webhook_redact?: boolean
  meow_redact?: boolean
  /** C2 SMTP 邮件；mail_password 三态（undefined=不变 / ''=清除 / 非空=设置）。 */
  mail_host?: string
  mail_port?: number
  mail_username?: string
  mail_password?: string
  mail_from?: string
  mail_to?: string
  mail_security?: 'none' | 'starttls' | 'tls'
  mail_redact?: boolean
}

/** E24 单渠道测试结果（失败必须带原因）。 */
export interface NotifyTestChannel {
  channel: 'healthchecks' | 'webhook' | 'meow' | 'mail'
  ok: boolean
  reason: string | null
}

/** E24 测试消息响应。 */
export interface NotifyTestResponse {
  ok: boolean
  latency_ms: number
  channels: NotifyTestChannel[]
  reason: string | null
}

// ---------------------------------------------------------------- P2 收尾批次 A（R-020 / R-021）

/** 审计结果枚举（与后端 AuditResult 白名单一一对应）。 */
export type AuditResult = 'ok' | 'denied' | 'failed'

/** E26 审计日志行（§4.6 字段全集；detail 为 JSON 文本原文，前端只展示不改写）。 */
export interface AuditOut {
  id: number
  ts: string
  action: string
  target: string | null
  detail: string | null
  result: AuditResult
  created_at: string
}

/** E26 响应：分页壳 + actions 动作名枚举（SELECT DISTINCT，前端筛选下拉数据源）。 */
export interface AuditPageResponse extends Page<AuditOut> {
  actions: string[]
}

/** E25 缓存占用响应（Q14 基线：du 超时回部分值 + truncated，不 5xx）。 */
export interface CacheUsageResponse {
  path: string
  size_bytes: number
  items_count: number
  measured_at: string
  truncated: boolean
}

// ---------------------------------------------------------------- E39 系统时间/时区同步

/** NTP 探测结果（失败必须带 reason 机器码，禁止静默）。 */
export interface NtpProbe {
  available: boolean
  server: string | null
  offset_ms: number | null
  rtt_ms: number | null
  reason: string | null
}

/** E39 服务器时间快照（GET /api/system/time）。 */
export interface SystemTimeResponse {
  /** 服务器 UTC 时刻（ISO 8601，秒级，Z 后缀）。 */
  server_time: string
  /** 生效时区名（config.timezone → settings.tz → UTC）。 */
  timezone: string
  /** 生效时区下的本地时间（ISO 8601 带偏移）。 */
  local_time: string
  /** 本地时间偏移（如 +08:00）。 */
  utc_offset: string
  /** 服务器当前 Unix 毫秒（前端实时时钟基准，避免用本机时钟）。 */
  epoch_ms: number
  /** 本次探测的生效 NTP 链（主备配置或内置默认链）。 */
  ntp_servers: string[]
  ntp: NtpProbe
}

/** E39 同步结论（POST /api/system/time/sync；applied=false 时必须带 reason）。 */
export interface TimeSyncResponse {
  ok: boolean
  applied: boolean
  offset_ms: number | null
  server: string | null
  reason: string | null
}

// ---------------------------------------------------------------- 批次 B（R-017 check 视图）

/** E22 check 错误行（parser 从 `check --json` 逐行 error 提取）。 */
export interface CheckErrorItem {
  message: string
}

/** E22 check 视图（V4 定稿：summary + error 行驱动，无进度集）。
 *
 * 计数语义（后端 T04 实际契约，2026-10-14 对齐）：
 * - `error_count` = 错误行数（截断场景可能少于 summary 计数）——UI 告警口径用它；
 * - `num_errors` = restic summary 原值（与 error_count 独立，仅展示）；
 * - `broken_packs` = 损坏 pack id 的**保序列表**（64hex，修复动作数据源）；
 *   健康仓库为空列表（后端解析层已把 Go nil 的 null 归一，前端不需容错 null）。
 */
export interface CheckView {
  errors: CheckErrorItem[]
  error_count: number
  num_errors: number
  broken_packs: string[]
  suggest_repair_index: boolean
  suggest_prune: boolean
  read_data: boolean
}

/** E22 响应 = E14 JobOut + check 专属解析视图（不建新表，Q9 基线）。 */
export interface CheckDetailResponse extends JobOut {
  check_view: CheckView
}

/** E38 报表单日桶（R-024；无运行日 total=0、ratio=null）。 */
export interface ReportBucket {
  date: string
  total: number
  success: number
  failed: number
  ratio: number | null
}

/** E38 备份成功率报表（按配置时区分桶，零运行日补齐）。 */
export interface BackupSuccessReport {
  days: number
  timezone: string
  from_date: string
  to_date: string
  buckets: ReportBucket[]
  total: number
  success: number
  failed: number
  ratio: number | null
}

/** C4 仓库密钥轮换请求（restic key passwd；新密码只经请求体，不回显）。 */
export interface RepoKeyRotateRequest {
  repo_id: string
  new_password: string
}

/** C4 仓库密钥轮换响应。 */
export interface KeyRotateResponse {
  ok: boolean
  repo_id: string
}

/** C4 手动 copy（搬家场景）请求。 */
export interface ManualCopyRequest {
  source_repo_id: string
  target_repo_id: string
  tags?: string[]
}

/** C4 手动 copy 响应（exit_code -1 = 超时中断，增量可续传）。 */
export interface ManualCopyResponse {
  ok: boolean
  source_repo_id: string
  target_repo_id: string
  exit_code: number
  message: string
}


/** 批次 E 配置导出请求（导出口令至少 10 位，保护加密凭据段）。 */
export interface ConfigExportRequest {
  password: string
}

/** 批次 E 导出包（前端只透传，不解释内容；secrets 为加密凭据段）。 */
export interface ConfigExportPack {
  format: string
  version: number
  created_at: string
  encryption: Record<string, unknown>
  data: Record<string, unknown>
}

/** 批次 E 配置导入请求（replace_existing 由 ConfirmGate 二次确认后置真）。 */
export interface ConfigImportRequest {
  pack: ConfigExportPack
  password: string
  replace_existing: boolean
}

/** 批次 E 导入报告（逐项可展示；reason 为服务端可读中文）。 */
export interface ConfigImportReport {
  repos: {
    created: string[]
    reused: string[]
    updated: string[]
    skipped: { id: string; reason: string }[]
  }
  tasks: {
    created: string[]
    updated: string[]
    skipped: { name: string; reason: string }[]
  }
  mirror_warnings: { mirror_id: string; reason: string }[]
  notify_updated: number
  creds_imported: number
}
