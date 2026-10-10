"""API 请求/响应模型（§5.0 统一约定 + §5.1 端点明细）。

本批次覆盖：E01 登录、E02 登出、E03 me、E04 health、Bootstrap 取密码、E28 改密，
以及 config 键值的脱敏输出与分页壳（供后续批次复用）。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

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
    """E14 任务详情（§4.5 字段全集）。"""

    id: str
    type: JobType
    status: JobStatus
    trigger: JobTrigger
    policy_id: int | None = None
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
    policy_id: int | None = Field(default=None, description="关联策略 id")
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


class DryRunItem(_Base):
    """E31 预览条目。"""

    path: str
    display_path: str
    size: int | None = None


class DryRunWarning(_Base):
    """E31 黄色警告（伪文件系统目录名，不拦截）。"""

    type: str = "pseudo_fs_name"
    path: str


class DryRunResponse(_Base):
    """E31 预览响应（不强制拦截，warnings 仅提示）。"""

    items: list[DryRunItem] = Field(default_factory=list)
    warnings: list[DryRunWarning] = Field(default_factory=list)
    truncated: bool = False
    error: str | None = None


# ---------------------------------------------------------------- E32 内置排除集（只读暴露）

class ExclusionsResponse(_Base):
    """E32 内置排除集的**只读**暴露（D8：单一事实来源，前端不得自行复制一份规则）。

    ``items`` 为当前模式下完整生效的内置集（容器内路径前缀形式，UI 侧按原路径语义展示需自行
    过 PathMapper）；``core`` 是其中与挂载模式无关的语义核心 8 条，供 UI 分组标注。
    """

    mode: RunMode
    items: list[str] = Field(default_factory=list, description="当前模式完整内置排除集（容器内路径）")
    core: list[str] = Field(default_factory=list, description="语义核心子集（与模式无关，供 UI 分组）")
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
    excludes: list[str] = Field(default_factory=list)
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


# ---------------------------------------------------------------- E19 恢复

class RestoreRequest(_Base):
    """E19 恢复请求：**只有** snapshot_id 与 target，不存在任何覆盖语义参数。"""

    snapshot_id: str = Field(min_length=8, max_length=64)
    target: str


class RestoreResponse(_Base):
    """E19 响应（202）：服务端计算的时间戳子目录。"""

    job_id: str
    restore_target: str


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
    """E30 仓库状态（仪表盘状态卡 + 锁残留醒目提示数据源）。"""

    connected: bool = False
    locked: bool = False
    capacity: RepoCapacity = Field(default_factory=RepoCapacity)
    last_error: str | None = None
