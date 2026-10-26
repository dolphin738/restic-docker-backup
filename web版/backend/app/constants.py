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
    "prune",  # 独立回收未引用数据块（restic prune；补漏 forget 未勾选 --prune 的遗留空间）
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

# 备份源根 / 恢复根的**默认容器路径**（settings 的默认值来源）
DEFAULT_SOURCES_ROOT = "/sources"
DEFAULT_RESTORE_ROOT = "/restore"
# ⚠️ 备份源的**生效白名单不再是常量**——它由部署形态派生（设计方案 §3.2 单一事实源）：
#     docker → (DEFAULT_SOURCES_ROOT,)    host → ("/",)
#   派生实现见 :func:`app.services.path_mapper.source_allowed_prefixes`，
#   运行期一律走它（或 ``settings.browse_root``）；此处刻意**不**保留第二份白名单常量，
#   否则 UI 可选路径与后端接受写入的路径会再次漂移（S2 argv 白名单的前置条件）。
# ⚠️ 恢复目标的两条白名单**不再是常量**（owner 2026-10-25 裁决 Q1=A，同一派生纪律）：
#    时间戳流  restore_allowed_prefixes(settings)      docker → (restore_root,)=/restore；host → (browse_root,)=/
#    原位流    inplace_restore_allowed_prefixes(settings) docker → /sources；host → /
#   派生实现见 :func:`app.services.path_mapper.restore_allowed_prefixes` /
#   :func:`...inplace_restore_allowed_prefixes`；运行期一律走它们。
#   历史说明：host 模式快照存的是主机绝对路径（如 /srv/data），固定 /sources 会让合法
#   原位恢复直接 400；固定 /restore 则让「选择位置」只能在这一棵目录里选。
# 原位恢复**执行层**的 --target 固定值（S7 v1.3「执行层落点纪律」）：
# restic 的落点是「--target + 快照内绝对路径」的拼接（非替换），而本项目快照存的是
# 容器绝对路径（/sources/etc/...），故只有 "/" 才是真原位（写回 /sources/etc/...）；
# 传 <目标目录> 会嵌套成 /sources/etc/sources/etc/... ——一个目标文件都不会被覆盖。
# S1：该字面量的唯一出处（ResticCmd 只在显式放行分支里引用它）。
INPLACE_RESTORE_TARGET_ROOT = "/"
# 注：E05 目录浏览允许的前缀与备份源同源，派生于 :func:`app.services.path_mapper
# .source_allowed_prefixes`；此处不再保留常量副本（理由同上）。

# ---------------------------------------------------------------- 部署形态检测（设计方案 §3.1）

