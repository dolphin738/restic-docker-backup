"""全局常量：任务枚举、路径白名单前缀、错误码（§5.0 错误码表）、审计动作。

本模块不依赖任何其他 app 模块，供全项目单向引用，避免循环导入。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 任务与状态（方案 §5 状态机）

JOB_TYPES: tuple[str, ...] = (
    "backup",
    "restore",
    "forget",
    "check",
    "unlock",
    "init",
    "dump",
    "repair",  # B1 自愈：restic repair index / repair packs
    "recover",  # B3 自愈：restic recover（找回未快照数据）
)
JOB_STATUSES: tuple[str, ...] = ("pending", "running", "success", "failed", "interrupted", "cancelled")
JOB_TRIGGERS: tuple[str, ...] = ("manual", "scheduled")

# 状态分组（§4.5 / §5 状态机）：活跃态用于互斥冲突判定，终态用于 SSE 收流与取消校验
ACTIVE_JOB_STATUSES: tuple[str, ...] = ("running", "pending")
TERMINAL_JOB_STATUSES: tuple[str, ...] = ("success", "failed", "interrupted", "cancelled")

# ---------------------------------------------------------------- R-023 仓库标识（repos 表主键）

# repo_id 会被拼进密码文件路径 ``<passfile_root>/<repo_id>/password``：
# 不含 `.` 与 `/`（故 `..` 与子目录写法均不可能），从源头杜绝路径穿越 → 任意文件读写。
# 建仓（POST /api/repos）与路径参数（/api/repos/{repo_id}/*）两侧共用同一规则。
REPO_ID_PATTERN: str = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
REPO_ID_MAX_LENGTH: int = 64

# ---------------------------------------------------------------- 路径白名单（§7.3 PathMapper）

# 可被选为备份源的根前缀（A0 模式收敛：host 模式已删除，只剩 /sources）
SOURCE_ALLOWED_PREFIXES: tuple[str, ...] = ("/sources",)
# 恢复目标白名单（P1 仅"恢复到时间戳子目录"，目标限定在可写挂载点）
RESTORE_ALLOWED_PREFIXES: tuple[str, ...] = ("/restore",)
# 原位恢复目标白名单（R-015 Q1 裁决，A0 收敛后 = 备份源根 ``/sources``）。
# 与 RESTORE_ALLOWED_PREFIXES 分离——避免 /restore 的时间戳语义被覆盖语义污染。
INPLACE_RESTORE_ALLOWED_PREFIXES: tuple[str, ...] = ("/sources",)
# 原位恢复**执行层**的 --target 固定值（S7 v1.3「执行层落点纪律」）：
# restic 的落点是「--target + 快照内绝对路径」的拼接（非替换），而本项目快照存的是
# 容器绝对路径（/sources/etc/...），故只有 "/" 才是真原位（写回 /sources/etc/...）；
# 传 <目标目录> 会嵌套成 /sources/etc/sources/etc/... ——一个目标文件都不会被覆盖。
# S1：该字面量的唯一出处（ResticCmd 只在显式放行分支里引用它）。
INPLACE_RESTORE_TARGET_ROOT = "/"
# E05 目录浏览允许的前缀
BROWSE_ALLOWED_PREFIXES: tuple[str, ...] = ("/sources",)

# ---------------------------------------------------------------- HTTP 方法

WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# ---------------------------------------------------------------- 错误码（§5.0 统一错误响应）

VALIDATION_ERROR = "VALIDATION_ERROR"
UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"
JOB_CONFLICT = "JOB_CONFLICT"
ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
RATE_LIMITED = "RATE_LIMITED"
INTERNAL = "INTERNAL"
RESTIC_FAILED = "RESTIC_FAILED"     # restic 子进程失败（E17/E18/E19/E29 探测类端点）
DUMP_FAILED = "DUMP_FAILED"         # dump 阶段失败（R-019：探活/执行/产物校验，原因明示）

# 端点级扩展码（§5.0 注）
PATH_OUT_OF_PREFIX = "PATH_OUT_OF_PREFIX"          # 400 E05/E19
ENV_OVERRIDDEN = "ENV_OVERRIDDEN"                  # 409 E07
READONLY_KEY = "READONLY_KEY"                          # 409 E07：只读派生键（repo.type）不可直接写
NOT_CANCELLABLE = "NOT_CANCELLABLE"                # 409 E16
RESTORE_FORBIDDEN = "RESTORE_FORBIDDEN"            # 403 E19
BOOTSTRAP_SHOWN = "BOOTSTRAP_SHOWN"                # 410 Bootstrap
PASSWORD_WRONG = "PASSWORD_WRONG"                  # 401 E01
CSRF_ORIGIN = "CSRF_ORIGIN"                        # 403 写方法 Origin 校验
BOOTSTRAP_REQUIRED = "BOOTSTRAP_REQUIRED"          # 403 首启未改密门
TASK_RUNNING = "TASK_RUNNING"                      # 409 E35''：运行中/等待中任务拒绝删除
REPO_NOT_FOUND = "REPO_NOT_FOUND"                  # 404 E34–E36：repo 作用域 id 不存在（P2 仅 default）
CREDENTIAL_DECRYPT_FAILED = "CREDENTIAL_DECRYPT_FAILED"  # 500 R-025：凭据密文无法解密（密码文件被换/数据损坏）

# 错误码 → HTTP 状态码（§5.0 表 + §5.1 端点级扩展码）
ERROR_STATUS: dict[str, int] = {
    VALIDATION_ERROR: 422,
    UNAUTHORIZED: 401,
    FORBIDDEN: 403,
    NOT_FOUND: 404,
    JOB_CONFLICT: 409,
    ACCOUNT_LOCKED: 429,
    RATE_LIMITED: 429,
    INTERNAL: 500,
    RESTIC_FAILED: 500,
    DUMP_FAILED: 500,
    PATH_OUT_OF_PREFIX: 400,
    ENV_OVERRIDDEN: 409,
    READONLY_KEY: 409,
    NOT_CANCELLABLE: 409,
    RESTORE_FORBIDDEN: 403,
    BOOTSTRAP_SHOWN: 410,
    PASSWORD_WRONG: 401,
    CSRF_ORIGIN: 403,
    BOOTSTRAP_REQUIRED: 403,
    TASK_RUNNING: 409,
    REPO_NOT_FOUND: 404,
    CREDENTIAL_DECRYPT_FAILED: 500,
}

# ---------------------------------------------------------------- 审计动作（§4.6 / §7.6）

AUDIT_LOGIN_OK = "login.ok"
AUDIT_LOGIN_FAILED = "login.failed"
AUDIT_LOGOUT = "logout"
AUDIT_PASSWORD_CHANGED = "password.changed"
AUDIT_BOOTSTRAP_SHOWN = "bootstrap.shown"
AUDIT_BOOTSTRAP_REGENERATED = "bootstrap.regenerated"  # 未展示随机密码前重启 → 重新生成（安全敏感动作）
AUDIT_CONFIG_UPDATE = "config.update"
AUDIT_RESTORE_RUN = "restore.run"
# R-015 E20 预览：只读留痕，**不入危险操作清单**（援 forget.preview 先例）
AUDIT_RESTORE_PREVIEW = "restore.preview"
# R-015 原位恢复：危险操作清单 9 项之一（S6 v1.3）
AUDIT_RESTORE_INPLACE = "restore.inplace"
AUDIT_FORGET_PREVIEW = "forget.preview"  # R-016 E21 预览请求（只读，但属敏感动作留痕）
AUDIT_JOB_SKIPPED_CONFLICT = "job.skipped_conflict"
AUDIT_JOB_SUBMIT = "job.submit"
AUDIT_JOB_CANCEL = "job.cancel"
AUDIT_JOB_FINISHED = "job.finished"
AUDIT_JOB_RECOVERED = "job.recovered"
AUDIT_CACHE_CLEAR = "cache.clear"
AUDIT_ENV_SYNC = "config.env_sync"
AUDIT_SOURCES_DRY_RUN = "sources.dry_run"
AUDIT_REPO_TEST = "repo.test"
# R-023 仓库密码文件首次写入（生成或自填）——安全敏感动作，留痕但**不记录明文**
AUDIT_REPO_PASSWORD_SET = "repo.password_set"
AUDIT_NOTIFY_UPDATE = "notify.update"  # R-018 E23 写入通知配置
AUDIT_NOTIFY_TEST = "notify.test"      # R-018 E24 测试消息发送
AUDIT_CONFIG_EXPORT = "config.export"  # 批次 E：配置导出（凭据口令加密，文件出系统边界，危险操作留痕）
AUDIT_CONFIG_IMPORT = "config.import"  # 批次 E：配置导入（改写仓库/任务/通知配置，危险操作留痕）
AUDIT_TASK_CREATE = "task.create"
AUDIT_TASK_UPDATE = "task.update"
AUDIT_TASK_DELETE = "task.delete"
AUDIT_RETENTION_CLEANUP = "retention.cleanup"  # R-020 每日维护：任务历史/日志清理（调度器触发）
AUDIT_DUMP_CLEANUP = "dump.cleanup"  # R-019 每日维护兜底：dump 产物清理（调度器触发）
AUDIT_TAG_UPDATE = "tag.update"  # A3 快照版本标签修改（打/去标签）
AUDIT_REPAIR_RUN = "repair.run"  # B1 自愈：仓库索引/卷修复（restic repair index/packs，危险操作留痕）
AUDIT_RECOVER_RUN = "recover.run"  # B3 自愈：找回未快照数据（restic recover，危险操作留痕）
AUDIT_COPY_RUN = "copy.run"  # C4 多目的地：restic copy 到镜像仓（跨仓库复制，危险操作留痕）
AUDIT_KEY_ROTATE = "key.rotate"  # C4 多目的地：restic key 轮换仓库密码（改密码，危险操作留痕）

# ---------------------------------------------------------------- 鉴权常量（§8）

SESSION_COOKIE_NAME = "rw_session"
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_SAMESITE = "strict"
SESSION_TTL_HOURS = 24

# 登录锁定：5 次失败 → 锁 5min × 2^lock_level，上限 1h
MAX_FAILED_ATTEMPTS = 5
LOCK_BASE_SECONDS = 300
LOCK_MAX_SECONDS = 3600

# 登录限流：同 IP 5 次/分钟（进程内固定窗口）
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 60

# ---------------------------------------------------------------- SSE / 日志（§5.1 E15/E27）

SSE_HEARTBEAT_SECONDS = 15.0       # 空闲心跳间隔（: keep-alive）
SSE_POLL_INTERVAL_SECONDS = 0.3    # 日志文件追加轮询间隔
LOG_TAIL_DEFAULT_BYTES = 65536     # E27 默认尾部字节数
LOG_TAIL_MAX_BYTES = 1048576       # E27 上限 1 MiB

# ---------------------------------------------------------------- SQLite（架构 §7.7 / D2）

DB_BUSY_TIMEOUT_MS = 5000          # 锁等待超时；默认值，实际值走 settings.db_busy_timeout_ms

# ---------------------------------------------------------------- 备份源浏览 / dry-run（E05/E31）

SOURCE_BROWSE_DEFAULT_LIMIT = 200
SOURCE_BROWSE_MAX_LIMIT = 1000
DRY_RUN_TIMEOUT_SECONDS = 120      # dry-run 预览超时（文档未定义，按最简合理取值）
DRY_RUN_MAX_ITEMS = 5000           # 预览条目上限，超出置 truncated
# 伪文件系统目录名（方案 §3.2 首备 dry-run 命名警告，命中出黄色警告但不拦截）
PSEUDO_FS_NAMES: tuple[str, ...] = ("proc", "sys", "dev", "run", "docker", "lost+found")

# ---------------------------------------------------------------- E17/E18 快照浏览

SNAPSHOT_LIST_TIMEOUT_SECONDS = 20      # snapshots --json 探测超时
SNAPSHOT_FILES_TIMEOUT_SECONDS = 30     # ls --json 探测超时
SNAPSHOT_FILES_DEFAULT_LIMIT = 200
SNAPSHOT_FILES_MAX_LIMIT = 1000

# ---------------------------------------------------------------- E21 forget 预览（R-016）

FORGET_PREVIEW_TIMEOUT_SECONDS = 60     # forget --dry-run 扫描快照超时（大仓库比 snapshots 慢）
TAG_OPERATION_TIMEOUT_SECONDS = 60       # A3 restic tag 独占锁修改快照超时（打/去标签）

# ---------------------------------------------------------------- R-018 通知（E23/E24）

NOTIFY_HTTP_TIMEOUT_SECONDS = 10        # 单渠道推送超时（通知绝不阻塞任务链路，超时即放弃）
# C2 SMTP 邮件渠道（Duplicati SendMail 模块等价物）
NOTIFY_MAIL_TIMEOUT_SECONDS = 20        # 单次 SMTP 会话超时（比 HTTP 宽：EHLO/STARTTLS/AUTH 多轮往返）
MAIL_PORT_DEFAULT = 587                 # 提交端口缺省（STARTTLS 提交端口，与 Duplicati 默认一致）
MAIL_SECURITY_MODES: tuple[str, ...] = ("none", "starttls", "tls")  # 加密方式白名单
NOTIFY_SKIPPED_THRESHOLD_DEFAULT = 100  # skipped 告警阈值缺省（Q4 裁决：0=只要有 skipped 即告警）
NOTIFY_SKIPPED_THRESHOLD_MAX = 1_000_000  # 阈值写爆兜底

# ---------------------------------------------------------------- E29/E30 仓库探测

REPO_PROBE_TIMEOUT_SECONDS = 15         # cat config 连通性探测
REPO_STATS_TIMEOUT_SECONDS = 30         # stats --mode raw-data 容量探测
# rclone listremotes 只读探测（A1）：纯本地操作（读 conf），5s 足够；超时即视为不可用
RCLONE_PROBE_TIMEOUT_SECONDS = 5.0
# 仓库状态进程内缓存 TTL：避免仪表盘轮询反复打仓库（E30 成本可控的关键手段）
REPO_STATUS_CACHE_SECONDS = 30.0
# 快照文件树分页游标上限（防止游标被篡改为超大值导致空转读取）
SNAPSHOT_FILES_MAX_OFFSET = 1_000_000

# ---------------------------------------------------------------- P2 收尾批次 A（E13 全量化 / E26 / E25 / retention）

# 任务历史/审计查询分页上限（Q13 基线：E13 对齐 E26 的既有上限，避免两套口径）
HISTORY_QUERY_MAX_LIMIT = 500

# retention（R-020）：jobs 行与任务日志的保留天数缺省（retention.jobs_days 可经 E07 调整）
RETENTION_JOBS_DAYS_DEFAULT = 90

# 缓存占用统计（E25，Q14 基线）：du 超时 + 30s 进程内结果缓存（援 E30 REPO_STATUS_CACHE_SECONDS 先例）
CACHE_USAGE_TIMEOUT_SECONDS = 30.0
CACHE_STATUS_CACHE_SECONDS = 30.0

# ---------------------------------------------------------------- P2 收尾批次 B：R-017 行为开关（behavior.*，E07 可调）

# 键名单一真源（config KV；JobRunner AUTO 分支与调度器 check 触发共用，默认全 off）
BEHAVIOR_AUTO_INIT_KEY = "behavior.auto_init"
BEHAVIOR_AUTO_UNLOCK_KEY = "behavior.auto_unlock"
BEHAVIOR_CHECK_ENABLED_KEY = "behavior.check_enabled"
BEHAVIOR_CHECK_SAMPLE_RATIO_KEY = "behavior.check_sample_ratio"
BEHAVIOR_MONTHLY_FULL_DAY_KEY = "behavior.monthly_full_day"

# 每日抽样 check 的缺省比例（0=不抽样、只月度全量；与 backup_tasks.check_sample_ratio 列缺省一致）
CHECK_SAMPLE_RATIO_DEFAULT = 0.02
# 月度全量 check 的「当月第 N 天」缺省与值域（[1, 28]，规避月末不存在日）
MONTHLY_FULL_DAY_DEFAULT = 1
MONTHLY_FULL_DAY_MIN = 1
MONTHLY_FULL_DAY_MAX = 28

# ---------------------------------------------------------------- P2 收尾批次 B：R-019 dump（dump.* 键与缺省）

DUMP_ENABLED_KEY = "dump.enabled"        # 0/1，缺省 0（off，保持既有语义）
DUMP_TYPE_KEY = "dump.type"              # pg | mysql | sqlite
DUMP_HOST_KEY = "dump.host"
DUMP_PORT_KEY = "dump.port"
DUMP_USER_KEY = "dump.user"
DUMP_DATABASES_KEY = "dump.databases"    # JSON 数组（pg/mysql 为库名、sqlite 为库文件路径）
DUMP_PASSWORD_KEY = "dump.password"      # 敏感键（config_repo.SENSITIVE_EXACT_KEYS 已含）
DUMP_RETENTION_DAYS_KEY = "dump.retention_days"

DUMP_ENGINES: tuple[str, ...] = ("pg", "mysql", "sqlite")
DUMP_PORT_DEFAULT = 5432
DUMP_RETENTION_DAYS_DEFAULT = 7          # 产物保留天数缺省；<=0 = 不启用清理（fail-safe）
DUMP_PROBE_TIMEOUT_SECONDS = 15          # pg 客户端/服务端版本探测超时
DUMP_RUN_TIMEOUT_SECONDS = 1800          # 单库 dump 执行超时（30 分钟）

# ---------------------------------------------------------------- P3-A：R-025 凭据加密存储（E07 可调）

# 凭据加密开关键（config KV；0/1）。**缺省 1（默认开）**——owner 2026-10-16 裁决：
# 开=敏感键（cred.* / dump.password / notify.*）写入即加密（AES-256-GCM，密钥源自
# 仓库密码文件派生）；关=明文存储。读侧恒按 `enc:v1:` 前缀判定，与开关无关（崩溃安全）。
# 清除该键（值为 NULL）等同缺省=开。
CRED_ENCRYPTION_KEY = "security.cred_encryption"

# ---------------------------------------------------------------- P3-B：R-024 Prometheus 指标与报表

# 指标端点抓取令牌（config KV；**库内只存 SHA-256**，与 session token 同口径）。
# 界面设置（不进 ENV_KEY_MAP——env 同步写原值会绕过硬哈希）；空/未设 = 仅 session 可拉。
METRICS_TOKEN_KEY = "metrics.token"
# 成功率报表时间窗上限（天）：与 retention.jobs_days 缺省 90 对齐——更早的 backup
# 历史可能已被清理，报了反而误导；下限 1 天。
REPORTS_MAX_DAYS = 90

# ---------------------------------------------------------------- P2 收尾批次 D：R-015 原位恢复（E19/E20）

# E20 覆盖预览分页（游标分页，编码与 E18 同源：base64 偏移游标）
RESTORE_PREVIEW_DEFAULT_LIMIT = 200
RESTORE_PREVIEW_MAX_LIMIT = 1000
# ls --json 扫描超时（比 E18 单子树略宽：预览要扫整个快照根）
RESTORE_PREVIEW_TIMEOUT_SECONDS = 60
# mtime 比对容差（秒）：restic 存纳秒、os.stat 回 float，等值判定需容忍浮点精度损失
COMPARE_MTIME_TOLERANCE_SECONDS = 0.001

# ---------------------------------------------------------------- 批次 D：C3 任务级失败重试

# 调度触发失败后的最大重试次数与值域（manual 触发恒不重试，对齐 Duplicati
# number-of-retries 的「自动运行才重试」语义）；default=2 即「首次 + 2 次重试」。
RETRY_COUNT_DEFAULT = 2
RETRY_COUNT_MAX = 5
# 指数退避：delay = min(base * 2 ** (attempt - 1), max)（attempt 从 1 计）
RETRY_BASE_DELAY_SECONDS = 30
RETRY_MAX_DELAY_SECONDS = 900

# ---------------------------------------------------------------- 批次 D：C4 copy + key + 多目的地

# 单任务的镜像仓数量上限：每个镜像都要独立走一遍 copy 全链路（下载去重索引 + 上传
# 包），无上限会让单次备份被镜像阶段拖到超过调度周期，后续周期全部撞仓库锁冲突。
MIRROR_REPO_IDS_MAX = 3
# 仓库口令文件状态字面量（单一来源：env_sync 派生、API 契约、C4 镜像阶段三处共用）
PASSFILE_STATUS_CONFIGURED = "configured"
PASSFILE_STATUS_MISSING = "missing"
# restic key 系列操作超时（add 取追加锁、remove/passwd 取独占锁，与 A3 tag 同口径）
KEY_OPERATION_TIMEOUT_SECONDS = 60
# 手动 copy（搬家场景）超时：全量首传可能很慢，给足余量；中断安全（restic copy 增量可续传）
MANUAL_COPY_TIMEOUT_SECONDS = 3600
