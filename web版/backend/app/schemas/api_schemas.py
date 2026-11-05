"""API 请求/响应模型（§5.0 统一约定 + §5.1 端点明细）。

本批次覆盖：E01 登录、E02 登出、E03 me、E04 health、Bootstrap 取密码、E28 改密，
以及 config 键值的脱敏输出与分页壳（供后续批次复用）。
"""

from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from app.constants import (
    MAIL_PORT_DEFAULT,
    MEOW_HTML_HEIGHT_DEFAULT,
    MEOW_HTML_HEIGHT_MAX,
    MEOW_HTML_HEIGHT_MIN,
    MEOW_MSG_TYPE_DEFAULT,
    MEOW_PLAN_DEFAULT,
    MIRROR_REPO_IDS_MAX,
    REPO_ID_MAX_LENGTH,
    REPO_ID_PATTERN,
    RESTORE_PREVIEW_DEFAULT_LIMIT,
    RESTORE_PREVIEW_MAX_LIMIT,
    RETRY_COUNT_DEFAULT,
    SNAPSHOT_FILES_DEFAULT_LIMIT,
    SNAPSHOT_FILES_MAX_LIMIT,
    SNAPSHOT_SEARCH_QUERY_MAX_LENGTH,
)
from app.schemas.enums import ConfigSource, JobStatus, JobTrigger, JobType

T = TypeVar("T")

# C2 SMTP 加密方式（字面量白名单，与 constants.MAIL_SECURITY_MODES 同源；非法值由
# pydantic 直接 422，不留给运行期回退逻辑兜底）
MailSecurity = Literal["none", "starttls", "tls"]
# MeoW 消息显示类型（字面量白名单，与 constants.MEOW_MSG_TYPES 同源）
MeowMsgType = Literal["text", "html"]
# MeoW 套餐等级（决定本地限流档位：free 守 API §5.8 限频，premium/lifetime 只守日总量）
MeowPlan = Literal["free", "premium", "lifetime"]


class _Base(BaseModel):
    """所有模型的公共基类：禁止未知字段，避免前端误传被静默忽略。"""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 统一响应壳（§5.0）


class ErrorResponse(_Base):
    """统一错误响应体（所有非 2xx）。"""

    code: str = Field(description="统一错误码，如 JOB_CONFLICT")
    message: str = Field(description="可直接展示给用户的中文提示")
    data: dict[str, Any] | None = Field(default=None, description="附加数据，如 retry_after")


class OkResponse(_Base):
    """通用成功响应体（E02 登出、E28 改密等）。"""

    code: str = "ok"


class Page(_Base, Generic[T]):
    """列表分页壳：``?limit=&offset=`` → items/total/limit/offset（§5.0 分页约定）。"""

    items: list[T]
    total: int = 0
    limit: int = 50
    offset: int = 0


class CursorPage(_Base, Generic[T]):
    """游标分页壳：文件树等大列表（§5.0 分页约定）。"""

    items: list[T]
    next_cursor: str | None = None


# ---------------------------------------------------------------- E01 登录 / E02 登出 / E03 me


class LoginRequest(_Base):
    """E01 登录请求。"""

    password: str = Field(min_length=1, max_length=256, description="管理员密码明文")


class MeResponse(_Base):
    """E03 会话信息（供运行模式卡 US-G2 与前端路由守卫使用；A0 模式收敛后只剩 user 维度）。"""

    authenticated: bool = True
    must_change_password: bool = Field(description="首启强制改密标志（admin_account.must_change）")
    user: str = Field(description="容器内运行用户（root / backup …）")


class BootstrapResponse(_Base):
    """首启一次性随机密码响应。"""

    password: str = Field(description="随机管理密码明文（仅此一次返回）")
    must_change_password: bool = True
    warning: str = "请立即保存并修改为新密码，关闭后无法再次查看"


class ChangePasswordRequest(_Base):
    """E28 改密请求（首启场景 old_password 为随机引导密码）。"""

    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


# ---------------------------------------------------------------- E04 health


class HealthResponse(_Base):
    """E04 健康检查（HEALTHCHECK 用，不校验仓库连通）。"""

    status: str = Field(description="ok=正常 / degraded=依赖异常但进程存活")
    db: str = Field(description="ok=SQLite 可查询 / error=不可查询")


# ---------------------------------------------------------------- config 键值输出（§4.3）


class ConfigItem(_Base):
    """单个配置项输出：敏感项只回 ``has_value`` + ``source``，不回明文。

    ``degraded`` 三态（方案 §8.4）：键的密钥源读不出来时，展示路径不抛异常、也不伪装
    成空值，而是 ``has_value=False`` + ``degraded=True`` + ``degraded_reason``。
    于是三种状态互不混淆：没配过（``has_value=False`` 且 ``degraded=False``）、有值
    （``has_value=True``）、失效（``has_value=False`` 且 ``degraded=True``）。
    """

    key: str
    value: str | None = None
    has_value: bool = False
    source: ConfigSource = ConfigSource.UI
    sensitive: bool = False
    degraded: bool = False
    degraded_reason: str | None = None


class ConfigResponse(_Base):
    """E06 配置读（本批次仅定义模型，路由在后续批次挂载）。"""

    items: list[ConfigItem] = Field(default_factory=list)
    passfile_status: str = Field(default="missing", description="configured|missing，仓库密码文件挂载状态")


class ConfigPatchRequest(_Base):
    """E07 配置写请求。"""

    patch: dict[str, str | None] = Field(description="键→值；置 null 表示清空该键")


# ---------------------------------------------------------------- E12–E16 / E27 任务


class JobOut(_Base):
    """E14 任务详情（§4.5 字段全集）。

    ``task_id`` 语义（选项② §1.2 决策二）：该 job 由哪个备份任务产生；删除任务后
    ``task_id`` 经外键置空（行与日志保留）。
    """

    id: str
    type: JobType
    status: JobStatus
    trigger: JobTrigger
    repo_id: str = "default"
    policy_id: int | None = None
    task_id: int | None = None
    snapshot_id: str | None = None
    bytes_done: int | None = None
    files_done: int | None = None
    skipped_count: int = 0
    last_event_id: int = 0
    log_path: str
    summary: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    created_at: str


class JobCreateRequest(_Base):
    """E12 触发任务请求。"""

    type: JobType = Field(description="任务类型白名单：backup/restore/forget/check/unlock/init/dump")
    task_id: int | None = Field(default=None, description="关联备份任务 id")
    snapshot_id: str | None = Field(default=None, description="restore/forget 关联快照")
    params: dict[str, Any] = Field(default_factory=dict, description="补充参数（sources/excludes/target…）")


class JobCreateResponse(_Base):
    """E12 响应（202）。"""

    id: str
    status: JobStatus = JobStatus.PENDING
    type: JobType


class JobLogResponse(_Base):
    """E27 任务日志尾部。"""

    id: str
    log_path: str
    tail_bytes: int
    content: str
    truncated: bool = False


# ---------------------------------------------------------------- E22 check 结果视图（R-017，批次 B）