# 显式覆盖环境变量：DEPLOY_MODE=docker|host|auto（默认 auto = 自动检测）。
# 自动检测**只决定默认值，不充当安全边界**——安全边界恒为 deny 清单（见下）+ 本变量显式覆盖。
DEPLOY_MODE_ENV = "DEPLOY_MODE"
DEPLOY_MODE_AUTO = "auto"
DEPLOY_MODE_DOCKER = "docker"
DEPLOY_MODE_HOST = "host"
DEPLOY_MODE_VALUES: tuple[str, ...] = (DEPLOY_MODE_AUTO, DEPLOY_MODE_DOCKER, DEPLOY_MODE_HOST)
# 自动检测链：① ``/.dockerenv`` 存在（绝大多数运行时的既定落点）；
#             ② 兜底：``/proc/self/mountinfo`` 中根挂载点 fstype 以 overlay 开头
DOCKER_ENV_FILE = "/.dockerenv"
MOUNTINFO_PATH = "/proc/self/mountinfo"
OVERLAY_FSTYPE_PREFIX = "overlay"
# host 模式的浏览根：整文件系统。可备份面 = 该根，安全边界由 deny 前缀承担（§3.1 原则）。
HOST_BROWSE_ROOT = "/"
# host 模式 deny 前缀基底：系统伪目录（E33 既有口径）；运行时再叠加仓库存储位置与
# 应用数据目录（见 :func:`app.services.deploy_mode.host_deny_prefixes`）。
HOST_DENY_BASE_PREFIXES: tuple[str, ...] = ("/proc", "/sys", "/dev", "/run")

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
REPO_NOT_EMPTY = "REPO_NOT_EMPTY"                  # 409 E41：仓库下仍有任务/历史任务，拒绝删除（R-023）
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
    REPO_NOT_EMPTY: 409,
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
# 独立回收预览（restic prune --dry-run，只读）：与 forget.preview 同先例，敏感动作留痕
AUDIT_PRUNE_PREVIEW = "prune.preview"
# 独立回收执行（restic prune，写入/删块）：危险操作清单项（S6 清单 16→17），detail 不含命令行原文
AUDIT_PRUNE_RUN = "prune.run"
AUDIT_JOB_SKIPPED_CONFLICT = "job.skipped_conflict"
AUDIT_JOB_SUBMIT = "job.submit"
AUDIT_JOB_CANCEL = "job.cancel"
# P7 D2 Abort 二段（2026-10-23 owner 裁决 A5）：跳过 SIGTERM 宽限直接 SIGKILL——与 cancel
# 分动作留痕（规范 §3.1 S6 清单 15→16 项），detail 记 force 与宽限值，不含命令行原文（S3）。
AUDIT_JOB_ABORT = "job.abort"
AUDIT_JOB_FINISHED = "job.finished"
AUDIT_JOB_RECOVERED = "job.recovered"
AUDIT_CACHE_CLEAR = "cache.clear"
AUDIT_ENV_SYNC = "config.env_sync"
AUDIT_SOURCES_DRY_RUN = "sources.dry_run"
AUDIT_REPO_TEST = "repo.test"
# R-023 仓库密码文件首次写入（生成或自填）——安全敏感动作，留痕但**不记录明文**
AUDIT_REPO_PASSWORD_SET = "repo.password_set"
AUDIT_REPO_REBUILD = "repo.rebuild"  # 自救通道：清空快照并用新密码重新初始化（仅本地 FS；2026-10-21 起不限 default）
# R-023 删除仓库：级联清除终态作业历史（jobs 行 + 日志文件）与该仓库凭据键——不可逆删除，
# 入 S6 危险操作清单（15 项）；detail 只含计数与仓库名，不回显凭据（S3）。
AUDIT_REPO_DELETE = "repo.delete"
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
# E39 时间同步：按 NTP 结果校准系统时钟（改系统状态，危险操作留痕；偏移/原因入 detail，无凭据面）
AUDIT_TIME_SYNC = "time.sync"
# R-020 裁决3：审计归档（压缩落盘，append-only 的「整段滚动导出」落地）
AUDIT_ARCHIVE = "audit.archive"
# R-020 裁决3：审计按保留期滚动清理（唯一受控 DELETE 路径，经 AuditRepo 单一出口；S6 例外）
AUDIT_CLEANUP = "audit.cleanup"

# ---------------------------------------------------------------- E39 时间同步（NTP 对时 + 系统时钟校准）

# NTP 服务器候选（按序探测，任一成功即用；公网池 + 国内池，容器多数环境可达）
NTP_SERVERS: tuple[str, ...] = ("pool.ntp.org", "ntp.aliyun.com", "cn.pool.ntp.org")
NTP_PORT = 123
NTP_TIMEOUT_SECONDS = 3.0  # 单服务器 UDP 超时（秒）
NTP_PROBE_CACHE_SECONDS = 30.0  # 探测结果进程内缓存（秒）——防前端轮询把 NTP 池打爆
# 偏移低于该值不动系统时钟（毫秒）：规避无谓写入与日志噪音
TIME_SYNC_MIN_OFFSET_MS = 1000.0
# 偏移超过该值视为异常响应（毫秒，1 天）：拒绝把系统时钟改到离谱的位置
TIME_SYNC_MAX_OFFSET_MS = 86_400_000.0

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

# ---------------------------------------------------------------- 系统设置域（compose → UI 迁移第一批）

