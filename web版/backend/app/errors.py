"""统一错误模型（§5.0）：所有非 2xx 响应均为 ``{"code", "message", "data"}``。

路由/服务只抛 :class:`AppError` 子类，由 ``main.py`` 注册的全局异常处理器统一转换为响应。

单一真源约束：错误码与其 HTTP 状态码的映射只存在于 ``app/constants.py`` 的
``ERROR_STATUS``；本模块**只引用不定义**（所有 ``status`` 均由 ``ERROR_STATUS[code]`` 派生），
避免两处数字双写漂移。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import (
    ACCOUNT_LOCKED,
    BOOTSTRAP_REQUIRED,
    BOOTSTRAP_SHOWN,
    COPY_NO_SNAPSHOT,
    COPY_SAME_REPO,
    COPY_TARGET_UNUSABLE,
    CREDENTIAL_DECRYPT_FAILED,
    CSRF_ORIGIN,
    ENV_OVERRIDDEN,
    ERROR_STATUS,
    FORBIDDEN,
    INTERNAL,
    JOB_CONFLICT,
    NOT_CANCELLABLE,
    NOT_FOUND,
    PASSWORD_WRONG,
    PATH_OUT_OF_PREFIX,
    RATE_LIMITED,
    RCLONE_CONF_LOCKED,
    RCLONE_REMOTE_IN_USE,
    RCLONE_UNAVAILABLE,
    READONLY_KEY,
    REPO_IN_USE_AS_MIRROR,
    REPO_NOT_EMPTY,
    REPO_NOT_FOUND,
    RESTIC_FAILED,
    RESTORE_FORBIDDEN,
    SITE_PACK_INVALID,
    SITE_PASSFILE_INCOMPLETE,
    SITE_SCHEMA_UNSUPPORTED,
    SITE_TARGET_NOT_EMPTY,
    TASK_RUNNING,
    UNAUTHORIZED,
    VALIDATION_ERROR,
)


class AppError(Exception):
    """业务错误基类。

    Attributes:
        status: HTTP 状态码（由 ``ERROR_STATUS[code]`` 派生）。
        code: 统一错误码（§5.0 错误码表）。
        message: 默认中文提示，可被构造参数覆盖。
        data: 可选的附加数据（如 ``retry_after``）。
    """

    code: str = INTERNAL
    status: int = ERROR_STATUS[INTERNAL]
    message: str = "服务器内部错误"

    def __init__(self, message: str | None = None, *, data: dict[str, Any] | None = None) -> None:
        """构造业务错误。

        Args:
            message: 覆盖默认提示文案。
            data: 附加到响应 ``data`` 字段的结构化数据。
        """
        resolved = message or self.message
        super().__init__(resolved)
        self.message = resolved
        self.data = data

    def __repr__(self) -> str:
        """返回便于日志排障的表示。"""
        return f"{type(self).__name__}(code={self.code!r}, status={self.status}, message={self.message!r})"


class ValidationError(AppError):
    """422 参数/路径/强度校验失败。"""

    code = VALIDATION_ERROR
    status = ERROR_STATUS[VALIDATION_ERROR]
    message = "参数校验失败"


class UnauthorizedError(AppError):
    """401 未登录或会话过期。"""

    code = UNAUTHORIZED
    status = ERROR_STATUS[UNAUTHORIZED]
    message = "未登录或会话已过期"


class ForbiddenError(AppError):
    """403 通用禁止访问。"""

    code = FORBIDDEN
    status = ERROR_STATUS[FORBIDDEN]
    message = "禁止访问"


class NotFoundError(AppError):
    """404 资源不存在。"""

    code = NOT_FOUND
    status = ERROR_STATUS[NOT_FOUND]
    message = "资源不存在"


class JobConflictError(AppError):
    """409 同仓库已有 running/pending 任务（手动触发拒绝，不排队）。"""

    code = JOB_CONFLICT
    status = ERROR_STATUS[JOB_CONFLICT]
    message = "已有任务进行中"


class AccountLockedError(AppError):
    """429 登录锁定（data 带 retry_after 秒）。"""

    code = ACCOUNT_LOCKED
    status = ERROR_STATUS[ACCOUNT_LOCKED]
    message = "账号已被临时锁定，请稍后重试"

    def __init__(self, retry_after: int, locked_until: str | None = None) -> None:
        """构造锁定错误。

        Args:
            retry_after: 距解锁的剩余秒数（向上取整）。
            locked_until: 解锁时刻（ISO 8601 UTC），可选。
        """
        data: dict[str, Any] = {"retry_after": retry_after}
        if locked_until:
            data["locked_until"] = locked_until
        super().__init__(f"账号已被临时锁定，请在 {retry_after} 秒后重试", data=data)


class RateLimitedError(AppError):
    """429 登录接口限流。"""

    code = RATE_LIMITED
    status = ERROR_STATUS[RATE_LIMITED]
    message = "请求过于频繁，请稍后重试"

    def __init__(self, retry_after: int = 0) -> None:
        """构造限流错误。

        Args:
            retry_after: 距窗口重置的剩余秒数。
        """
        super().__init__(data={"retry_after": retry_after})


class PathOutOfPrefixError(AppError):
    """400 路径越出白名单前缀（注入防护，方案 §6）。"""

    code = PATH_OUT_OF_PREFIX
    status = ERROR_STATUS[PATH_OUT_OF_PREFIX]
    message = "路径不在允许的前缀范围内"


class EnvOverriddenError(AppError):
    """409 该配置键由环境变量覆盖，界面不可写。"""

    code = ENV_OVERRIDDEN
    status = ERROR_STATUS[ENV_OVERRIDDEN]
    message = "该配置项由环境变量覆盖，不可在界面修改"


class ReadonlyKeyError(AppError):
    """409 只读派生键不可直接写（如 ``repo.type``，由服务端按 ``repo.url`` 协议推导落库）。"""

    code = READONLY_KEY
    status = ERROR_STATUS[READONLY_KEY]
    message = "该配置项为只读派生键，由服务端自动维护，不可直接修改"


class CopySameRepoError(ValidationError):
    """422 手动 copy：源与目标指向同一物理仓库（快照 ID 重叠）。

    独立错误码的原因：沿用 ``VALIDATION_ERROR`` 时前端只显示通用词条
    「参数校验失败」，用户无法得知是哪种拒绝。同仓是「提示复制完成但目标仓看不到」
    最隐蔽的成因，必须让它自己说出来。
    """

    code = COPY_SAME_REPO
    status = ERROR_STATUS[COPY_SAME_REPO]
    message = (
        "源仓库与目标仓库指向同一仓库（快照 ID 重叠），复制不会产生新副本；"
        "请检查两个仓库记录的地址是否指向同一存储"
    )


class CopyNoSnapshotError(ValidationError):
    """422 手动 copy：源仓库没有（匹配标签的）快照可复制。"""

    code = COPY_NO_SNAPSHOT
    status = ERROR_STATUS[COPY_NO_SNAPSHOT]
    message = "源仓库没有可复制的快照（若已设标签过滤，请确认该标签下确有快照）"


class CopyTargetUnusableError(ValidationError):
    """422 手动 copy：目标仓库读不出来（地址错误 / 口令不符 / 未初始化）。

    restic 的具体原因（退出码语义 + Fatal 行）放 ``data.reason``，不进 message——
    message 面向用户给可操作指引，细节留给排障与前端展开。
    """

    code = COPY_TARGET_UNUSABLE
    status = ERROR_STATUS[COPY_TARGET_UNUSABLE]
    message = "目标仓库不可用（请确认地址、密码与是否已初始化）"

    def __init__(self, *, target_repo_id: str, reason: str = "") -> None:
        """构造目标仓库不可用错误。

        Args:
            target_repo_id: 目标仓库 id（回显供定位）。
            reason: restic 侧失败原因（已脱敏；可选）。
        """
        super().__init__(data={"target_repo_id": target_repo_id, "reason": reason or None})


class CredentialKeyUnavailableError(ValidationError):
    """422 仓库密码文件缺失/不可读，无法进行凭据加解密（R-025，E5 布局）。"""

    message = "仓库密码文件缺失或不可读，无法进行凭据加解密"

    def __init__(self, path: str | None = None) -> None:
        """构造密钥不可用错误。

        Args:
            path: 密码文件路径（E5 per-repo 布局，回显供用户定位；非敏感信息）。
        """
        super().__init__(data={"path": path} if path else None)


class CredentialDecryptError(AppError):
    """500 凭据密文无法解密（密钥不匹配/数据被篡改；R-025）。

    明文/密文都不进 message——只给可操作原因（密码文件被更换或数据损坏）。
    """

    code = CREDENTIAL_DECRYPT_FAILED
    status = ERROR_STATUS[CREDENTIAL_DECRYPT_FAILED]
    message = "凭据解密失败：仓库密码文件可能已被更换，或配置数据已损坏"


class NotCancellableError(AppError):
    """409 任务非 running/pending，不可取消。"""

    code = NOT_CANCELLABLE
    status = ERROR_STATUS[NOT_CANCELLABLE]
    message = "该任务当前状态不可取消"


class RestoreForbiddenError(AppError):
    """403 恢复目标不在白名单。"""

    code = RESTORE_FORBIDDEN
    status = ERROR_STATUS[RESTORE_FORBIDDEN]
    message = "恢复目标不在允许的白名单内"


class BootstrapShownError(AppError):
    """410 首启随机密码已展示过，不可再次获取。"""

    code = BOOTSTRAP_SHOWN
    status = ERROR_STATUS[BOOTSTRAP_SHOWN]
    message = "首启随机密码已展示过一次，无法再次查看；如需重新引导请删除 /data 下数据库文件后重启"


class PasswordWrongError(AppError):
    """401 密码错误（安全文案不区分细节）。"""

    code = PASSWORD_WRONG
    status = ERROR_STATUS[PASSWORD_WRONG]
    message = "密码错误"


class CsrfOriginError(AppError):
    """403 写方法 Origin/Referer 同源校验失败。"""

    code = CSRF_ORIGIN
    status = ERROR_STATUS[CSRF_ORIGIN]
    message = "跨站请求被拒绝（Origin 校验失败）"


class BootstrapRequiredError(AppError):
    """403 首启未改密门（PRD US-A1 AC2）。"""

    code = BOOTSTRAP_REQUIRED
    status = ERROR_STATUS[BOOTSTRAP_REQUIRED]
    message = "请先完成首启改密后再访问其他功能"

    def __init__(self, steps: list[str] | None = None) -> None:
        """构造首启门错误。

        Args:
            steps: 引导步骤提示，写入响应 data 供前端跳转引导页。
        """
        data = {"steps": steps or []}
        super().__init__(data=data)


class InternalError(AppError):
    """500 兜底（不回内部细节）。"""

    code = INTERNAL
    status = ERROR_STATUS[INTERNAL]
    message = "服务器内部错误"


class ResticFailedError(AppError):
    """500 restic 子进程执行失败（探测类端点：原因明示，禁止静默）。"""

    code = RESTIC_FAILED
    status = ERROR_STATUS[RESTIC_FAILED]
    message = "restic 执行失败"

    def __init__(self, reason: str, *, exit_code: int | None = None) -> None:
        """构造 restic 失败错误。

        Args:
            reason: 可操作的原因说明（锁残留/凭据缺失/仓库未初始化…）。
            exit_code: restic 退出码，可选。
        """
        data: dict[str, object] = {"reason": reason}
        if exit_code is not None:
            data["exit_code"] = exit_code
        super().__init__(f"restic 执行失败：{reason}", data=data)


class TaskRunningError(AppError):
    """409 运行中/等待中任务拒绝删除（E35''：先取消再删除）。"""

    code = TASK_RUNNING
    status = ERROR_STATUS[TASK_RUNNING]
    message = "任务正在运行或等待中，请先取消后再删除"

    def __init__(self, running_job_id: str, task_name: str) -> None:
        """构造任务运行中错误。

        Args:
            running_job_id: 阻塞删除的活跃 job id（供前端定位取消入口）。
            task_name: 任务名称（UI 提示「已有任务进行中：<任务名>」）。
        """
        super().__init__(
            f"任务「{task_name}」正在运行或等待中，请先取消后再删除",
            data={"running_job_id": running_job_id, "task_name": task_name},
        )


class RepoNotFoundError(AppError):
    """404 repo 作用域 id 不存在（E34–E36 路径前缀；P2 仅 ``default``）。"""

    code = REPO_NOT_FOUND
    status = ERROR_STATUS[REPO_NOT_FOUND]
    message = "仓库不存在"

    def __init__(self, repo_id: str) -> None:
        """构造仓库不存在错误。

        Args:
            repo_id: 请求的 repo 作用域 id（回显供前端排障）。
        """
        super().__init__(f"仓库 {repo_id!r} 不存在", data={"repo_id": repo_id})


class RcloneUnavailableError(AppError):
    """503 rclone 不可用 / conf 不可写（二进制缺失、超时、事件循环不支持、挂载形态问题）。"""

    code = RCLONE_UNAVAILABLE
    status = ERROR_STATUS[RCLONE_UNAVAILABLE]
    message = "rclone 配置操作不可用"


class RcloneConfLockedError(AppError):
    """503 conf 已加密但解密口令不可达（所有 rclone: 远端暂不可用）。"""

    code = RCLONE_CONF_LOCKED
    status = ERROR_STATUS[RCLONE_CONF_LOCKED]
    message = "conf 已加密但口令不可达，所有 rclone: 远端不可用；请恢复配置口令后重试，或用宿主机明文 conf 备份还原"


class RepoNotEmptyError(AppError):
    """409 仓库仍被占用，拒绝删除（E41：有备份任务或活跃作业时先清理）。

    终态作业历史（success/failed/interrupted/cancelled）不阻塞删除——随仓库
    级联清理（见 :meth:`ReposRepo.delete`）；v1「有历史即拒删」会使任何初始化过
    的仓库永远无法删除（init 即落 job 行且 jobs 无单行删除入口），已废止。
    """

    code = REPO_NOT_EMPTY
    status = ERROR_STATUS[REPO_NOT_EMPTY]
    message = "仓库下仍有备份任务或运行中的作业，请先处理后再删除"

    def __init__(self, repo_id: str, *, tasks: int = 0, active_jobs: int = 0) -> None:
        """构造仓库占用错误。

        Args:
            repo_id: 被拒删的仓库 id（回显供前端定位）。
            tasks: 仓库下 backup_tasks 条数（>0 时提示先删任务）。
            active_jobs: 仓库下 running/pending 作业条数（>0 时提示先取消）。
        """
        if tasks and active_jobs:
            detail = (
                f"仓库 {repo_id!r} 下仍有 {tasks} 个备份任务与 {active_jobs} 个运行中的作业，"
                "请先处理后再删除"
            )
        elif tasks:
            detail = f"仓库 {repo_id!r} 下仍有 {tasks} 个备份任务，请先删除任务后再删除仓库"
        elif active_jobs:
            detail = (
                f"仓库 {repo_id!r} 下有 {active_jobs} 个正在运行或等待中的作业，请先取消后再删除仓库"
            )
        else:  # pragma: no cover - 调用方保证至少一项非零
            detail = f"仓库 {repo_id!r} 仍被占用，暂不可删除"
        super().__init__(
            detail,
            data={"repo_id": repo_id, "tasks": tasks, "active_jobs": active_jobs},
        )


class RcloneRemoteInUseError(AppError):
    """409 rclone 远端仍被引用，拒绝删除（**删除保护方案 P0**，裁决 1/2/3/4）。

    两类引用（命中任一即阻断）：

    1. **直接引用**：某仓库 ``url`` 形如 ``rclone:<remote>:<path>``，且 remote 段
       完整相等（带尾冒号匹配，避免 ``foo`` 误命中 ``foobar``）；
    2. **传递依赖**：conf 内 ``type=crypt`` 段的 ``remote=<remote>:...``——crypt 是
       独立段名，底层 remote 一删，加密层即废（crypt 密码丢失 = 永久不可读）。

    ``data`` 携带 ``blockers`` 全部信息（远端名 / 引用仓库 / 依赖 crypt 段 / 是否
    crypt 段），前端直接渲染「先处理这些」，无需二次请求。
    """

    code = RCLONE_REMOTE_IN_USE
    status = ERROR_STATUS[RCLONE_REMOTE_IN_USE]
    message = "该 rclone 远端仍被引用，请先处理后再删除"

    def __init__(
        self,
        remote: str,
        *,
        repos: tuple[tuple[str, str], ...] = (),
        dependent_remotes: tuple[str, ...] = (),
        crypt: bool = False,
    ) -> None:
        """构造「远端被引用」错误。

        Args:
            remote: 待删除的 rclone 远端名（回显供前端定位）。
            repos: 直接引用该 remote 的仓库 ``(id, name)``。
            dependent_remotes: 以该 remote 为底层的 crypt 段名。
            crypt: 待删段自身是否 crypt 类型（提示更严重：密码丢失不可逆）。
        """
        parts: list[str] = []
        if repos:
            names = "、".join(name or rid for rid, name in repos[:10])
            parts.append(f"仍被 {len(repos)} 个仓库引用（{names}）")
        if dependent_remotes:
            names = "、".join(dependent_remotes[:10])
            parts.append(f"仍被 {len(dependent_remotes)} 个 crypt 段作为底层（{names}）")
        detail = f"rclone 远端「{remote}」" + "，".join(parts) if parts else f"rclone 远端「{remote}」仍被引用"
        detail += "——请先删除或改指向这些仓库后再删除远端"
        if crypt:
            detail += "（该段为 crypt 加密层，密码丢失将导致数据永久不可读）"
        super().__init__(
            detail,
            data={
                "remote": remote,
                "repos": [{"id": rid, "name": name} for rid, name in repos],
                "dependent_remotes": list(dependent_remotes),
                "crypt": crypt,
            },
        )


class RepoInUseAsMirrorError(AppError):
    """409 仓库仍被其他仓库的备份任务作为镜像目的地，拒绝删除（**删除保护方案 P1**，裁决 5）。

    镜像仓是**跨仓引用**：``backup_tasks.mirror_repo_ids`` 是 JSON 文本列，删除主仓时
    若它仍在别仓任务的镜像列表里，后续复制阶段会直接标失败
    （运行期降级见 ``job_runner.py:820-828``）。与其让用户事后才发现，不如在删除
    前给出引用任务清单。
    """

    code = REPO_IN_USE_AS_MIRROR
    status = ERROR_STATUS[REPO_IN_USE_AS_MIRROR]
    message = "该仓库仍被其他备份任务作为镜像目的地，请先处理后再删除"

    def __init__(self, repo_id: str, *, tasks: tuple[tuple[int, str], ...] = ()) -> None:
        """构造「仓库被镜像引用」错误。

        Args:
            repo_id: 被拒删的仓库 id。
            tasks: 引用本仓为镜像目的地的任务 ``(id, name)``。
        """
        names = "、".join(name or str(tid) for tid, name in tasks[:10])
        detail = (
            f"仓库「{repo_id}」仍被 {len(tasks)} 个备份任务作为镜像目的地（{names}）——"
            "请先从这些任务的镜像配置中移除本仓后再删除"
        )
        super().__init__(
            detail,
            data={
                "repo_id": repo_id,
                "tasks": [{"id": tid, "name": name} for tid, name in tasks],
            },
        )

class SitePackInvalidError(ValidationError):
    """422 全站备份包结构/载荷/版本不符约定（v1.5 §4.7）。

    继承 :class:`ValidationError` 以复用 422 语义与 ``data`` 通道，但**必须独立成码**：
    前端要据此把「旧配置迁移包，请改用全站包」这类可操作原因原样显示，混进
    ``VALIDATION_ERROR`` 会被统一文案吞掉（同 ``COPY_*`` 三码的立码理由）。
    """

    code = SITE_PACK_INVALID
    status = ERROR_STATUS[SITE_PACK_INVALID]
    message = "备份包格式不符合要求"


class SiteSchemaUnsupportedError(AppError):
    """422 包内 ``schema_version`` 高于目标机 ``PRAGMA user_version``（v1.5 §4.3 第 5 步）。

    导入是「把旧机数据搬回新机」，目标机应用版本过旧时表结构缺列，写入必失败；
    先行拒绝比半途事务回滚更省事，也避免用户误以为包损坏。
    """

    code = SITE_SCHEMA_UNSUPPORTED
    status = ERROR_STATUS[SITE_SCHEMA_UNSUPPORTED]
    message = "备份包的数据结构版本高于本机，请先升级应用"

    def __init__(self, *, pack_schema: int, target_schema: int) -> None:
        """构造「架构版本过新」错误。

        Args:
            pack_schema: 包内 ``schema_version``。
            target_schema: 目标机 ``PRAGMA user_version``。
        """
        super().__init__(
            f"该包来自更新版本的应用（schema {pack_schema} > {target_schema}），请先升级",
            data={"pack_schema": pack_schema, "target_schema": target_schema},
        )


class SiteTargetNotEmptyError(AppError):
    """409 replace 导入要求目标机为空库态（v1.5 §4.3 第 8 步）。

    ``mode="replace"`` 会 DELETE 全部业务表，只在「刚部署、还没建仓库/任务」的机器上
    才有意义；一旦目标机有数据，静默清空等于数据丢失，故先列清阻碍项再要求确认整机替换。

    确认通道（§4.2 / §4.7）：调用方在 `data.acknowledge` 传回**包外层 `created_at`**
    （不是载荷内的 created_at，也不是目标机摘要字段），服务端逐字比对通过才放行；
    `data.acknowledge_required=True` 是前端 ConfirmGate 的显式开关。
    """

    code = SITE_TARGET_NOT_EMPTY
    status = ERROR_STATUS[SITE_TARGET_NOT_EMPTY]
    message = "目标实例已有数据，导入需要空库环境"

    def __init__(
        self,
        *,
        blockers: tuple[str, ...] = (),
        target: Mapping[str, Any] | None = None,
    ) -> None:
        """构造「目标非空」错误。

        Args:
            blockers: 阻碍项名称（如 ``repos`` / ``backup_tasks``），用于前端提示。
            target: 目标现状摘要（``SiteTargetSummary`` 的字段字典，只含计数/布尔/
                活跃作业清单，**不含任何凭据**）。§4.7 要求 409 一并返回目标现状摘要，
                让用户在确认「整机替换」之前看得见即将被清空的 jobs/tasks/config 行数。
        """
        listed = "、".join(blockers[:10]) or "业务数据"
        data: dict[str, Any] = {
            "blockers": list(blockers),
            "acknowledge_required": True,
            "acknowledge": "必须等于包外层 created_at（字符串逐字匹配）",
        }
        if target is not None:
            data["target"] = dict(target)
        super().__init__(
            f"目标实例的 {listed} 非空——确认整机替换，或改用一台全新实例导入",
            data=data,
        )


class SitePassfileIncompleteError(ValidationError):
    """422 源机仓库密码文件缺失，导出 fail-closed（v1.5 §3.3）。

    没有密码文件的仓库，导入后快照全不可读——给出一个「能导不能读」的包比不给包更糟，
    故导出阶段就拒绝，并点名缺哪些仓库。
    """

    code = SITE_PASSFILE_INCOMPLETE
    status = ERROR_STATUS[SITE_PASSFILE_INCOMPLETE]
    message = "存在仓库缺少密码文件，无法导出完整备份包"

    def __init__(self, *, repo_ids: tuple[str, ...] = (), message: str | None = None) -> None:
        """构造「密码文件缺失」错误。

        Args:
            repo_ids: 缺密码文件的仓库 id 列表。
            message: 覆盖默认文案。导出侧「conf 已加密但配置口令不可读」（方案 §3.3-6）
                是同一 fail-closed 语义的另一条原因，错误码共用、文案必须能说清是哪一种。
        """
        listed = "、".join(repo_ids[:10]) or "未知仓库"
        super().__init__(
            message or f"以下仓库的密码文件缺失，导出已中止（包会不完整）：{listed}",
            data={"repo_ids": list(repo_ids)},
        )