class CheckErrorOut(_Base):
    """E22 单条 check 错误（message 为 restic 输出原文，逐字保留、含换行）。"""

    message: str


class CheckView(_Base):
    """E22 check 结果视图（§2.4：由 summary + error 行驱动，**无进度集**）。

    ``broken_packs`` 为损坏 pack id 的保序列表（健康仓库为空列表——解析层已把
    Go nil 的 ``null`` 归一）；``num_errors`` 取 restic summary 原值（与
    ``error_count`` 独立：截断场景下 error 行可能少于 summary 计数）。
    """

    errors: list[CheckErrorOut] = Field(default_factory=list, description="error 行原文列表（保序）")
    error_count: int = 0
    num_errors: int = 0
    broken_packs: list[str] = Field(default_factory=list, description="损坏 pack id（修复动作数据源）")
    suggest_repair_index: bool = False
    suggest_prune: bool = False
    read_data: bool = Field(default=False, description="是否为 --read-data（全量读数据）形态")


class CheckDetailResponse(JobOut):
    """E22 响应：``JobOut`` 全字段 + ``check_view``（不建新表，Q9 基线）。"""

    check_view: CheckView


class AuditOut(_Base):
    """E26 审计日志行（§4.6 字段全集；detail 为 JSON 文本原文）。"""

    id: int
    ts: str
    action: str
    target: str | None = None
    detail: str | None = None
    result: str
    created_at: str


class AuditPageResponse(Page[AuditOut]):
    """E26 响应：分页壳 + ``actions`` 动作名枚举（前端筛选下拉数据源，repo 层去重查询）。"""

    actions: list[str] = Field(default_factory=list)


class AuditArchiveItem(_Base):
    """R-020 裁决3：审计压缩归档文件（``/data/audit/archive`` 下的 ``*.jsonl.gz``）。"""

    name: str = Field(description="归档文件名（如 audit-2026.jsonl.gz）")
    size_bytes: int = Field(description="文件大小（字节）")
    mtime_iso: str = Field(description="最后修改时间（UTC ISO）")


class JobCleanupResponse(_Base):
    """任务历史「立即清理」响应（裁决5：仅清 jobs 行与任务日志，不触碰审计日志）。"""

    jobs_deleted: int = Field(description="删除的 jobs 终态行数")
    log_files_deleted: int = Field(description="删除的任务日志文件数（孤儿 + 过期）")
    log_bytes_freed: int = Field(description="释放的日志字节数")
    jobs_days: int = Field(description="生效的 retention.jobs_days（<=0 表示未启用）")
    logs_days: int = Field(description="生效的 retention.logs_days（<=0 表示日志保留关闭）")


class CacheUsageResponse(_Base):
    """E25 缓存占用响应（Q14 基线：du 超时回部分值 + truncated，不 5xx）。"""

    path: str
    size_bytes: int
    items_count: int
    measured_at: str
    truncated: bool = False


# ---------------------------------------------------------------- E05 / E31 备份源


class SourceItem(_Base):
    """E05 目录浏览条目。"""

    name: str
    type: str = Field(description="dir|file|other")
    display_path: str = Field(description="剥掉容器前缀后的原路径语义")
    is_mountpoint: bool = Field(default=False, description="是否为容器挂载点（供源树节点徽章）")


class SourceBrowseResponse(_Base):
    """E05 目录浏览响应。"""

    path: str = Field(description="展示形态的当前路径（如 /etc）")
    items: list[SourceItem] = Field(default_factory=list)
    mounts: list[str] = Field(
        default_factory=list, description="挂载点快跳（展示形态；仅根目录请求返回，供 chips 使用）"
    )
    next_cursor: str | None = None


class DryRunRequest(_Base):
    """E31 首备扫描范围预览请求。"""

    sources: list[str] = Field(min_length=1, description="容器内路径（/sources 前缀）")
    excludes: list[str] = Field(default_factory=list, description="用户追加排除规则（内置集自动叠加）")
    exclude_spec: dict[str, Any] | None = Field(
        default=None, description="批次② 结构化过滤条件；存在时忽略 excludes（spec 为唯一事实源）"
    )


class TaskDryRunRequest(_Base):
    """E36 任务域 dry-run 预览请求：字段均可选，缺省取任务存量值（支撑「源集合变更后重新预览」）。

    与 :class:`DryRunRequest`（E31 无实体向导内预览，sources 必填）的差异即在此——
    任务实体已在库中，允许空 body 全量取存量值重新预览。
    """

    sources: list[str] | None = Field(default=None, description="展示形态备份源；None=取任务存量值")
    excludes: list[str] | None = Field(default=None, description="展示形态/纯 glob 排除；None=取任务存量值")
    exclude_spec: dict[str, Any] | None = Field(
        default=None, description="批次② 结构化过滤条件（未保存改动）；None=取任务存量 spec"
    )


class DryRunItem(_Base):
    """E31 / E36 预览条目。

    ``type`` 取 ``dir`` / ``file``：restic ``--verbose`` 用**路径尾斜杠**标记目录，
    解析层已归一到 ``FileRecord.type``。缺了它前端只能靠「有子路径」反推层级，
    **空目录**（备份仓库的 ``data/00``… 分片目录大多为空）就会被误算成文件。
    """

    path: str
    display_path: str
    type: str = Field(default="file", description="dir | file（restic verbose_status 的尾斜杠归一）")
    size: int | None = None


class DryRunWarning(_Base):
    """E31 黄色警告（伪文件系统目录名，不拦截）。"""

    type: str = "pseudo_fs_name"
    path: str


# ---------------------------------------------------------------- E33 容器文件系统浏览


class FsItem(_Base):
    """E33 文件系统条目（容器内绝对路径，无展示形态换算）。"""

    name: str
    type: str = Field(description="dir|file|other")
    path: str = Field(description="容器内绝对路径（如 /repo/sub）")
    is_mountpoint: bool = Field(default=False, description="该条目是否为容器挂载点（bind/volume）")


class FsBrowseResponse(_Base):
    """E33 浏览响应；``mounts`` 仅为根目录请求时回（前端做挂载点快跳）。"""

    path: str = Field(description="容器内绝对路径")
    items: list[FsItem] = Field(default_factory=list)
    mounts: list[str] = Field(default_factory=list, description="容器挂载点列表（path=/ 时返回）")
    next_cursor: str | None = None


class FsMkdirRequest(_Base):
    """E33 创建目录请求（容器视角，单级）。"""

    path: str = Field(description="待创建的容器内绝对路径（父目录必须已存在）")


class FsMkdirResponse(_Base):
    """E33 创建目录响应。

    ``created=false`` 表示目标目录**已存在**（幂等，不算错误）——前端据此提示
    「目录已存在，已直接选中」，而不是报红。
    """

    path: str = Field(description="已存在的容器内绝对路径（规范化后）")
    created: bool = Field(description="本次是否真的创建了新目录")


class DryRunResponse(_Base):
    """E31 预览响应（不强制拦截，warnings 仅提示）。"""

    items: list[DryRunItem] = Field(default_factory=list)
    warnings: list[DryRunWarning] = Field(default_factory=list)
    truncated: bool = False
    error: str | None = None


