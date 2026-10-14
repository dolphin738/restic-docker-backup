"""API 请求/响应模型（§5.0 统一约定 + §5.1 端点明细）。

本批次覆盖：E01 登录、E02 登出、E03 me、E04 health、Bootstrap 取密码、E28 改密，
以及 config 键值的脱敏输出与分页壳（供后续批次复用）。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.constants import RESTORE_PREVIEW_DEFAULT_LIMIT, RESTORE_PREVIEW_MAX_LIMIT
from app.schemas.enums import ConfigSource, HostMountMode, JobStatus, JobTrigger, JobType, RunMode

T = TypeVar("T")


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
    """E03 会话信息（供四象限卡 US-G2）。"""

    authenticated: bool = True
    must_change_password: bool = Field(description="首启强制改密标志（admin_account.must_change）")
    mode: RunMode = Field(description="host=全盘挂载 / sources=数据根目录")
    host_mount: HostMountMode = Field(description="/host 挂载模式 ro|rw")
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
    """单个配置项输出：敏感项只回 ``has_value`` + ``source``，不回明文。"""

    key: str
    value: str | None = None
    has_value: bool = False
    source: ConfigSource = ConfigSource.UI
    sensitive: bool = False


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

    ``task_id`` 语义（选项② §1.2 决策二）：该 job 由哪个备份任务产生；legacy 行
    仅 ``policy_id``；删除任务后 ``task_id`` 经外键置空（行与日志保留）。
    """

    id: str
    type: JobType
    status: JobStatus
    trigger: JobTrigger
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
    policy_id: int | None = Field(default=None, description="关联策略 id（legacy；与 task_id 互斥）")
    task_id: int | None = Field(default=None, description="关联备份任务 id（与 policy_id 互斥，同供 422）")
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


class SourceBrowseResponse(_Base):
    """E05 目录浏览响应。"""

    path: str = Field(description="展示形态的当前路径（如 /etc）")
    items: list[SourceItem] = Field(default_factory=list)
    next_cursor: str | None = None


class DryRunRequest(_Base):
    """E31 首备扫描范围预览请求。"""

    sources: list[str] = Field(min_length=1, description="容器内路径（/host 或 /sources 前缀）")
    excludes: list[str] = Field(default_factory=list, description="用户追加排除规则（内置集自动叠加）")


class TaskDryRunRequest(_Base):
    """E36 任务域 dry-run 预览请求：字段均可选，缺省取任务存量值（支撑「源集合变更后重新预览」）。

    与 :class:`DryRunRequest`（E31 无实体向导内预览，sources 必填）的差异即在此——
    任务实体已在库中，允许空 body 全量取存量值重新预览。
    """

    sources: list[str] | None = Field(default=None, description="展示形态备份源；None=取任务存量值")
    excludes: list[str] | None = Field(default=None, description="展示形态/纯 glob 排除；None=取任务存量值")


class DryRunItem(_Base):
    """E31 预览条目。"""

    path: str
    display_path: str
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

    mode: RunMode
    items: list[str] = Field(default_factory=list, description="当前模式完整内置排除集（容器内路径）")
    core: list[str] = Field(default_factory=list, description="语义核心子集（与模式无关，供 UI 分组）")
    display_items: list[str] = Field(default_factory=list, description="items 的展示形态（剥前缀，原路径语义）")
    display_core: list[str] = Field(default_factory=list, description="core 的展示形态（剥前缀，原路径语义）")
    total: int = 0


# ---------------------------------------------------------------- E08–E11 策略

class PolicyOut(_Base):
    """E08 策略输出（API 字段名与 DDL 列名不同者见 PolicyService 换算）。"""

    id: int
    name: str
    is_default: bool = False
    enabled: bool = True
    tag: str | None = None
    sources: list[str] = Field(default_factory=list)
    display_sources: list[str] = Field(
        default_factory=list, description="sources 的展示形态（剥前缀，原路径语义；UI 直消费）"
    )
    excludes: list[str] = Field(default_factory=list)
    display_excludes: list[str] = Field(
        default_factory=list, description="excludes 的展示形态（剥前缀，原路径语义；UI 直消费）"
    )
    keep_last: int = 0
    keep_hourly: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0
    group_by: str = "host,paths,tags"
    cron: str | None = None
    one_file_system: bool = False
    compression: str = "auto"
    check_sample_ratio: float = 0.02
    monthly_full_day: int = 1
    prune_interval_days: int = 7
    limit_up: int = 0      # KiB/s，0=不限（DDL 列 limit_up_kibps）
    limit_down: int = 0    # KiB/s，0=不限（DDL 列 limit_down_kibps）
    last_run_at: str | None = None
    created_at: str
    updated_at: str


class PolicyUpsertRequest(_Base):
    """E09 POST / E10 PUT 请求体（``is_default`` 由服务端控制，不接受客户端指定）。"""

    name: str = "default"
    enabled: bool = True
    tag: str | None = None
    sources: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    keep_last: int = 0
    keep_hourly: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0
    group_by: str = "host,paths,tags"
    cron: str | None = None
    one_file_system: bool | None = None   # None=按模式取缺省（sources 模式缺省 true）
    compression: str = "auto"
    check_sample_ratio: float = 0.02
    monthly_full_day: int = 1
    prune_interval_days: int = 7
    limit_up: int = 0
    limit_down: int = 0


# ---------------------------------------------------------------- E34–E36 备份任务（选项② R-026）

