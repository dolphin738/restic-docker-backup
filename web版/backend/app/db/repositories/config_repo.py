"""config 表仓储（§4.3）：KV 读写 + 敏感项脱敏 + ``source='env'`` 键写保护。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.db.database import Database
from app.errors import EnvOverriddenError
from app.schemas.enums import ConfigSource
from app.utils.time import utc_iso

# 敏感键：API 只回 has_value + source，不回明文（§4.3 键表"敏感=是"的项）
SENSITIVE_PREFIXES: tuple[str, ...] = ("cred.",)
SENSITIVE_EXACT_KEYS: tuple[str, ...] = ("dump.password",)


@dataclass(frozen=True)
class ConfigRow:
    """config 表一行。"""

    key: str
    value: str | None
    source: str
    updated_at: str


class ConfigRepo:
    """config 表唯一访问点。"""

    def __init__(self, db: Database) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
        """
        self._db = db

    # ---------------------------------------------------------------- 读

    async def get(self, key: str) -> ConfigRow | None:
        """按键读取一行；不存在返回 None。"""
        row = await self._db.fetch_one("SELECT key, value, source, updated_at FROM config WHERE key = ?", (key,))
        return _to_row(row) if row else None

    async def get_value(self, key: str, default: str | None = None) -> str | None:
        """读取键对应的值；不存在返回 default。"""
        row = await self.get(key)
        if row is None:
            return default
        return row.value

    async def list_all(self) -> list[ConfigRow]:
        """返回全部配置项（按 key 排序）。"""
        rows = await self._db.fetch_all("SELECT key, value, source, updated_at FROM config ORDER BY key")
        return [_to_row(row) for row in rows]

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
        """
        existing = await self.get(key)
        if not force and existing is not None and existing.source == ConfigSource.ENV:
            raise EnvOverriddenError(data={"key": key})
        now = utc_iso()
        await self._db.write(
            "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, source = excluded.source, "
            "updated_at = excluded.updated_at",
            (key, value, str(source), now),
        )
        return ConfigRow(key=key, value=value, source=str(source), updated_at=now)

    async def set_many(self, patch: Mapping[str, str | None]) -> list[ConfigRow]:
        """批量写入配置项；任一键被 env 覆盖则整体拒绝（不产生部分写入）。

        Args:
            patch: 键→值映射；值为 None 表示清空该键。

        Raises:
            EnvOverriddenError: 存在被环境变量覆盖的键。
        """
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

    async def delete(self, key: str, *, force: bool = False) -> bool:
        """删除单个键；``source='env'`` 的键拒绝删除。

        Args:
            key: 配置键。
            force: 仅供启动期 env→config 同步回收失效的 env 覆盖行（``env_sync``）使用。

        Returns:
            实际删除返回 True，键不存在返回 False。
        """
        existing = await self.get(key)
        if existing is None:
            return False
        if not force and existing.source == ConfigSource.ENV:
            raise EnvOverriddenError(data={"key": key})
        await self._db.write("DELETE FROM config WHERE key = ?", (key,))
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