# ---------------------------------------------------------------- E32 内置排除集（只读暴露）


class ExclusionsResponse(_Base):
    """E32 内置排除集的**只读**暴露（D8：单一事实来源，前端不得自行复制一份规则）。

    ``items`` / ``core`` 为容器前缀形态（wire 兼容，保留）；``display_items`` /
    ``display_core`` 为剥前缀后的展示形态（前端优先使用，无需自行过 PathMapper）。
    """

    items: list[str] = Field(default_factory=list, description="当前模式完整内置排除集（容器内路径）")
    core: list[str] = Field(default_factory=list, description="语义核心子集（与模式无关，供 UI 分组）")
    display_items: list[str] = Field(default_factory=list, description="items 的展示形态（剥前缀，原路径语义）")
    display_core: list[str] = Field(default_factory=list, description="core 的展示形态（剥前缀，原路径语义）")
    total: int = 0


# ---------------------------------------------------------------- E34–E36 备份任务（选项② R-026）


class TaskOut(_Base):
    """E34' 任务详情/编辑预填（全字段 + display 派生，tag 只读露出）。

    ``repo_id`` 作用域外键（E7）、``dry_run_pending`` 首跑门标记；
    ``legacy_policy_id`` 恒为 None（greenfield 无存量策略可继承，
    DDL 无此列——字段仅为 wire 兼容占位）。
    """

    id: int
    repo_id: str = "default"
    name: str
    enabled: bool = True
    tag: str
    sources: list[str] = Field(default_factory=list)
    display_sources: list[str] = Field(
        default_factory=list, description="sources 的展示形态（剥前缀，原路径语义；UI 直消费）"
    )
    excludes: list[str] = Field(default_factory=list)
    display_excludes: list[str] = Field(
        default_factory=list, description="excludes 的展示形态（剥前缀，原路径语义；UI 直消费）"
    )
    exclude_spec: dict[str, Any] | None = Field(
        default=None, description="批次② 结构化过滤条件（原样回传；None=旧任务，回落 excludes）"
    )
    keep_last: int = 0
    keep_hourly: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0
    # A2 时间线保留（restic duration，如 1y5m7d2h；空串 = 未启用）与 --keep-tag 清单
    keep_within: str = ""
    keep_within_hourly: str = ""
    keep_within_daily: str = ""
    keep_within_weekly: str = ""
    keep_within_monthly: str = ""
    keep_within_yearly: str = ""
    keep_tags: list[str] = Field(default_factory=list)
    group_by: str = "host,paths,tags"
    cron: str | None = None
    one_file_system: bool = False
    compression: str = "auto"
    check_sample_ratio: float = 0.02
    prune_interval_days: int = 7
    limit_up: int = 0      # KiB/s，0=不限（DDL 列 limit_up_kibps）
    limit_down: int = 0    # KiB/s，0=不限（DDL 列 limit_down_kibps）
    dry_run_pending: bool = False
    last_run_at: str | None = None
    # C3 调度触发失败后的最大重试次数
    retry_count: int = RETRY_COUNT_DEFAULT
    # C4 多目的地：备份成功后镜像到的仓库 id（空列表 = 单目的地）
    mirror_repo_ids: list[str] = Field(default_factory=list)
    legacy_policy_id: int | None = None   # greenfield 恒 None（无 legacy_policy_id 列）
    created_at: str
    updated_at: str


class TaskCardOut(_Base):
    """E34 任务卡片（派生状态聚合：见增量设计 §1.2 派生规则）。

    ``status`` 词表：paused（enabled=0）/ running / pending / success / failed /
    interrupted / cancelled / never_run；``last_result`` 为最近一次 backup 终态。

    P1 新增两个**可为 null** 的统计字段（UI 全量改造方案 §4.5.2 卡片「上次：相对时间
    + 耗时」与「快照数」）：``last_duration_ms`` 与 ``last_result`` 指向同一次运行；
    ``snapshot_count`` 为 ``null`` 表示**此刻不可知**（仓库未配置/快照列表探测失败），
    与 ``0``（任务确实还没产出快照）严格区分。
    """

    id: int
    name: str
    enabled: bool = True
    tag: str
    cron: str | None = None
    status: str = Field(
        description="派生状态：paused/running/pending/success/failed/interrupted/cancelled/never_run"
    )
    last_run_at: str | None = None
    last_result: str | None = None
    # P1：与 last_result 同一次运行的耗时（finished_at - started_at）；无终态历史为 null
    last_duration_ms: int | None = Field(default=None, ge=0, description="上次运行时长（毫秒）")
    # P1：该任务 tag 的快照数（一次快照列表按 tag 分组）；不可知为 null，无快照为 0
    snapshot_count: int | None = Field(default=None, ge=0, description="该任务快照数；不可知为 null")
    # P7 D2：任务级限速（KiB/s，0=不限）；状态条只读展示（owner 2026-10-22 任务级裁决：
    # 备份进程启动时读入 restic argv，运行中不可改，故前端仅只读展示、不提供运行中入口）
    limit_up: int = Field(default=0, ge=0, description="上传限速 KiB/s；0=不限")
    limit_down: int = Field(default=0, ge=0, description="下载限速 KiB/s；0=不限")
    next_run_at: str | None = Field(default=None, description="cron 推导；null=仅手动或已暂停")
    running_job_id: str | None = Field(default=None, description="有活跃 job 时非空（卡片内联进度入口）")
    dry_run_pending: bool = Field(default=False, description="true 时 UI 拦截 Start（R-026-08）")
    prune_due: bool = Field(default=False, description="保留清理到期提示（只提示，不自动执行，N3）")
    updated_at: str


class TaskUpsertRequest(_Base):
    """E35 POST / E35' PUT 请求体（全量语义）。

    **无 tag 字段**——服务端生成只读 ``task-<id>``；``one_file_system=null`` 按模式缺省。
    """

    name: str = Field(min_length=1, max_length=120, description="任务名称（必填非空）")
    enabled: bool = True
    sources: list[str] = Field(default_factory=list, description="备份源（展示形态，如 /etc）")
    excludes: list[str] = Field(default_factory=list, description="用户追加排除（展示形态/纯 glob）")
    exclude_spec: dict[str, Any] | None = Field(
        default=None,
        description="批次② 结构化过滤条件（camelCase）；存在时 excludes 置空、以 spec 为唯一事实源",
    )
    keep_last: int = 0
    keep_hourly: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0
    # A2 时间线保留（restic duration 形态，如 1y5m7d2h；空串 = 未启用）与
    # --keep-tag 清单（每元素一条 flag，多值 OR 语义；空列表 = 未启用）
    keep_within: str = ""
    keep_within_hourly: str = ""
    keep_within_daily: str = ""
    keep_within_weekly: str = ""
    keep_within_monthly: str = ""
    keep_within_yearly: str = ""
    keep_tags: list[str] = Field(default_factory=list)
    group_by: str = "host,paths,tags"
    cron: str | None = None
    one_file_system: bool | None = None   # None=按模式取缺省（sources 模式缺省 true）
    compression: str = "auto"
    check_sample_ratio: float = 0.02
    prune_interval_days: int = 7
    limit_up: int = 0
    limit_down: int = 0
    # C3 调度触发失败后的最大重试次数（0 = 不重试；手动触发恒不重试）
    retry_count: int = RETRY_COUNT_DEFAULT
    # C4 多目的地：镜像仓 id 列表（数量上限由 pydantic 直接 422；存在性/自指由路由层校验）
    mirror_repo_ids: list[str] = Field(default_factory=list, max_length=MIRROR_REPO_IDS_MAX)


