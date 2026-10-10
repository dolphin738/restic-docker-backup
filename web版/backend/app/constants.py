"""全局常量：任务枚举、路径白名单前缀、错误码（§5.0 错误码表）、审计动作。

本模块不依赖任何其他 app 模块，供全项目单向引用，避免循环导入。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 任务与状态（方案 §5 状态机）

JOB_TYPES: tuple[str, ...] = ("backup", "restore", "forget", "check", "unlock", "init", "dump")
JOB_STATUSES: tuple[str, ...] = ("pending", "running", "success", "failed", "interrupted", "cancelled")
JOB_TRIGGERS: tuple[str, ...] = ("manual", "scheduled")

# 状态分组（§4.5 / §5 状态机）：活跃态用于互斥冲突判定，终态用于 SSE 收流与取消校验
ACTIVE_JOB_STATUSES: tuple[str, ...] = ("running", "pending")
TERMINAL_JOB_STATUSES: tuple[str, ...] = ("success", "failed", "interrupted", "cancelled")

# ---------------------------------------------------------------- 路径白名单（§7.3 PathMapper）

# 可被选为备份源的根前缀（按 RESTIC_WEB_MODE 二选一）
SOURCE_ALLOWED_PREFIXES: tuple[str, ...] = ("/host", "/sources")
# 恢复目标白名单（P1 仅"恢复到时间戳子目录"，目标限定在可写挂载点）
RESTORE_ALLOWED_PREFIXES: tuple[str, ...] = ("/restore",)
# E05 目录浏览允许的前缀
BROWSE_ALLOWED_PREFIXES: tuple[str, ...] = ("/host", "/sources")

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

# 端点级扩展码（§5.0 注）
PATH_OUT_OF_PREFIX = "PATH_OUT_OF_PREFIX"          # 400 E05/E19
ENV_OVERRIDDEN = "ENV_OVERRIDDEN"                  # 409 E07
DEFAULT_POLICY_IMMUTABLE = "DEFAULT_POLICY_IMMUTABLE"  # 409 E11
POLICY_WRITE_CLOSED = "POLICY_WRITE_CLOSED"            # 409 E09/E10/E11：P1 多策略写未开放
NOT_CANCELLABLE = "NOT_CANCELLABLE"                # 409 E16
RESTORE_FORBIDDEN = "RESTORE_FORBIDDEN"            # 403 E19
BOOTSTRAP_SHOWN = "BOOTSTRAP_SHOWN"                # 410 Bootstrap
PASSWORD_WRONG = "PASSWORD_WRONG"                  # 401 E01
CSRF_ORIGIN = "CSRF_ORIGIN"                        # 403 写方法 Origin 校验
BOOTSTRAP_REQUIRED = "BOOTSTRAP_REQUIRED"          # 403 首启未改密门

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
    PATH_OUT_OF_PREFIX: 400,
    ENV_OVERRIDDEN: 409,
    DEFAULT_POLICY_IMMUTABLE: 409,
    POLICY_WRITE_CLOSED: 409,
    NOT_CANCELLABLE: 409,
    RESTORE_FORBIDDEN: 403,
    BOOTSTRAP_SHOWN: 410,
    PASSWORD_WRONG: 401,
    CSRF_ORIGIN: 403,
    BOOTSTRAP_REQUIRED: 403,
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
AUDIT_JOB_SKIPPED_CONFLICT = "job.skipped_conflict"
AUDIT_JOB_SUBMIT = "job.submit"
AUDIT_JOB_CANCEL = "job.cancel"
AUDIT_JOB_FINISHED = "job.finished"
AUDIT_JOB_RECOVERED = "job.recovered"
AUDIT_CACHE_CLEAR = "cache.clear"
AUDIT_ENV_SYNC = "config.env_sync"
AUDIT_SOURCES_DRY_RUN = "sources.dry_run"
AUDIT_POLICY_CREATE = "policy.create"
AUDIT_POLICY_UPDATE = "policy.update"
AUDIT_POLICY_DELETE = "policy.delete"
AUDIT_REPO_TEST = "repo.test"

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

# ---------------------------------------------------------------- E29/E30 仓库探测

REPO_PROBE_TIMEOUT_SECONDS = 15         # cat config 连通性探测
REPO_STATS_TIMEOUT_SECONDS = 30         # stats --mode raw-data 容量探测
# 仓库状态进程内缓存 TTL：避免仪表盘轮询反复打仓库（E30 成本可控的关键手段）
REPO_STATUS_CACHE_SECONDS = 30.0
# 快照文件树分页游标上限（防止游标被篡改为超大值导致空转读取）
SNAPSHOT_FILES_MAX_OFFSET = 1_000_000