# 键名（E07 config 表）：compose 的 LOG_LEVEL / DB_BUSY_TIMEOUT_MS 迁入系统设置，
# env 仍可覆盖（ENV_KEY_MAP 登记 → source='env' → UI 置灰 + 409 保护）。
LOG_LEVEL_KEY = "log.level"
DB_BUSY_TIMEOUT_MS_KEY = "db.busy_timeout_ms"
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DB_BUSY_TIMEOUT_MS_MIN = 100       # 锁等待下限（毫秒）：低于 100ms 基本等于不等锁
DB_BUSY_TIMEOUT_MS_MAX = 600_000   # 锁等待上限（毫秒）：10 分钟，防配置被写爆后无限等

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
# 快照列表进程内缓存 TTL（P1 任务卡片 ``snapshot_count``：一次列表按 tag 分组计数）。
# **独立常量，不共享 E30 的 ``REPO_STATUS_CACHE_SECONDS``**（B6，owner 2026-10-23 裁决）：
# 两者关心的对象不同——status 是「此刻能否连通 / 是否上锁」（希望灵敏），snapshot_count 只在
# 备份终态变化（且 ``invalidate_repo_status_cache`` 已连带清本缓存）→ 各自可调、互不牵连。
SNAPSHOT_LIST_CACHE_SECONDS = 30.0
# P1 卡片 ``snapshot_count`` 的探测超时（B4，2026-10-23 裁决）：取 5s 而非列表页的 20s——
# 它是**附带指标**，不能让主页 GET 列表被仓库不可达拖到 20s。超时/失败结论入缓存并指数退避
# （见 SnapshotCatalog：30s → 60s → 120s → 240s → 300s 封顶），避免每次 TTL 到期再爆等一次。
SNAPSHOT_COUNT_TIMEOUT_SECONDS = 5.0
SNAPSHOT_COUNT_BACKOFF_MAX_SECONDS = 300.0
# 快照内文件搜索缓存（B3，2026-10-23 裁决）：快照 id 内容不可变 → 缓存只为省重复 ``ls``
# （游标翻页 / 同一关键词重复搜索）。条目数按 query 维度限 LRU（MAX_ENTRIES），单条命中数
# 超 MAX_ITEMS 则不缓存（防单字符搜索把整棵快照树钉在内存里）。
SNAPSHOT_SEARCH_CACHE_SECONDS = 30.0
SNAPSHOT_SEARCH_CACHE_MAX_ENTRIES = 4
SNAPSHOT_SEARCH_CACHE_MAX_ITEMS = 50_000
# P3 快照内文件搜索：关键词长度上限（防超长串拖垮服务端过滤；下限由空串校验兜底）
SNAPSHOT_SEARCH_QUERY_MAX_LENGTH = 200

# ---------------------------------------------------------------- E21 forget 预览（R-016）

FORGET_PREVIEW_TIMEOUT_SECONDS = 60     # forget --dry-run 扫描快照超时（大仓库比 snapshots 慢）
TAG_OPERATION_TIMEOUT_SECONDS = 60       # A3 restic tag 独占锁修改快照超时（打/去标签）
PRUNE_PREVIEW_TIMEOUT_SECONDS = 120      # prune --dry-run 扫描全部数据块（比 forget 更重，大仓库分钟级）

# ---------------------------------------------------------------- R-018 通知（E23/E24）