# ---------------------------------------------------------------- E17/E18 快照


class SnapshotOut(_Base):
    """E17 快照条目（paths 已映射为原路径语义）。"""

    id: str
    time: str | None = None
    host: str | None = None
    tags: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    size: int | None = None


class SnapshotFileOut(_Base):
    """E18 文件树条目（display_path 为原路径语义）。"""

    name: str
    type: str = "file"
    path: str
    display_path: str
    size: int | None = None
    mtime: str | None = None


class SnapshotFilesResponse(_Base):
    """E18 文件树响应（游标分页）。"""

    snapshot_id: str
    path: str
    items: list[SnapshotFileOut] = Field(default_factory=list)
    next_cursor: str | None = None
    truncated: bool = False


class SnapshotFileSearchRequest(_Base):
    """P3 快照内文件搜索请求（恢复向导 select-files 的搜索框，R6）。

    ``query`` 为文件名/路径子串（大小写不敏感，对**展示路径**匹配）；纯空白由路由层
    拒 422——空关键词等价于「全量拉取」，那正是本端点要禁止的用法（V-3）。
    """

    query: str = Field(min_length=1, max_length=SNAPSHOT_SEARCH_QUERY_MAX_LENGTH)
    limit: int = Field(default=SNAPSHOT_FILES_DEFAULT_LIMIT, ge=1, le=SNAPSHOT_FILES_MAX_LIMIT)
    cursor: str | None = None


class SnapshotFileSearchResponse(_Base):
    """P3 搜索响应：游标语义与 E18 **同口径**（``decode/encode_cursor`` 同一套）。

    ``total`` 是**命中总数**（不是本页条数）——搜索框要显示「N 个结果」，前端不
    该靠翻页累加去猜。``truncated`` 与 E18 同源：restic 输出被截断时为真，此时
    total/items 只是已收流部分的结论。
    """

    snapshot_id: str
    query: str
    items: list[SnapshotFileOut] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None
    truncated: bool = False


# ---------------------------------------------------------------- E21 forget 预览（R-016）


class ForgetPreviewRequest(_Base):
    """E21 forget 预览请求（R-016：策略参数 → forget --dry-run --json）。

    至少一个保留规则必须显式给出——无保留规则的 forget 会删除组内**全部**快照
    （G3 服务端兜底：preview 与 E12 执行两侧同拒 422）。计数档（keep_last/…）
    值域 ``≥1``：0 = 不启用该维度（``--keep-last 0`` 的 restic 语义歧义，禁止显式传 0）。

    A2 时间线档（keep_within/keep_within_*）：restic duration 形态（y/m/d/h，
    如 ``1y5m7d2h``），空串/None = 未启用；keep_tags：``--keep-tag`` 清单
    （每元素一条 flag，多值 OR 语义），空列表 = 未启用。
    """

    tag: str | None = Field(default=None, description="按 tag 限定 forget 范围（如任务 tag task-1）；缺省作用于全仓库")
    group_by: str = Field(default="host,paths,tags", description="分组维度（restic --group-by）")
    keep_last: int | None = Field(default=None, ge=1)
    keep_hourly: int | None = Field(default=None, ge=1)
    keep_daily: int | None = Field(default=None, ge=1)
    keep_weekly: int | None = Field(default=None, ge=1)
    keep_monthly: int | None = Field(default=None, ge=1)
    keep_yearly: int | None = Field(default=None, ge=1)
    keep_within: str | None = Field(default=None, description="时间线保留：最新快照指定时长内的全部快照（如 1y5m7d2h）")
    keep_within_hourly: str | None = Field(default=None, description="时间线保留：时长内的 hourly 快照")
    keep_within_daily: str | None = Field(default=None, description="时间线保留：时长内的 daily 快照")
    keep_within_weekly: str | None = Field(default=None, description="时间线保留：时长内的 weekly 快照")
    keep_within_monthly: str | None = Field(default=None, description="时间线保留：时长内的 monthly 快照")
    keep_within_yearly: str | None = Field(default=None, description="时间线保留：时长内的 yearly 快照")
    keep_tags: list[str] = Field(default_factory=list, description="--keep-tag 清单（命中任一的快照保留）")
    prune: bool = Field(default=True, description="确认执行后是否随 forget --prune 回收数据（预览本身恒只读）")


class ForgetPreviewSnapshot(_Base):
    """E21 预览中的单条快照（paths 已映射为原路径语义，与 E17 口径一致）。"""

    id: str
    time: str | None = None
    host: str | None = None
    tags: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)


class ForgetPreviewResponse(_Base):
    """E21 预览响应：将被裁剪的快照列表 + 参数回显（确认提交 E12 与预览同源）。

    ``params`` 为回显的执行参数（tag/group_by/keep_*/prune），前端确认门第三步
    提交 E12 时原样透传，保证「预览与实际结果一致」（R-016 验收口径）。
    """

    remove: list[ForgetPreviewSnapshot] = Field(default_factory=list)
    remove_count: int = 0
    keep_count: int = 0
    group_count: int = 0
    params: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False


# ---------------------------------------------------------------- R-016 独立回收预览（restic prune --dry-run）

class PrunePreviewResponse(_Base):
    """独立回收预览响应（restic ``prune --dry-run`` 只读探测）。

    只回「会被删除的 pack 数 / 可释放字节数」与原始输出行，供前端在确认门前展示
    「仓库里有没有需要 prune 的数据」。``clean`` 为真表示无需回收（无任何未引用数据块）。
    """

    would_remove_packs: int = 0
    freed_bytes: int | None = None
    freed_text: str | None = None
    clean: bool = False
    lines: list[str] = Field(default_factory=list)




class TagSnapshotsRequest(_Base):
    """A3 快照打/去标签请求（restic ``tag --add/--remove``）。

    ``snapshot_ids`` 与 ``tag_filter``/``host`` 二选一提供目标范围——两者皆空会命中
    仓库内**全部**快照（restic ``tag`` 无 ID 即按过滤条件改全部），属高危操作，
    路由层强制拒 422；``add``/``remove`` 至少给一个（restic 端 "nothing to do!"，
    拼装层前移拒 422）。
    """

    snapshot_ids: list[str] = Field(default_factory=list, description="目标快照 ID 列表（与 tag/host 过滤二选一）")
    add: list[str] = Field(default_factory=list, description="要添加的标签（打标签；逗号分隔或数组）")
    remove: list[str] = Field(default_factory=list, description="要移除的标签（去标签；逗号分隔或数组）")
    tag_filter: list[str] | None = Field(default=None, description="按现有 tag 过滤目标快照（--tag 选择语义，可多选）")
    host: str | None = Field(default=None, description="按 host 过滤目标快照（--host）")


