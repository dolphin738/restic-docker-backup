"""AuthService（架构 §1.1）：登录/登出编排、登录限流与锁定退避。

- 登录限流：进程内固定窗口，同 IP 5 次/分钟（内存实现，重启清零可接受——锁定已持久化兜底，§8）；
- 登录锁定：计数持久化于 ``admin_account``，容器重启不清零；5 次失败 → 锁 5min × 2^level（上限 1h）。
"""

from __future__ import annotations

import logging
import time

from fastapi import Request

from app.auth import password as password_mod
from app.auth.bootstrap import AdminAccountStore
from app.auth.session import SessionService
from app.constants import AUDIT_LOGIN_FAILED, AUDIT_LOGIN_OK, AUDIT_LOGOUT
from app.db.repositories.audit_repo import AuditRepo
from app.errors import AccountLockedError, InternalError, PasswordWrongError, RateLimitedError
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class LoginRateLimiter:
    """进程内固定窗口限流器（同 IP 登录接口 5 次/分钟）。"""

    def __init__(self, limit: int = 5, window_seconds: int = 60) -> None:
        """构造限流器。

        Args:
            limit: 窗口内允许的最大请求数。
            window_seconds: 窗口长度（秒）。
        """
        self._limit = max(1, limit)
        self._window = max(1, window_seconds)
        self._buckets: dict[str, tuple[float, int]] = {}

    def acquire(self, key: str) -> None:
        """占用一次配额；超限时抛错。

        Args:
            key: 限流维度键（此处为客户端 IP）。

        Raises:
            RateLimitedError: 窗口内请求数超过上限（429 RATE_LIMITED）。
        """
        now = time.monotonic()
        start, count = self._buckets.get(key, (now, 0))
        if now - start >= self._window:
            start, count = now, 0
        if count + 1 > self._limit:
            retry_after = max(1, int(self._window - (now - start)))
            self._buckets[key] = (start, count + 1)
            raise RateLimitedError(retry_after=retry_after)
        self._buckets[key] = (start, count + 1)

    def reset(self) -> None:
        """清空全部计数（测试用）。"""
        self._buckets.clear()


def client_ip(request: Request, settings: Settings) -> str:
    """解析客户端 IP（限流维度）。

    仅在 ``trust_proxy=true`` 时信任 ``X-Forwarded-For`` 首跳，避免被伪造头绕过限流。
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


class AuthService:
    """登录/登出编排（§6.1 时序图）。"""

    def __init__(
        self,
        audit: AuditRepo,
        sessions: SessionService,
        admin: AdminAccountStore,
        settings: Settings | None = None,
        rate_limiter: LoginRateLimiter | None = None,
    ) -> None:
        """构造认证服务。

        Args:
            audit: 审计仓储。
            sessions: 会话服务。
            admin: 管理员账户仓储。
            settings: 运行期配置。
            rate_limiter: 限流器；缺省按配置构造。
        """
        self._audit = audit
        self._sessions = sessions
        self._admin = admin
        self._settings = settings or get_settings()
        self._limiter = rate_limiter or LoginRateLimiter(
            limit=self._settings.login_rate_limit,
            window_seconds=self._settings.login_rate_window_seconds,
        )

    async def login(self, password: str, ip: str) -> str:
        """E01 登录：限流 → 锁定检查 → 校验密码 → 签发会话。

        Args:
            password: 提交的明文密码。
            ip: 客户端 IP（限流与审计维度）。

        Returns:
            明文会话 token（由路由写入 cookie）。

        Raises:
            RateLimitedError: 429 限流。
            AccountLockedError: 429 账号锁定。
            PasswordWrongError: 401 密码错误。
        """
        self._limiter.acquire(ip)

        state = await self._admin.lock_state()
        if state.locked:
            await self._audit.record(
                AUDIT_LOGIN_FAILED,
                target=ip,
                detail={"reason": "locked"},
                result="denied",
            )
            raise AccountLockedError(retry_after=state.retry_after, locked_until=state.locked_until)

        account = await self._admin.get()
        if account is None:
            raise InternalError("管理员账户未初始化")

        if not password_mod.verify_password(password, account.password_hash):
            await self._admin.register_failure()
            await self._audit.record(
                AUDIT_LOGIN_FAILED,
                target=ip,
                detail={"reason": "password_wrong"},
                result="denied",
            )
            raise PasswordWrongError()

        await self._admin.clear_failures()
        token = await self._sessions.create()
        await self._audit.record(AUDIT_LOGIN_OK, target=ip, result="ok")
        return token

    async def logout(self, token: str | None, ip: str = "") -> None:
        """E02 登出：销毁服务端会话（路由负责清 cookie）。"""
        await self._sessions.destroy(token)
        await self._audit.record(AUDIT_LOGOUT, target=ip or None, result="ok")

    async def destroy_others(self, current_token: str | None) -> int:
        """异步销毁除当前会话外的全部会话（E28 改密后"销毁其他 session"）。

        Args:
            current_token: 当前会话明文 token；为 None 时销毁全部会话。

        Returns:
            被销毁的会话条数。
        """
        affected = await self._sessions.destroy_others(current_token)
        if affected:
            logger.info("改密后销毁其他会话 %d 条", affected)
        return affected