NOTIFY_HTTP_TIMEOUT_SECONDS = 10        # 单渠道推送超时（通知绝不阻塞任务链路，超时即放弃）
# C2 SMTP 邮件渠道（Duplicati SendMail 模块等价物）
NOTIFY_MAIL_TIMEOUT_SECONDS = 20        # 单次 SMTP 会话超时（比 HTTP 宽：EHLO/STARTTLS/AUTH 多轮往返）
MAIL_PORT_DEFAULT = 587                 # 提交端口缺省（STARTTLS 提交端口，与 Duplicati 默认一致）
MAIL_SECURITY_MODES: tuple[str, ...] = ("none", "starttls", "tls")  # 加密方式白名单
NOTIFY_SKIPPED_THRESHOLD_DEFAULT = 100  # skipped 告警阈值缺省（Q4 裁决：0=只要有 skipped 即告警）
NOTIFY_SKIPPED_THRESHOLD_MAX = 1_000_000  # 阈值写爆兜底
# MeoW 消息形态（API 文档 §3.2：msgType/htmlHeight 是查询参数，不属于 JSON Body）
MEOW_MSG_TYPES: tuple[str, ...] = ("text", "html")  # 消息显示类型白名单
MEOW_MSG_TYPE_DEFAULT = "text"          # 缺省纯文本（默认模板渲染的是多行纯文本）
MEOW_HTML_HEIGHT_DEFAULT = 200          # html 消息显示高度缺省（像素，API 同值）
MEOW_HTML_HEIGHT_MIN = 50               # 高度下限（低于此值 App 侧无法完整渲染）
MEOW_HTML_HEIGHT_MAX = 2000             # 高度上限（超出屏幕高度无意义）
# MeoW 使用限制（API 文档 §5.8 非会员限频 + §5.9 公共出口 IP 提示）
MEOW_MIN_INTERVAL_SECONDS = 3.0         # 免费版最小发送间隔（3 秒 1 条）
MEOW_MAX_WAIT_SECONDS = 3.0             # 为守最小间隔最多等待多久；超出即丢弃（通知不拖任务链路）
# 套餐等级 → (最小间隔秒, ((窗口秒数, 条数上限), ...))；间隔 0.0 = 不限频率（只受日总量约束）
MEOW_PLANS: tuple[str, ...] = ("free", "premium", "lifetime")
MEOW_PLAN_DEFAULT = "free"
MEOW_PLAN_LIMITS: dict[str, tuple[float, tuple[tuple[int, int], ...]]] = {
    # 免费版：API §5.8 四档限频
    "free": (MEOW_MIN_INTERVAL_SECONDS, ((60, 15), (3600, 60), (86400, 1000))),
    # 高级版：频率不限，只受每天 2000 条约束
    "premium": (0.0, ((86400, 2000),)),
    # 终身版：频率不限，只受每天 3000 条约束
    "lifetime": (0.0, ((86400, 3000),)),
}

# ---------------------------------------------------------------- E29/E30 仓库探测

REPO_PROBE_TIMEOUT_SECONDS = 15         # cat config 连通性探测
REPO_STATS_TIMEOUT_SECONDS = 30         # stats --mode raw-data 容量探测
REPO_INIT_TIMEOUT_SECONDS = 120         # restic init（重建仓库）超时
# rclone listremotes 只读探测（A1）：纯本地操作（读 conf），5s 足够；超时即视为不可用
RCLONE_PROBE_TIMEOUT_SECONDS = 5.0
# Windows 子进程可用性提示（2026-10-19 实证）：uvicorn 的 --reload / --workers>1 会经
# use_subprocess 分支把 Windows 事件循环强制选成 SelectorEventLoop——它不支持
# create_subprocess_exec（NotImplementedError，asyncio/base_events.py:539），restic/
# rclone 全部子进程操作 500。修法：--loop none（走系统默认 policy=ProactorEventLoop），
# dev-scripts/dev.ps1 已内置；Linux/Docker 无此问题。rclone 探测与 restic spawn 共用。
SUBPROCESS_LOOP_HINT = (
    "当前事件循环不支持子进程（Windows 下 uvicorn --reload 默认 SelectorEventLoop）"
    "——请以 --loop none 重启后端（dev-scripts/dev.ps1 已内置该参数）"
)
# 仓库状态进程内缓存 TTL：避免仪表盘轮询反复打仓库（E30 成本可控的关键手段）
REPO_STATUS_CACHE_SECONDS = 30.0
# 探测失败的结构化分类（E30 status / E29 test 共用；前端按它判定「初始化」按钮该不该出现，
# 不必再匹配中文原因文案——文案可改、分类稳定，按钮判定不会随文案漂移）
FAILURE_KIND_LOCKED = "locked"
FAILURE_KIND_PASSWORD = "password"
FAILURE_KIND_NOT_INITIALIZED = "not_initialized"
FAILURE_KIND_CREDENTIALS = "credentials"
FAILURE_KIND_NETWORK = "network"
FAILURE_KIND_TIMEOUT = "timeout"
FAILURE_KIND_UNKNOWN = "unknown"
# 仓库密码生成长度（后端 secrets 生成与前端「生成强随机密码」同口径，避免两处位数不一致）
REPO_PASSWORD_LENGTH = 24
# 快照文件树分页游标上限（防止游标被篡改为超大值导致空转读取）
SNAPSHOT_FILES_MAX_OFFSET = 1_000_000

