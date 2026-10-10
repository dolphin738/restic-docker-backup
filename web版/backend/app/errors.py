"""统一错误模型（§5.0）：所有非 2xx 响应均为 ``{"code", "message", "data"}``。

路由/服务只抛 :class:`AppError` 子类，由 ``main.py`` 注册的全局异常处理器统一转换为响应。

单一真源约束：错误码与其 HTTP 状态码的映射只存在于 ``app/constants.py`` 的
``ERROR_STATUS``；本模块**只引用不定义**（所有 ``status`` 均由 ``ERROR_STATUS[code]`` 派生），
避免两处数字双写漂移。
"""

from __future__ import annotations

from typing import Any

from app.constants import (
    ACCOUNT_LOCKED,
    BOOTSTRAP_REQUIRED,
    BOOTSTRAP_SHOWN,
    CSRF_ORIGIN,
    DEFAULT_POLICY_IMMUTABLE,
    ENV_OVERRIDDEN,
    ERROR_STATUS,
    FORBIDDEN,
    INTERNAL,
    JOB_CONFLICT,
    NOT_CANCELLABLE,
    NOT_FOUND,
    PASSWORD_WRONG,
    PATH_OUT_OF_PREFIX,
    POLICY_WRITE_CLOSED,
    RATE_LIMITED,
    RESTIC_FAILED,
    RESTORE_FORBIDDEN,
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


class DefaultPolicyImmutableError(AppError):
    """409 默认策略不可删除。"""

    code = DEFAULT_POLICY_IMMUTABLE
    status = ERROR_STATUS[DEFAULT_POLICY_IMMUTABLE]
    message = "默认策略不可删除"


class PolicyWriteClosedError(AppError):
    """409 P1 多策略写未开放（E09/E10/E11：仅单条默认策略可写）。"""

    code = POLICY_WRITE_CLOSED
    status = ERROR_STATUS[POLICY_WRITE_CLOSED]
    message = "P1 仅支持单条默认策略，多策略写开放于 P2"


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
