"""服务端会话（§8）：token 生成、SHA-256 落库、24h 滑动续期、过期行惰性清理。

明文 token 只存在于 cookie，不落库不落日志（§4.2）；库中仅存 ``SHA-256(token)``。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Request

from app.db.database import Database
from app.settings import Settings
from app.utils.time import add_seconds, now_utc, utc_iso

TOKEN_BYTES = 32


@dataclass(frozen=True)
class SessionContext:
    """通过校验的会话上下文（依赖注入结果）。"""

    token: str
    token_hash: str
    created_at: str
    last_seen_at: str
    expires_at: str


def hash_token(token: str) -> str:
    """计算会话 token 的 SHA-256（十六进制）——库中唯一存储形态。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """生成 URL 安全的随机会话 token（256 位熵）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def cookie_secure(request: Request, settings: Settings) -> bool:
    """判定会话 cookie 是否需要 ``Secure`` 属性（§8）。

    反代终结 HTTPS 时置 Secure：由 ``force_https`` 配置或 ``X-Forwarded-Proto`` 头判定。
    """
    if settings.force_https:
        return True
    if settings.trust_proxy:
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if proto == "https":
            return True
    return request.url.scheme == "https"


class SessionService:
    """会话签发 / 校验 / 续期 / 销毁。"""

    def __init__(self, db: Database, settings: Settings) -> None:
        """构造会话服务。

        Args:
            db: 数据库单例。
            settings: 运行期配置（TTL、cookie 名）。
        """
        self._db = db
        self._settings = settings

    @property
    def ttl_seconds(self) -> int:
        """会话有效期（秒），默认 24h。"""
        return max(1, self._settings.session_ttl_hours) * 3600

    async def create(self) -> str:
        """签发新会话并落库。

        Returns:
            明文 token（仅此一次出现，需由调用方写入 cookie）。
        """
        token = generate_token()
        now = now_utc()
        created_at = utc_iso(now)
        expires_at = utc_iso(add_seconds(now, self.ttl_seconds))
        await self._db.write(
            "INSERT INTO sessions (token_hash, last_seen_at, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (hash_token(token), created_at, expires_at, created_at),
        )
        return token

    async def get_valid(self, token: str | None) -> SessionContext | None:
        """校验 token 并执行滑动续期。

        Args:
            token: cookie 中的明文 token。

        Returns:
            有效会话上下文；token 缺失/不存在/已过期时返回 None。
        """
        if not token:
            return None
        token_hash = hash_token(token)
        row = await self._db.fetch_one(
            "SELECT token_hash, last_seen_at, expires_at, created_at FROM sessions WHERE token_hash = ?",
            (token_hash,),
        )
        if row is None:
            return None
        now = now_utc()
        expires_at = row["expires_at"]
        if expires_at and str(expires_at) <= utc_iso(now):
            await self._db.write("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        # 滑动续期：每次认证请求刷新 last_seen_at 与 expires_at（§8）
        last_seen = utc_iso(now)
        new_expires = utc_iso(add_seconds(now, self.ttl_seconds))
        await self._db.write(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
            (last_seen, new_expires, token_hash),
        )
        return SessionContext(
            token=token,
            token_hash=str(row["token_hash"]),
            created_at=str(row["created_at"]),
            last_seen_at=last_seen,
            expires_at=new_expires,
        )

    async def destroy(self, token: str | None) -> None:
        """销毁单个会话（E02 登出）。"""
        if not token:
            return
        await self._db.write("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))

    async def destroy_others(self, token: str | None) -> int:
        """销毁除指定会话外的全部会话（E28 改密后"销毁其他 session"）。

        Returns:
            被销毁的会话条数。
        """
        current = hash_token(token) if token else ""
        cursor_row = await self._db.fetch_one("SELECT COUNT(*) AS total FROM sessions WHERE token_hash <> ?", (current,))
        affected = int(cursor_row["total"]) if cursor_row else 0
        await self._db.write("DELETE FROM sessions WHERE token_hash <> ?", (current,))
        return affected

    async def destroy_all(self) -> int:
        """销毁全部会话。

        Returns:
            被销毁的会话条数。
        """
        row = await self._db.fetch_one("SELECT COUNT(*) AS total FROM sessions")
        affected = int(row["total"]) if row else 0
        await self._db.write("DELETE FROM sessions")
        return affected

    async def purge_expired(self) -> int:
        """惰性清理过期会话行（启动期调用，§8）。

        Returns:
            被清理的条数。
        """
        row = await self._db.fetch_one("SELECT COUNT(*) AS total FROM sessions WHERE expires_at <= ?", (utc_iso(),))
        affected = int(row["total"]) if row else 0
        if affected:
            await self._db.write("DELETE FROM sessions WHERE expires_at <= ?", (utc_iso(),))
        return affected

    @staticmethod
    def constant_time_compare(left: str, right: str) -> bool:
        """常量时间字符串比较（供需要自行比对哈希的场景使用）。"""
        return hmac.compare_digest(left, right)
