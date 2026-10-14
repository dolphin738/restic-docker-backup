"""audit_logs 表仓储（§4.6）：append-only，代码面只提供 INSERT 与 SELECT。

归档方式见方案 §6.1：整段滚动导出后截断新文件，不覆盖已写入内容，故不存在 UPDATE/DELETE 路径。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.db.database import Database
from app.schemas.enums import AuditResult
from app.utils.log import truncate_detail
from app.utils.time import utc_iso

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


@dataclass(frozen=True)
class AuditRow:
    """audit_logs 表一行。"""

    id: int
    ts: str
    action: str
    target: str | None
    detail: str | None
    result: str
    created_at: str


class AuditRepo:
    """审计日志唯一访问点（只增不改不删）。"""

    def __init__(self, db: Database) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
        """
        self._db = db

    async def record(
        self,
        action: str,
        *,
        target: str | None = None,
        detail: Mapping[str, Any] | None = None,
        result: AuditResult | str = AuditResult.OK,
    ) -> int:
        """追加一条审计记录。

        Args:
            action: 动作名，如 ``login.failed``。
            target: 操作对象（快照 id / 策略 id / 路径 / job id / 客户端 IP）。
            detail: 结构化详情，序列化后按长度上限截断。
            result: ``ok`` / ``denied`` / ``failed``。

        Returns:
            新记录的自增 id。
        """
        now = utc_iso()
        await self._db.write(
            "INSERT INTO audit_logs (ts, action, target, detail, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, action, target, truncate_detail(detail), str(result), now),
        )
        return await self._db.last_insert_id()

    async def query(
        self,
        *,
        action: str | None = None,
        action_prefix: str | None = None,
        result: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[list[AuditRow], int]:
        """分页查询审计日志（E26，倒序返回；Q11 基线：target 搜索不做——无索引）。

        Args:
            action: 按**精确**动作名过滤；None 表示不过滤。
            action_prefix: 按动作名**前缀**过滤（如 ``restore.``）；用户输入中的
                ``%``/``_``/``\\`` 会先转义再拼 LIKE（防通配注入），右锚定可命中
                ``idx_audit_action`` 索引（F3）。
            result: 结果过滤（``ok``/``denied``/``failed``；白名单由路由层校验）。
            since: ``ts >= `` 下界（UTC ISO 文本，与时间列同构）；None 不设界。
            until: ``ts <= `` 上界；None 不设界。
            limit: 每页条数（上限 500）。
            offset: 偏移量。

        Returns:
            ``(rows, total)`` 元组。
        """
        safe_limit = max(1, min(limit, MAX_LIMIT))
        safe_offset = max(0, offset)
        clauses: list[str] = []
        params: list[str] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if action_prefix:
            clauses.append("action LIKE ? ESCAPE '\\'")
            params.append(f"{_escape_like(action_prefix)}%")
        if result:
            clauses.append("result = ?")
            params.append(result)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total_row = await self._db.fetch_one(f"SELECT COUNT(*) AS total FROM audit_logs {where}", tuple(params))
        total = int(total_row["total"]) if total_row else 0
        rows = await self._db.fetch_all(
            f"SELECT id, ts, action, target, detail, result, created_at FROM audit_logs {where} "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params) + (safe_limit, safe_offset),
        )
        return [_to_row(row) for row in rows], total

    async def distinct_actions(self) -> list[str]:
        """返回审计日志中出现过的全部动作名（字典序去重，E26 筛选下拉数据源）。"""
        rows = await self._db.fetch_all("SELECT DISTINCT action FROM audit_logs ORDER BY action ASC")
        return [str(row["action"]) for row in rows]


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符（``%``/``_``/``\\``），配合 ``ESCAPE '\\'`` 使用（防通配注入）。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_row(row: Any) -> AuditRow:
    """将 sqlite3.Row 转换为 :class:`AuditRow`。"""
    return AuditRow(
        id=int(row["id"]),
        ts=str(row["ts"]),
        action=str(row["action"]),
        target=None if row["target"] is None else str(row["target"]),
        detail=None if row["detail"] is None else str(row["detail"]),
        result=str(row["result"]),
        created_at=str(row["created_at"]),
    )
