"""统一错误模型（§5.0）：所有非 2xx 响应均为 ``{"code", "message", "data"}``。

路由/服务只抛 :class:`AppError` 子类，由 ``main.py`` 注册的全局异常处理器统一转换为响应，
保证错误码与 HTTP 状态码的映射只有一处实现。
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
    FORBIDDEN,
    INTERNAL,
    JOB_CONFLICT,
    NOT_CANCELLABLE,
    NOT_FOUND,
    PASSWORD_WRONG,
    PATH_OUT_OF_PREFIX,
    RATE_LIMITED,
    RESTORE_FORBIDDEN,
    UNAUTHORIZED,
    VALIDATION_ERROR,
)


class AppError(Exception):
    """业务错误基类。

    Attributes:
        status: HTTP 状态码。
        code: 统一错误码（§5.0 错误码表）。
        message: 默认中文提示，可被构造参数覆盖。
        data: 可选的附加数据（如 ``retry_after``）。
    """

    status: int = 500
    code: str = INTERNAL
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

    status = 422
    code = VALIDATION_ERROR
    message = "参数校验失败"


class UnauthorizedError(AppError):
    """401 未登录或会话过期。"""

    status = 401
    code = UNAUTHORIZED
    message = "未登录或会话已过期"


class ForbiddenError(AppError):
    """403 通用禁止访问。"""

    status = 403
    code = FORBIDDEN
    message = "禁止访问"


class NotFoundError(AppError):
    """404 资源不存在。"""

    status = 404
    code = NOT_FOUND
    message = "资源不存在"


class JobConflictError(AppError):
    """409 同仓库已有 running/pending 任务（手动触发拒绝，不排队）。"""

    status = 409
    code = JOB_CONFLICT
    message = "已有任务进行中"


class AccountLockedError(AppError):
    """429 登录锁定（data 带 retry_after 秒）。"""

    status = 429
    code = ACCOUNT_LOCKED
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

    status = 429
    code = RATE_LIMITED
    message = "请求过于频繁，请稍后重试"

    def __init__(self, retry_after: int = 0) -> None:
        """构造限流错误。

        Args:
            retry_after: 距窗口重置的剩余秒数。
        """
        super().__init__(data={"retry_after": retry_after})


class PathOutOfPrefixError(AppError):
    """400 路径越出白名单前缀（注入防护，方案 §6）。"""

    status = 400
    code = PATH_OUT_OF_PREFIX
    message = "路径不在允许的前缀范围内"


class EnvOverriddenError(AppError):
    """409 该配置键由环境变量覆盖，界面不可写。"""

    status = 409
    code = ENV_OVERRIDDEN
    message = "该配置项由环境变量覆盖，不可在界面修改"


class DefaultPolicyImmutableError(AppError):
    """409 默认策略不可删除。"""

    status = 409
    code = DEFAULT_POLICY_IMMUTABLE
    message = "默认策略不可删除"


class NotCancellableError(AppError):
    """409 任务非 running/pending，不可取消。"""

    status = 409
    code = NOT_CANCELLABLE
    message = "该任务当前状态不可取消"


class RestoreForbiddenError(AppError):
    """403 恢复目标不在白名单。"""

    status = 403
    code = RESTORE_FORBIDDEN
    message = "恢复目标不在允许的白名单内"


class BootstrapShownError(AppError):
    """410 首启随机密码已展示过，不可再次获取。"""

    status = 410
    code = BOOTSTRAP_SHOWN
    message = "首启随机密码已展示过一次，无法再次查看；如需重新引导请删除 /data 下数据库文件后重启"


class PasswordWrongError(AppError):
    """401 密码错误（安全文案不区分细节）。"""

    status = 401
    code = PASSWORD_WRONG
    message = "密码错误"


class CsrfOriginError(AppError):
    """403 写方法 Origin/Referer 同源校验失败。"""

    status = 403
    code = CSRF_ORIGIN
    message = "跨站请求被拒绝（Origin 校验失败）"


class BootstrapRequiredError(AppError):
    """403 首启未改密门（PRD US-A1 AC2）。"""

    status = 403
    code = BOOTSTRAP_REQUIRED
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

    status = 500
    code = INTERNAL
    message = "服务器内部错误"
