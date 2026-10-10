"""全局常量：任务枚举、路径白名单前缀、错误码（§5.0 错误码表）、审计动作。

本模块不依赖任何其他 app 模块，供全项目单向引用，避免循环导入。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 任务与状态（方案 §5 状态机）

JOB_TYPES: tuple[str, ...] = ("backup", "restore", "forget", "check", "unlock", "init", "dump")
JOB_STATUSES: tuple[str, ...] = ("pending", "running", "success", "failed", "interrupted", "cancelled")
JOB_TRIGGERS: tuple[str, ...] = ("manual", "scheduled")

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

# 端点级扩展码（§5.0 注）
PATH_OUT_OF_PREFIX = "PATH_OUT_OF_PREFIX"          # 400 E05/E19
ENV_OVERRIDDEN = "ENV_OVERRIDDEN"                  # 409 E07
DEFAULT_POLICY_IMMUTABLE = "DEFAULT_POLICY_IMMUTABLE"  # 409 E11
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
    PATH_OUT_OF_PREFIX: 400,
    ENV_OVERRIDDEN: 409,
    DEFAULT_POLICY_IMMUTABLE: 409,
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
AUDIT_CACHE_CLEAR = "cache.clear"

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
