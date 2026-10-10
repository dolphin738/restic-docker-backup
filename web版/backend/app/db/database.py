"""SQLite 访问层（§7.7 / R-010）：aiosqlite 单例 + WAL + user_version 幂等迁移器。

工程约定（方案 §5）：
- ``journal_mode=WAL`` + ``busy_timeout=5000`` + ``foreign_keys=ON``；
- 单写连接：全部写操作经 :meth:`Database.write` 串行化（SQLite 单写多读，规避 SQLITE_BUSY）；
- schema 版本由 ``PRAGMA user_version`` 记录，启动时按序执行 ``migrations/000N_*.sql``，
  每个文件一个事务、幂等可重复执行。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_DB_INSTANCE: "Database | None" = None


class Database:
    """aiosqlite 单例封装：唯一持有连接的对象。"""

    def __init__(self, db_path: str | Path, migrations_dir: str | Path | None = None) -> None:
        """构造数据库对象（尚未建立连接）。

        Args:
            db_path: SQLite 文件路径，如 ``/data/restic.db``。
            migrations_dir: 迁移脚本目录；缺省为本模块同级 ``migrations/``。
        """
        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir) if migrations_dir else _MIGRATIONS_DIR
        self._conn: aiosqlite.Connection | None = None

    # ---------------------------------------------------------------- 生命周期

    async def connect(self) -> None:
        """建立连接、应用 PRAGMA 并执行迁移（幂等）。"""
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # WAL 与 busy_timeout 为库级持久化设置；foreign_keys 为连接级，须每次连接开启
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.commit()
        self._conn = conn
        applied = await self.migrate()
        if applied:
            logger.info("数据库迁移完成：应用 %d 个脚本，当前 user_version=%d", applied, await self.user_version())

    async def close(self) -> None:
        """关闭连接（幂等）。"""
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        """返回已建立的连接；未连接时抛错。"""
        if self._conn is None:
            raise RuntimeError("Database 尚未连接，请先调用 connect()")
        return self._conn

    # ---------------------------------------------------------------- 迁移器

    async def user_version(self) -> int:
        """返回当前 schema 版本（``PRAGMA user_version``）。"""
        row = await self.fetch_one("PRAGMA user_version")
        return int(row[0]) if row else 0

    async def migrate(self) -> int:
        """按序执行未应用的迁移脚本。

        Returns:
            本次实际应用的脚本数量（0 表示已是最新）。
        """
        current = await self.user_version()
        scripts = sorted(
            self.migrations_dir.glob("*.sql"),
            key=lambda p: _script_index(p.name),
        )
        applied = 0
        for script in scripts:
            version = _script_index(script.name)
            if version <= current:
                continue
            sql = script.read_text(encoding="utf-8")
            # 每个脚本包在一个事务内：BEGIN … COMMIT（DDL 均以 IF NOT EXISTS 书写，可重复执行）
            await self.conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
            await self.conn.execute(f"PRAGMA user_version = {version}")
            await self.conn.commit()
            logger.info("已应用迁移脚本 %s（user_version=%d）", script.name, version)
            applied += 1
        return applied

    # ---------------------------------------------------------------- 读写

    async def write(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """执行写语句并提交（全部写经此方法串行化）。

        Args:
            sql: 带 ``?`` 占位符的 SQL。
            params: 绑定参数。
        """
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """执行查询并返回全部行（无匹配时返回空列表）。"""
        async with self.conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return list(rows)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        """执行查询并返回首行（无匹配时返回 None）。"""
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def last_insert_id(self) -> int:
        """返回最近一次 INSERT 的自增 id（audit_logs 写入后取用）。"""
        row = await self.fetch_one("SELECT last_insert_rowid() AS id")
        return int(row["id"]) if row else 0


def _script_index(filename: str) -> int:
    """从迁移脚本文件名提取版本号（``0001_init.sql`` → 1）。"""
    return int(filename.split("_", 1)[0])


async def init_database(db_path: str | Path | None = None) -> Database:
    """初始化（或复用）进程内数据库单例并建立连接。

    Args:
        db_path: 数据库文件路径；缺省取 ``settings.db_path``。

    Returns:
        已连接并完成迁移的 :class:`Database` 单例。
    """
    global _DB_INSTANCE
    if _DB_INSTANCE is None:
        from app.settings import get_settings  # 延迟导入：避免与 settings 模块形成导入环

        settings = get_settings()
        _DB_INSTANCE = Database(db_path or settings.db_path)
        await _DB_INSTANCE.connect()
    return _DB_INSTANCE


def get_database() -> Database:
    """返回已初始化的数据库单例；未初始化时抛错。"""
    if _DB_INSTANCE is None:
        raise RuntimeError("Database 尚未初始化，请先调用 init_database()")
    return _DB_INSTANCE


async def close_database() -> None:
    """关闭并清空数据库单例（lifespan 收尾 / 测试隔离用）。"""
    global _DB_INSTANCE
    if _DB_INSTANCE is None:
        return
    await _DB_INSTANCE.close()
    _DB_INSTANCE = None