class TagChanged(_Base):
    """单条被改标签的快照：旧 id → 新 id（restic ``tag`` 改标签会生成新快照对象）。"""

    old_snapshot_id: str
    new_snapshot_id: str


class TagSnapshotsResponse(_Base):
    """A3 打/去标签响应：改动条数 + 逐条新旧 id 映射（restic ``tag`` 的 changed/summary 输出）。"""

    changed_count: int = 0
    changed: list[TagChanged] = Field(default_factory=list)
    truncated: bool = False


# ---------------------------------------------------------------- E23/E24 通知（R-018）


class NotifyConfigResponse(_Base):
    """E23 通知配置读取。

    敏感字段（healthchecks/webhook URL 内嵌令牌、SMTP 邮箱密码）只回 ``*_set`` 布尔，
    不回明文（与 E06 cred.* 同口径）；其余字段回现值（未配置回缺省）。
    """

    healthchecks_url_set: bool = False
    webhook_url_set: bool = False
    webhook_template: str = ""
    meow_nickname: str = ""
    meow_msg_type: MeowMsgType = MEOW_MSG_TYPE_DEFAULT      # 消息显示类型（text/html）
    meow_html_height: int = MEOW_HTML_HEIGHT_DEFAULT        # 仅 msgType=html 生效（像素）
    meow_url: str = ""                                      # 通知跳转链接（可选，API §3.2）
    meow_img_url: str = ""                                  # 通知图标 URL（可选，建议 216×216 PNG）
    meow_plan: MeowPlan = MEOW_PLAN_DEFAULT                 # 套餐等级（限流档位）
    on_success: bool = False
    skipped_threshold: int = 100
    webhook_redact: bool = False
    meow_redact: bool = True
    # C2 SMTP 邮件（密码只回 mail_password_set）
    mail_host: str = ""
    mail_port: int = MAIL_PORT_DEFAULT
    mail_username: str = ""
    mail_password_set: bool = False
    mail_from: str = ""
    mail_to: str = ""
    mail_security: MailSecurity = "starttls"
    mail_redact: bool = False


class NotifyConfigPatch(_Base):
    """E23 通知配置写入。

    敏感字段三态（``healthchecks_url`` / ``webhook_url`` / ``mail_password``）：
    ``None`` = 不变、``""`` = 清除、非空 = 设置；其余字段 ``None`` = 不变。
    """

    healthchecks_url: str | None = Field(default=None, max_length=2048)
    webhook_url: str | None = Field(default=None, max_length=2048)
    webhook_template: str | None = Field(default=None, max_length=4000)
    meow_nickname: str | None = Field(default=None, max_length=128)
    meow_msg_type: MeowMsgType | None = None
    meow_html_height: int | None = Field(
        default=None, ge=MEOW_HTML_HEIGHT_MIN, le=MEOW_HTML_HEIGHT_MAX
    )
    meow_url: str | None = Field(default=None, max_length=2048)
    meow_img_url: str | None = Field(default=None, max_length=2048)
    meow_plan: MeowPlan | None = None
    on_success: bool | None = None
    skipped_threshold: int | None = Field(default=None, ge=0, le=1_000_000)
    webhook_redact: bool | None = None
    meow_redact: bool | None = None
    # C2 SMTP 邮件
    mail_host: str | None = Field(default=None, max_length=255)
    mail_port: int | None = Field(default=None, ge=1, le=65535)
    mail_username: str | None = Field(default=None, max_length=255)
    mail_password: str | None = Field(default=None, max_length=512)
    mail_from: str | None = Field(default=None, max_length=255)
    mail_to: str | None = Field(default=None, max_length=2000)
    mail_security: MailSecurity | None = None
    mail_redact: bool | None = None


class NotifyTestChannel(_Base):
    """E24 单渠道测试结果（禁止静默：失败必须带原因）。"""

    channel: str = Field(description="healthchecks | webhook | meow | mail")
    ok: bool
    reason: str | None = None


class NotifyTestResponse(_Base):
    """E24 测试消息响应：整体 ok + 逐渠道结果；未配置任何渠道时 ok=False + reason。"""

    ok: bool
    latency_ms: int = 0
    channels: list[NotifyTestChannel] = Field(default_factory=list)
    reason: str | None = None


# ---------------------------------------------------------------- E19 恢复


class RestoreRequest(_Base):
    """E19 恢复请求：默认恢复到时间戳子目录（零覆盖语义），原位恢复为 R-015 扩展分支。

    ``paths`` 为 B1 文件级恢复入参（**展示形态**，如 ``/etc``、``/home/u/x``）；
    缺省/空数组 = 整快照恢复（兼容既有行为）。服务端逐条还原为容器形态并过
    :class:`PathMapper` 白名单校验（越界 400 ``PATH_OUT_OF_PREFIX``）。

    R-015（红线 S7 v1.4）：``overwrite_mode`` **仅原位恢复分支可用**——时间戳
    子目录路径出现即 422（该路径的 ``--replace_existing=if-changed`` 由 JobRunner
    固定注入，界面无入口）；原位分支下取值 **``never`` 或 ``always``**
    （A7 Duplicati 布尔勾选：默认不勾 = never 跳过已存在文件，勾选 = always
    覆盖；``if-changed`` 是时间戳路径的服务端注入值，原位分支不接受）。
    A7 起确认门放松为单次确认，**不再有** ``keyword`` 字段。
    """

    snapshot_id: str = Field(min_length=8, max_length=64)
    target: str
    paths: list[str] = Field(
        default_factory=list,
        description="要恢复的文件/目录（展示形态）；缺省或空 = 整快照恢复",
    )
    overwrite_mode: str | None = Field(
        default=None,
        description="原位恢复覆盖模式（仅接受 never/always；时间戳路径不得传）",
    )


class RestoreResponse(_Base):
    """E19 响应（202）：服务端计算的时间戳子目录（原位分支为给定 target 本身）。"""

    job_id: str
    restore_target: str


# ---------------------------------------------------------------- E20 原位恢复覆盖预览（R-015）


class RestorePreviewRequest(_Base):
    """E20 覆盖预览请求：比对快照内容与目标磁盘现状（只读）。"""

    snapshot_id: str = Field(min_length=8, max_length=64)
    target: str = Field(description="原位恢复目标（容器形态；须命中该快照的备份路径）")
    limit: int = Field(
        default=RESTORE_PREVIEW_DEFAULT_LIMIT, ge=1, le=RESTORE_PREVIEW_MAX_LIMIT
    )
    cursor: str | None = Field(default=None, description="上一页末条目的不透明游标")