class TaskOut(_Base):
    """E34' 任务详情/编辑预填（全字段 + display 派生，tag 只读露出）。

    与 :class:`PolicyOut` 的差异：``repo_id`` 作用域外键（E7）、``dry_run_pending``
    首跑门标记；``legacy_policy_id`` 恒为 None（greenfield 无存量策略可继承，
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
    keep_last: int = 0
    keep_hourly: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0
    group_by: str = "host,paths,tags"
    cron: str | None = None
    one_file_system: bool = False
    compression: str = "auto"
    check_sample_ratio: float = 0.02
    monthly_full_day: int = 1
    prune_interval_days: int = 7
    limit_up: int = 0      # KiB/s，0=不限（DDL 列 limit_up_kibps）
    limit_down: int = 0    # KiB/s，0=不限（DDL 列 limit_down_kibps）
    dry_run_pending: bool = False
    last_run_at: str | None = None
    legacy_policy_id: int | None = None   # greenfield 恒 None（无 legacy_policy_id 列）
    created_at: str
    updated_at: str


class TaskCardOut(_Base):
    """E34 任务卡片（派生状态聚合：见增量设计 §1.2 派生规则）。

    ``status`` 词表：paused（enabled=0）/ running / pending / success / failed /
    interrupted / cancelled / never_run；``last_result`` 为最近一次 backup 终态。
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
    keep_last: int = 0
    keep_hourly: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0
    group_by: str = "host,paths,tags"
    cron: str | None = None
    one_file_system: bool | None = None   # None=按模式取缺省（sources 模式缺省 true）
    compression: str = "auto"
    check_sample_ratio: float = 0.02
    monthly_full_day: int = 1
    prune_interval_days: int = 7
    limit_up: int = 0
    limit_down: int = 0


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


# ---------------------------------------------------------------- E21 forget 预览（R-016）

class ForgetPreviewRequest(_Base):
    """E21 forget 预览请求（R-016：策略参数 → forget --dry-run --json）。

    至少一个 ``keep_*`` 规则必须显式给出——无保留规则的 forget 会删除组内**全部**
    快照（G3 服务端兜底：preview 与 E12 执行两侧同拒 422）。规则值域 ``≥1``：
    0 = 不启用该维度（``--keep-last 0`` 的 restic 语义歧义，禁止显式传 0）。
    """

    tag: str | None = Field(default=None, description="按 tag 限定 forget 范围（如任务 tag task-1）；缺省作用于全仓库")
    group_by: str = Field(default="host,paths,tags", description="分组维度（restic --group-by）")
    keep_last: int | None = Field(default=None, ge=1)
    keep_hourly: int | None = Field(default=None, ge=1)
    keep_daily: int | None = Field(default=None, ge=1)
    keep_weekly: int | None = Field(default=None, ge=1)
    keep_monthly: int | None = Field(default=None, ge=1)
    keep_yearly: int | None = Field(default=None, ge=1)
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


# ---------------------------------------------------------------- E23/E24 通知（R-018）

class NotifyConfigResponse(_Base):
    """E23 通知配置读取。

    敏感 URL 字段（healthchecks/webhook，内嵌令牌）只回 ``*_set`` 布尔，不回明文
    （与 E06 cred.* 同口径）；其余字段回现值（未配置回缺省）。
    """

    healthchecks_url_set: bool = False
    webhook_url_set: bool = False
    webhook_template: str = ""
    meow_nickname: str = ""
    on_success: bool = False
    skipped_threshold: int = 100
    webhook_redact: bool = False
    meow_redact: bool = True


class NotifyConfigPatch(_Base):
    """E23 通知配置写入。

    敏感 URL 字段三态：``None`` = 不变、``""`` = 清除、非空 = 设置；
    其余字段 ``None`` = 不变。
    """

    healthchecks_url: str | None = Field(default=None, max_length=2048)
    webhook_url: str | None = Field(default=None, max_length=2048)
    webhook_template: str | None = Field(default=None, max_length=4000)
    meow_nickname: str | None = Field(default=None, max_length=128)
    on_success: bool | None = None
    skipped_threshold: int | None = Field(default=None, ge=0, le=1_000_000)
    webhook_redact: bool | None = None
    meow_redact: bool | None = None


class NotifyTestChannel(_Base):
    """E24 单渠道测试结果（禁止静默：失败必须带原因）。"""

    channel: str = Field(description="healthchecks | webhook | meow")
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

    R-015（红线 S7 v1.3）：``overwrite_mode`` / ``keyword`` **仅原位恢复分支可用**——
    时间戳子目录路径出现二者之一即 422（该路径的 ``--overwrite=if-changed`` 由
    JobRunner 固定注入，界面无入口）；原位分支下 ``overwrite_mode`` 必须为
    ``always``、``keyword`` 必须等于快照短 id（三重确认门的后端校验）。
    """

    snapshot_id: str = Field(min_length=8, max_length=64)
    target: str
    paths: list[str] = Field(
        default_factory=list,
        description="要恢复的文件/目录（展示形态）；缺省或空 = 整快照恢复",
    )
    overwrite_mode: str | None = Field(
        default=None, description="原位恢复覆盖模式（仅接受 always；时间戳路径不得传）"
    )
    keyword: str | None = Field(
        default=None, description="原位恢复确认关键字（快照短 id 前 8 位）"
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
    """E20 响应：覆盖清单（游标分页）+ 覆盖总量（预览时点口径）。"""

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
