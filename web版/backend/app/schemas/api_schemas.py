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