class RestorePreviewItem(_Base):
    """E20 覆盖清单条目（``path`` 容器形态，``display_path`` 为原路径语义）。"""

    path: str
    display_path: str
    snapshot_mtime: str | None = None
    disk_mtime: str | None = None
    snapshot_size: int | None = None
    disk_size: int = 0


class RestorePreviewResponse(_Base):
    """E20 响应：覆盖清单（游标分页）+ 覆盖总量（预览时点口径）。

    ``total_bytes`` 语义（S-1 审查 N-2 点明）：**将写入磁盘的字节**——按快照侧
    ``snapshot_size`` 求和（restic 缺失该字段的条目计 0），**不是**「被覆盖文件的
    磁盘侧大小之和」（后者用 ``disk_size``，本响应不提供）。UI 呈现口径应为
    「本次原位恢复将写入约 N 字节」。
    """

    overwrite_count: int = 0
    items: list[RestorePreviewItem] = Field(default_factory=list)
    total_bytes: int = 0
    next_cursor: str | None = None
    truncated: bool = False


# ---------------------------------------------------------------- E29/E30 仓库探测


class RepoTestResponse(_Base):
    """E29 仓库连通性测试（失败必须给出可操作原因，禁止静默）。"""

    ok: bool
    latency_ms: int = 0
    reason: str | None = None
    # 结构化失败分类（FAILURE_KIND_*；成功时 None）——前端据此判定该给
    # 「初始化」还是「重设密码」，不再依赖中文文案匹配
    failure_kind: str | None = None


class RepoCapacity(_Base):
    """E30 仓库容量（探测失败时字段为 null）。"""

    size: int | None = None
    snaps: int | None = None


class RepoStatusResponse(_Base):
    """E30 仓库状态（仪表盘状态卡 + 锁残留醒目提示数据源）。

    ``configured=False`` 表示 ``repo.url`` 尚未填写——UI 方案 L3（QA 提出）：
    「未配置」与「连接失败」分色（warning / error），二者不该同为一个红叉。
    """

    configured: bool = True
    connected: bool = False
    locked: bool = False
    capacity: RepoCapacity = Field(default_factory=RepoCapacity)
    last_error: str | None = None
    # 结构化失败分类（FAILURE_KIND_*；connected=True 时为 None）——UI 判定
    # 「初始化 / 重设密码 / 解锁」哪个动作可用的唯一依据（不靠匹配 last_error 文案）
    failure_kind: str | None = None


class RepoKeyOut(_Base):
    """仓库密钥身份的一条记录（``restic key list --json`` 的展示子集）。

    只含展示必需字段：``data``（master key 密文）与 ``salt/N/r/p``（KDF 参数）
    **不进入 API**——它们是暴破难度指纹，对用户没有操作价值。
    """

    id: str  # 8 位短 id（与 restic key list 表格列一致）
    id_full: str  # 完整 64 位 hex（供复制到 CLI 排障）
    user: str
    host: str
    created: str
    # restic 按子进程 TZ 渲染的无时区字符串 → 带上该 TZ 标注（restic.tz = config.timezone
    # → settings.tz 回退，默认 UTC），否则用户看到的建库时间会与自己本地时间差出时区偏移
    # 且无从判断（不做换算，C8）
    created_tz: str = "UTC"
    current: bool = False
    neutral: bool = False  # user/host 均为中性值（svc-backup@repo）


class RepoKeysOut(_Base):
    """仓库密钥身份（只读）。

    ``overall`` 四态见 ``services/repo_keys.KEY_OVERALL_*``；``probe_error`` 非空时
    ``keys`` 为空，前端必须显示原因（不留空白）。
    """

    repo_id: str
    overall: str = "unknown"
    keys: list[RepoKeyOut] = Field(default_factory=list)
    probe_error: str | None = None


class ReportBucketOut(_Base):
    """R-024 报表单日桶（按配置时区；无运行日 total=0、ratio=null）。"""

    date: str
    total: int
    success: int
    failed: int
    ratio: float | None


class BackupSuccessReportOut(_Base):
    """R-024 备份成功率报表（E37）。"""

    days: int
    timezone: str
    from_date: str
    to_date: str
    buckets: list[ReportBucketOut]
    total: int
    success: int
    failed: int
    ratio: float | None


# ---------------------------------------------------------------- E39 时间同步（时区手动设置 + NTP 对时）


class NtpProbeOut(_Base):
    """NTP 探测结果（失败必须带 reason 机器码，禁止静默）。"""

    available: bool
    server: str | None
    offset_ms: float | None
    rtt_ms: float | None
    reason: str | None


class SystemTimeOut(_Base):
    """E39 服务器时间快照：UTC 时刻 + 生效时区本地时间 + NTP 偏差。

    ``epoch_ms`` 供前端以服务器时刻为基准做实时时钟（避免用浏览器本机时间——
    本机时钟漂移会让「服务器时间」显示与后端实际判定脱节）。
    ``ntp_servers`` 为本次探测的生效链（主备配置或内置默认链），供前端展示。
    """

    server_time: str
    timezone: str
    local_time: str
    utc_offset: str
    epoch_ms: int
    ntp_servers: list[str]
    ntp: NtpProbeOut


class TimeSyncOut(_Base):
    """E39 同步结论：``applied=False`` 时必须带 reason（未应用/失败原因）。"""

    ok: bool
    applied: bool
    offset_ms: float | None
    server: str | None
    reason: str | None


class SystemRuntimeOut(_Base):
    """部署形态与浏览根快照（设计方案 §3.2 单一事实源）。

    ``browse_root`` 是 UI 允许选源与后端接受写入路径的**同一来源**——前端据此决定树根
    （docker→``/sources``，host→``/``），不再自行写死。``deny_prefixes`` 仅在 host 模式非空
    （docker 的浏览面已被 ``/sources`` 白名单界定），供前端在源树中隐藏不可选区域；
    真正的安全拦截在后端 :meth:`app.services.path_mapper.PathMapper.validate`。

    ``restore_root`` / ``inplace_root`` 是**恢复链路**的两个生效根（owner 2026-10-25 裁决
    Q1=A，同样由部署形态派生）：docker → ``/restore`` 与 ``/sources``；host → 两者均为 ``/``。
    前者是「选择位置」流的树根与时间戳子目录的落点父目录，后者是原位目标
    「展示形态 → 容器形态」换算的前缀（host 为 ``/`` 时恒等，不再叠加前缀）。
    """

    deploy_mode: str
    browse_root: str
    sources_root: str
    restore_root: str
    inplace_root: str
    deny_prefixes: list[str]


class RepoOut(_Base):
    """R-023 仓库输出（repos 表事实源；type 派生不落库，passfile_status 只读派生）。"""

    id: str
    name: str
    type: str
    url: str | None
    host_tag: str | None
    url_source: str
    host_tag_source: str
    passfile_status: str
    created_at: str


class ArchivedRepoOut(_Base):
    """归档仓库（仍可通过「恢复」一键还原：行、凭据、密码文件都还在）。"""

    repo_id: str
    name: str
    type: str
    url: str | None
    archived_at: str
    passfile_status: str


