"""rclone 配置段归档仓储（``rclone_config_archives`` 表，删除保护方案 D3/D10）。

**这里存的是「等价于凭据」的东西**：整段 conf key-value（值本身是 rclone 混淆态，
非明文，但混淆态可被直接用于访问后端）。因此三条硬约束：

1. **API 永不回值**：只允许回 ``type`` / ``deleted_at`` / ``used_by_repos`` / 键名列表；
2. **日志不写**：任何报错只提 remote 名，不拼装 config_json；
3. **不进配置导出**：本表不出现在 config 导入导出链路上（S3）。

生命周期：删除配置段 → 自动落一行（保留期 ``retention.rclone_archive_days``，
默认 365 天）→ 可一键写回 / 到期由每日维护清除。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.db.database import Database
from app.utils.time import utc_iso


@dataclass(frozen=True)
class RcloneArchive:
    """一行归档记录（**不含值**的元信息 + 完整 config 映射）。"""

    remote: str
    type: str
    config: dict[str, str]
    deleted_at: str
    used_by_repos: int

    @property
    def keys(self) -> list[str]:
        """该段的键名清单（可安全回显，不含值）。"""
        return sorted(self.config)


class RcloneArchiveRepo:
    """``rclone_config_archives`` 表唯一访问点。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(
        self,
        remote: str,
        *,
        type_name: str,
        config: dict[str, str],
        used_by_repos: int = 0,
    ) -> None:
        """落一条归档（同名段再次删除时覆盖旧归档——只保留最近一次）。"""
        await self._db.write(
            "INSERT OR REPLACE INTO rclone_config_archives "
            "(remote, type, config_json, deleted_at, used_by_repos) VALUES (?, ?, ?, ?, ?)",
            (remote, type_name, json.dumps(config, ensure_ascii=False), utc_iso(), used_by_repos),
        )

    async def get(self, remote: str) -> RcloneArchive | None:
        """读单个归档；不存在返回 None。"""
        row = await self._db.fetch_one(
            "SELECT remote, type, config_json, deleted_at, used_by_repos "
            "FROM rclone_config_archives WHERE remote = ?",
            (remote,),
        )
        return _to_archive(row) if row else None

    async def list_all(self) -> list[RcloneArchive]:
        """全部归档（最近删除在前）。"""
        rows = await self._db.fetch_all(
            "SELECT remote, type, config_json, deleted_at, used_by_repos "
            "FROM rclone_config_archives ORDER BY deleted_at DESC, remote ASC"
        )
        return [_to_archive(row) for row in rows]

    async def delete(self, remote: str) -> bool:
        """删除归档记录（「清理归档」动作，不动 conf、不动数据）。"""
        row = await self._db.fetch_one(
            "SELECT 1 FROM rclone_config_archives WHERE remote = ?", (remote,)
        )
        if row is None:
            return False
        await self._db.write("DELETE FROM rclone_config_archives WHERE remote = ?", (remote,))
        return True

    async def purge_older_than(self, cutoff_iso: str) -> list[str]:
        """清掉 ``deleted_at < cutoff_iso`` 的归档（保留期到期），返回被清的 remote 名。"""
        rows = await self._db.fetch_all(
            "SELECT remote FROM rclone_config_archives WHERE deleted_at < ?", (cutoff_iso,)
        )
        remotes = [str(row["remote"]) for row in rows]
        for remote in remotes:
            await self._db.write("DELETE FROM rclone_config_archives WHERE remote = ?", (remote,))
        return remotes


def _to_archive(row: Any) -> RcloneArchive:
    """把 sqlite3.Row 映射为 :class:`RcloneArchive`（解析失败按空段处理）。"""
    try:
        config = json.loads(str(row["config_json"]))
    except (TypeError, ValueError):  # 脏数据：宁可空段也不 500（恢复时会因缺 type 被拒）
        config = {}
    return RcloneArchive(
        remote=str(row["remote"]),
        type=str(row["type"]),
        config={str(k): str(v) for k, v in dict(config).items()},
        deleted_at=str(row["deleted_at"]),
        used_by_repos=int(row["used_by_repos"]),
    )
