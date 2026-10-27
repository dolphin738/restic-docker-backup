"""rclone 凭据形态记录仓储（``rclone_credential_forms`` 表，裁决 15 方案 A）。

**只存形态与 conf 现值哈希，绝不存凭据明文**（不破 S3 零回显）：

- ``obscured``：该凭据落盘形态是否混淆产物（1=混淆 / 0=明文）；
- ``value_sha256``：conf 现值哈希 —— 外部改动检测（宿主机手工改过 → 哈希对不上 →
  形态判「未知」，就地转换跳过，绝不拿猜测的形态去重写密码）。

生命周期（与 conf 段同生共死）：

- 段删除 → :meth:`delete_remote` 清掉该段全部行；
- 宿主机在外部删段 → 记录成孤儿 → :meth:`delete_orphans` 按 conf 现存段惰性对齐清理。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.db.database import Database
from app.utils.time import utc_iso

# 形态记录条目：字段 → (是否混淆形态, conf 现值 sha256)
FormEntry = tuple[bool, str]


class RcloneFormRepo:
    """``rclone_credential_forms`` 表仓储（形态记录读写）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def load(self) -> dict[str, dict[str, FormEntry]]:
        """读全部记录：``remote → field → (obscured, sha256)``。"""
        rows = await self._db.fetch_all(
            "SELECT remote, field, obscured, value_sha256 FROM rclone_credential_forms"
        )
        out: dict[str, dict[str, FormEntry]] = {}
        for row in rows:
            out.setdefault(str(row["remote"]), {})[str(row["field"])] = (
                bool(row["obscured"]),
                str(row["value_sha256"]),
            )
        return out

    async def replace_remote(self, remote: str, entries: Mapping[str, FormEntry]) -> None:
        """整段替换某 remote 的形态记录（**单事务**：先删后插，中途失败不留半截）。

        Args:
            remote: conf 段名。
            entries: 字段 → (是否混淆形态, conf 现值 sha256)；空 = 清空该段记录。
        """
        now = utc_iso()
        await self._db.conn.execute("DELETE FROM rclone_credential_forms WHERE remote = ?", (remote,))
        for field, (obscured, digest) in entries.items():
            await self._db.conn.execute(
                "INSERT OR REPLACE INTO rclone_credential_forms"
                " (remote, field, obscured, value_sha256, updated_at) VALUES (?, ?, ?, ?, ?)",
                (remote, field, 1 if obscured else 0, digest, now),
            )
        await self._db.conn.commit()

    async def delete_remote(self, remote: str) -> None:
        """删除某 remote 的全部形态记录（段被删除时同事务清账，不留垃圾）。"""
        await self._db.write("DELETE FROM rclone_credential_forms WHERE remote = ?", (remote,))

    async def delete_orphans(self, existing_remotes: Iterable[str]) -> int:
        """惰性对齐：清掉 conf 中**已不存在**的 remote 记录（宿主机外部删段产生）。

        Args:
            existing_remotes: conf 当前真实存在的段名集合。

        Returns:
            删除的行数。
        """
        keep = set(existing_remotes)
        rows = await self._db.fetch_all("SELECT DISTINCT remote FROM rclone_credential_forms")
        orphans = [str(row["remote"]) for row in rows if str(row["remote"]) not in keep]
        for remote in orphans:
            await self._db.write("DELETE FROM rclone_credential_forms WHERE remote = ?", (remote,))
        return len(orphans)