class RepoTombstoneOut(_Base):
    """墓碑（归档记录）：只记「这里曾经有过什么」，**不含密码与凭据**。

    ``data_destroyed`` = 该仓库走过 ``purge_disk``（目录内容 + 密码文件已销毁）；
    ``released_at`` 非空 = ID 已被手动释放（新仓库可复用该 id）。
    """

    repo_id: str
    name: str
    type: str
    url_display: str | None
    archived_at: str
    snapshot_count: int | None
    data_destroyed: bool
    released_at: str | None


class RepoRepointRequest(_Base):
    """把引用某 rclone remote 的仓库批量改指向另一个 remote（受控出口）。

    **只改 url 前缀**，不碰数据、不碰凭据；目标是删除被引用 remote 前的那条出路。
    """

    new_remote: str


class RepoRepointOut(_Base):
    """改指向结果：动过的仓库清单（id + 新地址）。"""

    old_remote: str
    new_remote: str
    repos: list[dict[str, str]] = Field(default_factory=list)


class RcloneArchiveItemOut(_Base):
    """归档的 rclone 段（**零凭据回显**：只给键名清单，永不回值）。"""

    remote: str
    type: str
    deleted_at: str
    used_by_repos: int
    keys: list[str] = Field(default_factory=list)


class RcloneArchivesOut(_Base):
    """归档页「rclone 配置归档」分区数据（最近删除在前）。"""

    items: list[RcloneArchiveItemOut] = Field(default_factory=list)


class ArchiveListOut(_Base):
    """归档页「仓库归档」分区数据：可恢复的归档仓库 + 只剩记录的墓碑。"""

    repos: list[ArchivedRepoOut] = Field(default_factory=list)
    tombstones: list[RepoTombstoneOut] = Field(default_factory=list)


class RepoReleaseRequest(_Base):
    """释放归档仓库 ID：**必须原样输入仓库 id** 才算确认（不可逆地放弃找回）。"""

    confirm_id: str


class RcloneRemotesOut(_Base):
    """A1 rclone 只读探测结果（Q17 档位：不生成、不编辑、不展示 conf 内凭据）。

    ``available`` = rclone 二进制可用且探测成功（与 conf 是否存在无关）；
    ``conf_present`` = conf 文件真实存在；``remotes`` = 远端名清单（去行尾 ``:``）。
    """

    available: bool
    conf_present: bool
    conf_path: str | None
    remotes: list[str]
    reason: str | None
    conf_encrypted: bool = False
    conf_locked: bool = False


class RcloneConfigItemOut(_Base):
    """rclone conf 单段只读视图（**凭据零回显**：只给非凭据参数 + ``has_*`` 布尔 + 形态布尔）。

    ``credential_obscured``：凭据键 → 落盘形态是否 obscure 产物；``None`` = 形态未知
    （非本服务写入 / conf 被外部改过），就地转换会跳过该键（方案 A，owner 2026-10-27）。
    """

    remote: str
    type: str
    managed: bool  # type 在注册表内 → 可字段级编辑
    params: dict[str, str]  # 非凭据参数（非 managed 段恒空）
    has_credentials: dict[str, bool]  # 凭据键 → 是否已配置
    credential_obscured: dict[str, bool | None] = {}  # 凭据键 → 落盘形态（None=未知）
    used_by_repos: int = Field(
        default=0,
        description="直接引用本 remote 的仓库数（含归档仓库）；>0 时禁止删除（删除保护方案 P0，裁决 1/3）",
    )


class RepoUsageMirrorRef(_Base):
    """占用明细里「把我当镜像目的地的任务」引用（不含凭据与地址）。"""

    id: int
    name: str


class RepoUsageOut(_Base):
    """仓库占用明细（删除保护方案 P1，裁决 5/6）：镜像引用 + 快照总数。

    ``snapshot_total`` 为 ``null`` 表示**此刻不可知**（仓库未配置 / 探测失败），
    前端必须显示「不可知」而非 0——0 会让用户误判「从没备过」而放心删除。
    """

    repo_id: str
    mirror_refs: list[RepoUsageMirrorRef] = Field(default_factory=list)
    snapshot_total: int | None = Field(default=None, ge=0)


class RcloneUsageRepoRef(_Base):
    """占用明细里的仓库引用（不含凭据与地址，避免扩大暴露面）。"""

    id: str
    name: str


class RcloneRemoteUsageOut(_Base):
    """rclone 远端占用明细（删除保护方案 P0，裁决 2）。

    ``repos``：直接引用该 remote 的仓库；``dependent_remotes``：以该 remote 为
    底层的 crypt 段名（传递依赖）；``crypt``：待查段自身是否 crypt。
    """

    remote: str
    repos: list[RcloneUsageRepoRef] = Field(default_factory=list)
    dependent_remotes: list[str] = Field(default_factory=list)
    crypt: bool = False


class RcloneConfigsOut(_Base):
    """rclone conf 全段查询快照。"""

    conf_path: str
    conf_present: bool
    configs: list[RcloneConfigItemOut]
    available: bool
    reason: str | None
    conf_encrypted: bool = False
    conf_locked: bool = False


class RcloneConfEncryptionOut(_Base):
    """rclone conf 加密三态（口令存在性只回布尔，口令明文永不回显）。"""

    conf_encrypted: bool
    conf_locked: bool
    password_present: bool
    conf_path: str
    pass_path: str


_ConfPassword = Annotated[SecretStr, StringConstraints(min_length=1, max_length=512)]


class _RcloneConfPasswordBase(_Base):
    """口令字段先包成 SecretStr：422 校验错误也不回显明文（S3）。"""

    @field_validator(
        "password", "current_password", "old_password", "new_password", mode="before", check_fields=False
    )
    @classmethod
    def _hide_password(cls, value: Any) -> Any:
        return SecretStr(value) if isinstance(value, str) else value


class RcloneConfEnableRequest(_RcloneConfPasswordBase):
    """启用 conf 加密；省略 / null = 后端生成 32 位口令并一次性回显。

    ``backup`` 控制是否把启用前的明文 conf 写到 <PASSFILE_ROOT>/conf-backup.conf：
    它是口令彻底丢失时唯一的恢复路径，关掉即等于「口令丢了就再也打不开」。
    """

    password: _ConfPassword | None = None
    backup: bool = True


class RcloneConfRekeyRequest(_RcloneConfPasswordBase):
    """更换 conf 加密口令（新旧口令均不得为空）。"""

    old_password: _ConfPassword
    new_password: _ConfPassword


class RcloneConfPasswordRequest(_RcloneConfPasswordBase):
    """停用 / 解锁 conf 加密：按端点使用 ``current_password`` / ``password``。"""

    current_password: _ConfPassword | None = None
    password: _ConfPassword | None = None


class RcloneConfPasswordOnceOut(_Base):
    """enable / rekey 的一次性明文口令（只在本次响应体出现，不进日志 / 审计）。"""

    code: str = "ok"
    password_once: str | None = None