# ---------------------------------------------------------------- P2 收尾批次 A（E13 全量化 / E26 / E25 / retention）

# 任务历史/审计查询分页上限（Q13 基线：E13 对齐 E26 的既有上限，避免两套口径）
HISTORY_QUERY_MAX_LIMIT = 500

# retention（R-020）：jobs 行与任务日志的保留天数（retention.jobs_days 可经 E07 调整）
RETENTION_JOBS_DAYS_KEY = "retention.jobs_days"
RETENTION_JOBS_DAYS_DEFAULT = 90
# retention.logs_days：任务日志文件保留天数；键缺省时回退 retention.jobs_days（实现 fallback，无新列）
RETENTION_LOGS_DAYS_KEY = "retention.logs_days"

# retention（R-020 裁决1/裁决3）：审计日志保留与归档
# - retention.audit_days：审计行保留天数（缺省 365）；<=0 = 不启用自动清理（永不删）
# - retention.audit_archive_days：压缩归档文件（.jsonl.gz）自身保留天数（缺省 365）；<=0 = 不清理
# - retention.audit_archive_dir：归档目录（DB 配置键，留空回落 settings.audit_archive_dir）
# - retention.audit_archive_cold_target：冷存目标占位（仅提示，应用不自动同步；部署文档指路外送）
RETENTION_AUDIT_DAYS_KEY = "retention.audit_days"
RETENTION_AUDIT_DAYS_DEFAULT = 365
RETENTION_AUDIT_ARCHIVE_DAYS_KEY = "retention.audit_archive_days"
RETENTION_AUDIT_ARCHIVE_DAYS_DEFAULT = 365
RETENTION_AUDIT_ARCHIVE_DIR_KEY = "retention.audit_archive_dir"
RETENTION_AUDIT_ARCHIVE_COLD_TARGET_KEY = "retention.audit_archive_cold_target"
# 归档单文件（按年切分、追加不覆盖）：audit-<YYYY>.jsonl.gz（同一年的每日维护都追加进同一文件）
AUDIT_ARCHIVE_FILE_GLOB = "audit-*.jsonl.gz"

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
# R-017 收口（2026-10-21）：每日抽样由「随机百分比」改为「n/t 确定性分桶滚动扫描」——
# t = 全量扫描周期天数，每天读第 n 个桶，t 天恰好覆盖全仓数据块一次（零重复、零遗漏）。
BEHAVIOR_CHECK_SUBSET_DENOMINATOR_KEY = "behavior.check_subset_denominator"
# 月度全量独立开关（0/1，缺省 0=关）：与总开关 check_enabled 分离，默认关闭——
# 每日 n/t 已能按周期全覆盖，月度全量默认只在需要「时点权威全量结论」时手动打开。
BEHAVIOR_MONTHLY_FULL_ENABLED_KEY = "behavior.monthly_full_enabled"

# 每日 n/t 扫描周期天数（t）缺省与值域（[0, 256]；0 = 不做每日扫描，仅月度全量）
# 上限 256 对齐 restic totalBucketsMax（见 services/restic.py READ_DATA_SUBSET_MAX_BUCKETS）。
CHECK_SUBSET_DENOMINATOR_DEFAULT = 30
CHECK_SUBSET_DENOMINATOR_MIN = 0
CHECK_SUBSET_DENOMINATOR_MAX = 256
# 已废弃：每日抽样的「随机百分比」比例（0=不抽样）。保留仅为旧配置兜底——
# 新的 check_subset_denominator 未设置时按 t = round(1 / ratio) 换算（见 scheduler）。
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
