"""首启引导与管理员账户状态机（§6.1 时序图 / PRD US-A1）。

职责划分：
- :class:`AdminAccountStore` —— ``admin_account`` 单行表的唯一读写口（含登录锁定计数）；
- :class:`BootstrapService` —— 首启随机密码一次性展示 + 强制改密状态机。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.constants import (
    AUDIT_BOOTSTRAP_SHOWN,
    AUDIT_PASSWORD_CHANGED,
)
from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.auth import password as password_mod
from app.errors import BootstrapShownError, InternalError, PasswordWrongError, ValidationError
from app.settings import Settings, get_settings
from app.utils.time import add_seconds, now_utc, parse_iso, utc_iso

logger = logging.getLogger(__name__)

BOOTSTRAP_STEPS: tuple[str, ...] = (
    "打开首启引导页，查看并保存一次性随机管理密码",
    "使用该随机密码作为旧密码，设置新密码",
    "改密完成后使用新密码登录",
)


@dataclass(frozen=True)
class AdminAccount:
    """admin_account 单行快照。"""

    password_hash: str
    must_change: bool
    bootstrap_shown: bool
    bootstrap_password_hash: str | None
    failed_attempts: int
    lock_level: int
    locked_until: str | None
    updated_at: str


@dataclass(frozen=True)
class LockState:
    """登录锁定状态。"""

    locked: bool
    retry_after: int
    locked_until: str | None


class AdminAccountStore:
    """``admin_account`` 单行表的唯一读写口（R-001 状态持久化）。"""

    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        """构造账户仓储。

        Args:
            db: 数据库单例。
            settings: 运行期配置（锁定退避参数）。
        """
        self._db = db
        self._settings = settings or get_settings()

    # ---------------------------------------------------------------- 读

    async def get(self) -> AdminAccount | None:
        """读取账户行；未初始化返回 None。"""
        row = await self._db.fetch_one(
            "SELECT password_hash, must_change, bootstrap_shown, bootstrap_password_hash, "
            "failed_attempts, lock_level, locked_until, updated_at FROM admin_account WHERE id = 1"
        )
        return _to_account(row) if row else None

    async def lock_state(self) -> LockState:
        """计算当前锁定状态（含剩余秒数）。"""
        account = await self.get()
        if account is None or not account.locked_until:
            return LockState(locked=False, retry_after=0, locked_until=None)
        locked_until = parse_iso(account.locked_until)
        if locked_until is None:
            return LockState(locked=False, retry_after=0, locked_until=None)
        remaining = (locked_until - now_utc()).total_seconds()
        if remaining <= 0:
            return LockState(locked=False, retry_after=0, locked_until=account.locked_until)
        return LockState(locked=True, retry_after=int(remaining + 0.999), locked_until=account.locked_until)

    # ---------------------------------------------------------------- 初始化

    async def ensure_initialized(self) -> tuple[AdminAccount, str | None]:
        """保证账户行存在；首启（或尚未展示过随机密码）时生成/重新生成随机密码。

        Returns:
            ``(account, plaintext)``：plaintext 非 None 表示本次新生成了随机密码，
            需由调用方缓存到内存以供一次性展示（明文不落库）。
        """
        account = await self.get()
        if account is None:
            return await self._create_with_random_password()
        if account.must_change and not account.bootstrap_shown:
            # 随机密码尚未展示（进程重启过）：重新生成，保证 get 引导密码始终可用
            return await self._reset_random_password()
        return account, None

    async def _create_with_random_password(self) -> tuple[AdminAccount, str]:
        """首次初始化：写入随机密码哈希（password_hash 与 bootstrap_password_hash 同值）。"""
        plain = password_mod.generate_random_password()
        hashed = password_mod.hash_password(plain)
        now = utc_iso()
        await self._db.write(
            "INSERT INTO admin_account (id, password_hash, must_change, bootstrap_shown, "
            "bootstrap_password_hash, failed_attempts, lock_level, locked_until, updated_at) "
            "VALUES (1, ?, 1, 0, ?, 0, 0, NULL, ?)",
            (hashed, hashed, now),
        )
        account = await self.get()
        if account is None:  # pragma: no cover - 理论不可达
            raise InternalError("管理员账户初始化失败")
        return account, plain

    async def _reset_random_password(self) -> tuple[AdminAccount, str]:
        """重新生成随机密码（仅当尚未展示过时调用）。"""
        plain = password_mod.generate_random_password()
        hashed = password_mod.hash_password(plain)
        await self._db.write(
            "UPDATE admin_account SET password_hash = ?, bootstrap_password_hash = ?, "
            "failed_attempts = 0, lock_level = 0, locked_until = NULL, updated_at = ? WHERE id = 1",
            (hashed, hashed, utc_iso()),
        )
        account = await self.get()
        if account is None:  # pragma: no cover - 理论不可达
            raise InternalError("管理员账户初始化失败")
        return account, plain

    # ---------------------------------------------------------------- 锁定计数（§8）

    async def register_failure(self) -> LockState:
        """记录一次登录失败；达到阈值时进入指数退避锁定。

        Returns:
            更新后的锁定状态。
        """
        account = await self.get()
        if account is None:
            return LockState(locked=False, retry_after=0, locked_until=None)

        failed = account.failed_attempts + 1
        locked_until = account.locked_until
        lock_level = account.lock_level
        if failed >= self._settings.max_failed_attempts:
            seconds = min(
                self._settings.lock_base_seconds * (2**lock_level),
                self._settings.lock_max_seconds,
            )
            locked_until = utc_iso(add_seconds(now_utc(), seconds))
            lock_level += 1
            failed = 0  # 锁定后重新计数：解锁后再失败 5 次进入下一级
        await self._db.write(
            "UPDATE admin_account SET failed_attempts = ?, lock_level = ?, locked_until = ?, updated_at = ? "
            "WHERE id = 1",
            (failed, lock_level, locked_until, utc_iso()),
        )
        return await self.lock_state()

    async def clear_failures(self) -> None:
        """登录成功：清零失败计数与锁定级数。"""
        await self._db.write(
            "UPDATE admin_account SET failed_attempts = 0, lock_level = 0, locked_until = NULL, updated_at = ? "
            "WHERE id = 1",
            (utc_iso(),),
        )

    # ---------------------------------------------------------------- 改密

    async def apply_new_password(self, new_hash: str) -> None:
        """写入新密码哈希并结束首启引导状态。

        Args:
            new_hash: 新密码哈希。
        """
        await self._db.write(
            "UPDATE admin_account SET password_hash = ?, must_change = 0, bootstrap_password_hash = NULL, "
            "failed_attempts = 0, lock_level = 0, locked_until = NULL, updated_at = ? WHERE id = 1",
            (new_hash, utc_iso()),
        )

    async def mark_bootstrap_shown(self) -> None:
        """置 ``bootstrap_shown=1``（随机密码已展示，之后取密码一律 410）。"""
        await self._db.write(
            "UPDATE admin_account SET bootstrap_shown = 1, updated_at = ? WHERE id = 1",
            (utc_iso(),),
        )


class BootstrapService:
    """首启随机密码一次性展示 + 强制改密状态机（§6.1）。"""

    def __init__(
        self,
        admin: AdminAccountStore,
        audit: AuditRepo,
        settings: Settings | None = None,
    ) -> None:
        """构造引导服务。

        Args:
            admin: 管理员账户仓储。
            audit: 审计仓储（append-only）。
            settings: 运行期配置。
        """
        self._admin = admin
        self._audit = audit
        self._settings = settings or get_settings()
        # 进程内缓存的随机密码明文：仅用于一次性展示，不落库、不落日志
        self._pending_password: str | None = None

    async def ensure_initial_admin(self) -> None:
        """启动期调用：确保账户行存在，并缓存（必要时新生成的）随机密码明文。"""
        _, plain = await self._admin.ensure_initialized()
        if plain is not None:
            self._pending_password = plain

    async def reveal_password(self) -> str:
        """一次性取回首启随机密码明文。

        Returns:
            随机密码明文。

        Raises:
            BootstrapShownError: 已展示过或已完成改密（410 BOOTSTRAP_SHOWN）。
            InternalError: 明文缺失（理论不可达，进程重启会重新生成）。
        """
        account = await self._admin.get()
        if account is None:
            raise InternalError("管理员账户未初始化")
        if not account.must_change or account.bootstrap_shown:
            raise BootstrapShownError()
        if self._pending_password is None:
            raise InternalError("首启随机密码不可用，请重启容器后重试")
        plain = self._pending_password
        await self._admin.mark_bootstrap_shown()
        self._pending_password = None
        await self._audit.record(AUDIT_BOOTSTRAP_SHOWN, target="admin", result="ok")
        return plain

    async def change_password(
        self,
        old_password: str,
        new_password: str,
        *,
        destroy_other_sessions: Callable[[str | None], Awaitable[int]] | None = None,
        current_token: str | None = None,
    ) -> None:
        """E28 改密：校验旧密码 → 强度校验 → 重哈希 → 结束引导态 → 销毁其他会话 → 审计。

        Args:
            old_password: 旧密码（首启场景为随机引导密码）。
            new_password: 新密码明文。
            destroy_other_sessions: 可选的异步回调，签名为 ``async (token) -> int``；
                传入时改密成功后销毁除当前会话外的全部会话（§5.1 E28）。
            current_token: 当前会话明文 token（用于保留当前会话）。

        Raises:
            PasswordWrongError: 旧密码不正确（401 PASSWORD_WRONG）。
            ValidationError: 新密码强度不足（422）。
        """
        account = await self._admin.get()
        if account is None:
            raise InternalError("管理员账户未初始化")

        candidates = [account.password_hash]
        if account.bootstrap_password_hash:
            candidates.append(account.bootstrap_password_hash)
        if not any(password_mod.verify_password(old_password, candidate) for candidate in candidates):
            await self._audit.record(
                "password.change_failed",
                target="admin",
                detail={"reason": "old_password_mismatch"},
                result="denied",
            )
            raise PasswordWrongError("旧密码不正确")

        password_mod.validate_password_strength(new_password)
        if new_password == old_password:
            raise ValidationError("新密码不能与旧密码相同")

        await self._admin.apply_new_password(password_mod.hash_password(new_password))
        destroyed = 0
        if destroy_other_sessions is not None:
            destroyed = int(await destroy_other_sessions(current_token))
        await self._audit.record(
            AUDIT_PASSWORD_CHANGED,
            target="admin",
            detail={"sessions_destroyed": destroyed},
            result="ok",
        )


def _to_account(row: Any) -> AdminAccount:
    """将 sqlite3.Row 转换为 :class:`AdminAccount`。"""
    return AdminAccount(
        password_hash=str(row["password_hash"]),
        must_change=bool(int(row["must_change"])),
        bootstrap_shown=bool(int(row["bootstrap_shown"])),
        bootstrap_password_hash=(
            None if row["bootstrap_password_hash"] is None else str(row["bootstrap_password_hash"])
        ),
        failed_attempts=int(row["failed_attempts"]),
        lock_level=int(row["lock_level"]),
        locked_until=None if row["locked_until"] is None else str(row["locked_until"]),
        updated_at=str(row["updated_at"]),
    )