class RcloneConfigWriteRequest(_Base):
    """新增/编辑 rclone conf 段（字段随 ``type`` 变化，由生成器校验）。

    ``params`` 只接受注册表内已知键（未知键 422，不开自由改键口）。

    落盘形态（裁决 17，2026-10-28 收敛）：**conf 里的凭据恒为 rclone 混淆产物**。
    真机取证发现「明文落盘」对 crypt/webdav 一律不可用（``failed to decrypt
    password: input too short…``），故不再提供目标形态选项；用户唯一需要声明的是
    **提交值本身的形态**：

    - ``value_obscured=False``（默认）：提交的是明文密码，服务端 ``--obscure``
      混淆一次后落盘；
    - ``value_obscured=True``：提交的**已是** rclone 混淆产物（从别处复制的串），
      原样落盘（``--no-obscure``），不二次混淆——二次混淆会让 rclone 换一套加密
      命名空间（不报错，但仓库看起来是空的）。

    ``store_form`` / ``declare_form`` 已删除（旧客户端多传会被 pydantic 忽略，
    行为等价「按明文提交、落盘混淆」，不会写出坏配置）。
    """

    type: str
    params: dict[str, str]
    value_obscured: bool = False


class RcloneConfigWriteOut(_Base):
    """写操作结果（**凭据零回显**）。"""

    remote: str
    type: str
    conf_path: str
    action: str  # create | update | delete
    remotes_after: list[str]


class RepoCreateRequest(_Base):
    """R-023 新建仓库请求（url/host_tag 可空，日后再补）。

    ``id`` 会被拼进密码文件路径 ``<passfile_root>/<id>/password``，故**必须**过
    :data:`REPO_ID_PATTERN`（不含 `.` 与 `/`，杜绝 `..` 路径穿越 → 任意文件读写）。

    A8：``id`` 改为**可选**——留空（None/空串）时由服务端生成
    ``r<YYMMDD>-<6 位小写 Base32 随机>``（14 字符，如 ``r261022-k3f9x2``；日期段 UTC
    口径、字典序即时间序，Crockford 字母表剔除 i/l/o/u；2026-10-22 由
    ``repo-<26 位 ULID>`` 改版，存量 id 不受影响）；
    显式提供时仍按既有规则校验 + 存在性校验（409/422）。
    """

    id: str | None = Field(
        default=None,
        max_length=REPO_ID_MAX_LENGTH,
        pattern=REPO_ID_PATTERN,
        description="仓库标识：字母/数字开头，仅允许字母数字与 _ -；留空则服务端生成 r<YYMMDD>-xxxxxx",
    )
    name: str
    url: str | None = None
    host_tag: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _blank_id_to_none(cls, v: object) -> object:
        """A8：空串/纯空白视作「未提供」，交由服务端生成（而非被 pattern 拒为 422）。"""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class RepoIdSuggestion(_Base):
    """A8 服务端生成的候选仓库标识（``r<YYMMDD>-<6 位随机>``），供新建向导预填。

    不落库；用户可改可不改。留空提交时 ``POST /api/repos`` 仍会服务端生成（见 A8）。
    """

    id: str


class RepoPasswordRequest(_Base):
    """R-023 设置仓库密码（**仅在密码文件不存在时允许**）。

    - ``password`` 为空/None：后端生成强随机密码（长度 ``REPO_PASSWORD_LENGTH``，
      与前端「生成强随机密码」同口径），明文**一次性回显**（不落库、不落日志）；
    - ``password`` 非空：用户自填（明文只经本次请求体，响应不回显）；
    - ``force``：密码文件已存在时也允许覆盖——**仅当仓库当前打不开**（密码错误 /
      未初始化 / 凭据无效）才被接受，仓库可正常打开时仍 422（应走密钥轮换）。
    """

    password: str | None = Field(default=None, max_length=512)
    force: bool = False


class RepoPasswordResponse(_Base):
    """R-023 设置仓库密码结果。

    ``password`` 仅在 ``generated=True`` 时回显一次；自填场景恒为 None（用户已知）。
    ``path`` 为容器内路径，供运维在宿主机挂载点定位该文件（与 config 报错口径一致）。
    """

    ok: bool
    generated: bool
    password: str | None = None
    path: str


class RepoUpdateRequest(_Base):
    """R-023 更新仓库请求（三态：None=不变 / ""=清除 / 非空=设置）。"""

    name: str | None = None
    url: str | None = None
    host_tag: str | None = None


class RepoKeyRotateRequest(_Base):
    """C4 仓库密钥轮换（restic key passwd）：在旧密码仍有效时改为新密码。

    - ``repo_id``：目标仓库（其口令文件须已配置，否则 422）；
    - ``new_password``：新密码（明文只经本次请求体，响应不回显）。
    """

    repo_id: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=512)


class KeyRotateResponse(_Base):
    """C4 密钥轮换结果（new_password 不回显，敏感）。"""

    ok: bool
    repo_id: str


class ManualCopyRequest(_Base):
    """C4 手动 copy（搬家场景）：把源仓库快照原样复制到目标仓库（增量，只传新快照）。

    - ``source_repo_id`` / ``target_repo_id`` 须为已存在且已配置地址与密码的仓库；
    - ``tags``：可选过滤（仅复制带这些标签的快照），与任务级镜像同口径。
    """

    source_repo_id: str = Field(min_length=1)
    target_repo_id: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class ManualCopyResponse(_Base):
    """C4 手动 copy 结果。

    ``ok`` 只代表 restic 进程退出码为 0，**不代表真的复制了东西**（源/目标同仓、
    快照已全部存在、``--tag`` 无匹配时 restic 同样返回 0）。真实增量在 ``copied``：

    - ``copied > 0``：本次新增的快照条数（界面显示「已复制 N 个」）；
    - ``copied == 0``：目标仓已包含全部待复制快照（界面必须说「未新增」，不说「复制完成」）；
    - ``copied is None``：复制成功但后置复核读不到目标仓列表（降级，不臆造数量）。

    ``log_name`` 是 restic 原始输出的留存**文件名**（所在目录 ``<data_dir>/logs/copy``
    属部署事实，按 S1 不经 API 暴露）；留存失败为 ``None``，不影响结论。
    """

    ok: bool
    source_repo_id: str
    target_repo_id: str
    exit_code: int
    message: str = ""
    copied: int | None = None
    target_total: int | None = None
    log_name: str | None = None


class ConfigExportRequest(_Base):
    """批次 E 配置导出：导出口令（至少 10 位，保护加密凭据段）。"""

    password: str = Field(min_length=10, max_length=256)


class ConfigImportRequest(_Base):
    """批次 E 配置导入：完整导出包 + 口令 + 覆盖开关（ConfirmGate 由前端负责）。"""

    pack: dict[str, Any] = Field(description='导出包 JSON 全文')
    password: str = Field(min_length=1, max_length=256)
    replace_existing: bool = False


class ConfigImportReport(_Base):
    """批次 E 导入报告（逐项可展示；reason 为用户可读中文）。"""

    repos: dict[str, Any]
    tasks: dict[str, Any]
    mirror_warnings: list[dict[str, str]]
    notify_updated: int
    creds_imported: int
