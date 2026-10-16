"""config 表仓储（§4.3）：KV 读写 + 敏感项脱敏 + ``source='env'`` 键写保护 + 派生键维护。

派生键（只读，服务端自动维护）：
- ``repo.type``：由 ``repo.url`` 的协议前缀推导（``s3:``→s3、``b2:``→b2、其余→local），
  在**任何** ``repo.url`` 写入路径（E07 界面 / env 同步）上同源落库；直接写它 → 409 ``READONLY_KEY``。

R-025 凭据加密（P3-A，owner 2026-10-16 裁决默认开）：注入 :class:`EncryptionState` 后，
敏感键（``cred.*`` / ``dump.password`` / ``notify.*``）写入即加密、读取透明解密——
**读侧恒按 ``enc:v1:`` 前缀判定，与开关无关**（崩溃安全，见 services/crypto.py）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.db.database import Database
from app.errors import CredentialDecryptError, EnvOverriddenError, ReadonlyKeyError
from app.schemas.enums import ConfigSource
from app.services.crypto import CredentialCrypto
from app.utils.time import utc_iso

if TYPE_CHECKING:  # 避免运行时循环导入（crypto 不依赖本模块，仅类型注解需要）
    from app.services.crypto import EncryptionState

logger = logging.getLogger(__name__)

# 敏感键：API 只回 has_value + source，不回明文（§4.3 键表"敏感=是"的项）
SENSITIVE_PREFIXES: tuple[str, ...] = ("cred.",)
# R-018：通知渠道 URL 内嵌令牌（healthchecks ping UUID / webhook 地址），同凭据口径脱敏
SENSITIVE_EXACT_KEYS: tuple[str, ...] = ("dump.password", "notify.healthchecks_url", "notify.webhook.url")

# 仓库地址与派生类型键（§4.3 键表）
REPO_URL_KEY = "repo.url"
REPO_TYPE_KEY = "repo.type"


def derive_repo_type(url: str | None) -> str:
    """按仓库地址的协议前缀推导 ``repo.type``（§4.3：s3 / b2 / local）。

    Args:
        url: 仓库地址，如 ``s3:https://s3.endpoint/bucket`` 或 ``/repo``。

    Returns:
        ``s3`` / ``b2`` / ``local`` 之一（空地址回落 ``local``）。
    """
    lowered = (url or "").strip().lower()
    if lowered.startswith("s3:"):
        return "s3"
    if lowered.startswith("b2:"):
        return "b2"
    return "local"


@dataclass(frozen=True)
class ConfigRow:
    """config 表一行。"""

    key: str
    value: str | None
    source: str
    updated_at: str


class ConfigRepo:
    """config 表唯一访问点。"""

    def __init__(self, db: Database, encryption: EncryptionState | None = None) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
            encryption: 凭据加密运行态（R-025）；None 时行为与本特性前一致
                （明文存储；但若库里已有密文，读取仍尝试解密，无密钥组件则报错
                而非把密文当明文透出）。
        """
        self._db = db
        self._encryption = encryption

    # ---------------------------------------------------------------- 读

    def _maybe_decrypt(self, value: str | None) -> str | None:
        """读侧透明解密：非 ``enc:v1:`` 前缀原样返回（与开关无关，崩溃安全）。"""
        if value is None or not CredentialCrypto.is_encrypted(value):
            return value
        if self._encryption is None:
            # 密文存在却无加密组件（装配缺失）——报错，绝不把密文当明文透给业务
            raise CredentialDecryptError()
        return self._encryption.crypto.decrypt(value)

    async def get(self, key: str) -> ConfigRow | None:
        """按键读取一行；不存在返回 None。"""
        row = await self._db.fetch_one("SELECT key, value, source, updated_at FROM config WHERE key = ?", (key,))
        if not row:
            return None
        return self._decrypt_row(row)

    async def get_value(self, key: str, default: str | None = None) -> str | None:
        """读取键对应的值；不存在返回 default。"""
        row = await self.get(key)
        if row is None:
            return default
        return row.value

    async def list_all(self) -> list[ConfigRow]:
        """返回全部配置项（按 key 排序；密文透明解密）。"""
        rows = await self._db.fetch_all("SELECT key, value, source, updated_at FROM config ORDER BY key")
        return [self._decrypt_row(row) for row in rows]

    async def list_all_raw(self) -> list[ConfigRow]:
        """返回全部配置项**的存储原形**（不解密；仅供加密迁移判定形态，勿用于业务）。"""
        rows = await self._db.fetch_all("SELECT key, value, source, updated_at FROM config ORDER BY key")
        return [_to_row(row) for row in rows]

    def _decrypt_row(self, row: Any) -> ConfigRow:
        """行转换 + 读侧透明解密（业务读取统一入口）。"""
        base = _to_row(row)
        return ConfigRow(
            key=base.key,
            value=self._maybe_decrypt(base.value),
            source=base.source,
            updated_at=base.updated_at,
        )

    # ---------------------------------------------------------------- 写

    async def set(
        self,
        key: str,
        value: str | None,
        *,
        source: ConfigSource = ConfigSource.UI,
        force: bool = False,
    ) -> ConfigRow:
        """写入（upsert）单个键；``source='env'`` 的键拒绝界面写入。

        Args:
            key: 配置键，如 ``repo.url``。
            value: 配置值；None 表示清空。
            source: 来源标记，界面写入恒为 ``ui``。
            force: 仅供启动期 env→config 同步使用（``env_sync``），
                跳过 ``source='env'`` 写保护；业务写入路径必须保持 False。

        Raises:
            EnvOverriddenError: 该键已由环境变量覆盖且 ``force=False``（409 ENV_OVERRIDDEN）。
            ReadonlyKeyError: 试图直接写只读派生键 ``repo.type``（409 READONLY_KEY）。
        """
        if key == REPO_TYPE_KEY:
            raise ReadonlyKeyError(data={"key": key})
        await self._guard_env_source(key, force=force)
        stored = self._maybe_encrypt(key, value)
        now = utc_iso()
        await self._upsert(key, stored, str(source), now)
        if key == REPO_URL_KEY:
            # 派生键同源落库：repo.url 的任何写入路径都同步维护 repo.type（单一事实来源）
            if value:
                await self._upsert(REPO_TYPE_KEY, derive_repo_type(value), str(source), now)
            else:
                await self._db.write("DELETE FROM config WHERE key = ?", (REPO_TYPE_KEY,))
        return ConfigRow(key=key, value=value, source=str(source), updated_at=now)

    async def _guard_env_source(self, key: str, *, force: bool) -> None:
        """env 覆盖写保护：只读 ``source`` 列，**不解密旧值**。

        刻意不走 :meth:`get`——密码文件被更换后旧密文不可解密，若守卫先解密，
        该键将永远无法经 E07 覆写（用户连「重新录入凭据」的恢复路径都被堵死）。
        """
        if force:
            return
        row = await self._db.fetch_one("SELECT source FROM config WHERE key = ?", (key,))
        if row is not None and str(row["source"]) == ConfigSource.ENV:
            raise EnvOverriddenError(data={"key": key})

    async def _upsert(self, key: str, value: str | None, source: str, now: str | None = None) -> None:
        """裸 upsert（不做任何守卫）：供 :meth:`set` 与派生键写入复用。"""
        await self._db.write(
            "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, source = excluded.source, "
            "updated_at = excluded.updated_at",
            (key, value, source, now or utc_iso()),
        )

    def _maybe_encrypt(self, key: str, value: str | None) -> str | None:
        """写侧透明加密：开关开 + 敏感键 + 非空值 → 密文；其余原样（与读侧前缀判定对称）。"""
        if value is None or value == "" or self._encryption is None or not self._encryption.enabled:
            return value
        if not self.is_sensitive(key):
            return value
        return self._encryption.crypto.encrypt(value)

    async def migrate_encryption(self, *, encrypt: bool) -> int:
        """敏感键存量形态搬运（R-025 开关/启动期迁移；幂等）。

        Args:
            encrypt: True=明文→密文（启用）；False=密文→明文（关闭）。

        Returns:
            实际搬运的键数（已就位的不重复计）。
        """
        if self._encryption is None:
            return 0
        crypto = self._encryption.crypto
        changed = 0
        now = utc_iso()
        for row in await self.list_all_raw():
            if not row.value or not self.is_sensitive(row.key):
                continue
            encrypted = crypto.is_encrypted(row.value)
            if encrypt and not encrypted:
                await self._upsert(row.key, crypto.encrypt(row.value), row.source, now)
                changed += 1
            elif not encrypt and encrypted:
                try:
                    plaintext = crypto.decrypt(row.value)
                except CredentialDecryptError:
                    # 密码文件被更换 ⇒ 该键旧密文不可解密：跳过并告警（不中断整批），
                    # 保留原密文形态；恢复手段=经 E07 覆写该键（set 守卫不解密旧值）
                    logger.warning("凭据加密迁移跳过 %s：旧密文无法解密（密码文件可能已被更换）", row.key)
                    continue
                await self._upsert(row.key, plaintext, row.source, now)
                changed += 1
        return changed

    async def set_many(self, patch: Mapping[str, str | None]) -> list[ConfigRow]:
        """批量写入配置项；任一键被 env 覆盖则整体拒绝（不产生部分写入）。

        Args:
            patch: 键→值映射；值为 None 表示清空该键。

        Raises:
            EnvOverriddenError: 存在被环境变量覆盖的键。
            ReadonlyKeyError: patch 中含只读派生键 ``repo.type``。
        """
        if REPO_TYPE_KEY in patch:
            raise ReadonlyKeyError(data={"key": REPO_TYPE_KEY})
        keys = list(patch.keys())
        if not keys:
            return []
        placeholders = ",".join("?" * len(keys))
        rows = await self._db.fetch_all(
            f"SELECT key, value, source, updated_at FROM config WHERE key IN ({placeholders}) "
            "AND source = 'env'",
            tuple(keys),
        )
        if rows:
            blocked = [str(row["key"]) for row in rows]
            raise EnvOverriddenError(data={"keys": blocked})
        return [await self.set(key, value) for key, value in patch.items()]

    async def backfill_repo_type(self) -> bool:
        """存量数据回填：库里有 ``repo.url`` 但缺 ``repo.type`` 时按协议推导补写。

        供启动期调用一次，保证老库升级后 ``repo.type`` 也有值（前端无需再从 URL 推导）。

        Returns:
            实际回填返回 True。
        """
        url = await self.get_value(REPO_URL_KEY)
        if not url:
            return False
        existing_type = await self.get_value(REPO_TYPE_KEY)
        if existing_type is not None:
            return False
        await self._upsert(REPO_TYPE_KEY, derive_repo_type(url), str(ConfigSource.UI))
        return True

    async def delete(self, key: str, *, force: bool = False) -> bool:
        """删除单个键；``source='env'`` 的键拒绝删除。

        同 :meth:`_guard_env_source`：守卫只读 ``source`` 列——密码文件被更换后
        旧密文不可解密，但删除（恢复手段之一）必须仍然可行。
        """
        row = await self._db.fetch_one("SELECT source FROM config WHERE key = ?", (key,))
        if row is None:
            return False
        if not force and str(row["source"]) == ConfigSource.ENV:
            raise EnvOverriddenError(data={"key": key})
        await self._db.write("DELETE FROM config WHERE key = ?", (key,))
        if key == REPO_URL_KEY:
            # 派生键跟随清空：repo.url 没了，repo.type 不应残留旧值
            await self._db.write("DELETE FROM config WHERE key = ?", (REPO_TYPE_KEY,))
        return True

    # ---------------------------------------------------------------- 脱敏

    @staticmethod
    def is_sensitive(key: str) -> bool:
        """判断键是否为敏感项（API 只回 has_value）。"""
        return key in SENSITIVE_EXACT_KEYS or key.startswith(SENSITIVE_PREFIXES)

    def mask(self, row: ConfigRow) -> dict[str, Any]:
        """按敏感性脱敏后输出：敏感项回 ``has_value`` + ``source``，不回明文。"""
        sensitive = self.is_sensitive(row.key)
        payload: dict[str, Any] = {
            "key": row.key,
            "has_value": row.value is not None and row.value != "",
            "source": row.source,
            "sensitive": sensitive,
        }
        if not sensitive:
            payload["value"] = row.value
        return payload


def _to_row(row: Any) -> ConfigRow:
    """将 sqlite3.Row 转换为 :class:`ConfigRow`。"""
    return ConfigRow(
        key=str(row["key"]),
        value=None if row["value"] is None else str(row["value"]),
        source=str(row["source"]),
        updated_at=str(row["updated_at"]),
    )
