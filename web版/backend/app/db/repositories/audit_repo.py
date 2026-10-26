"""audit_logs 表仓储（§4.6）：append-only，代码面只提供 INSERT 与 SELECT。

归档/清理方式见方案 §6.1 与任务历史与审计日志保留策略方案：
- 正常写读：仅 INSERT / SELECT；
- 唯一例外（S6 受控 DELETE）：按保留期滚动清理经本仓储的 :meth:`purge_older_than`
  单一出口（全表只此一处 DELETE），且归档成功后才调用，归档失败即跳过删除（fail-safe）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
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

    async def iter_rows(
        self,
        *,
        action_prefix: str | None = None,
        result: str | None = None,
        since: str | None = None,
        until: str | None = None,
        batch: int = 1000,
    ) -> AsyncIterator[AuditRow]:
        """按 ts 窗口 + 动作前缀/结果游标遍历全表（供归档/导出 NDJSON）。

        以 ``id`` 为主游标分批取（不依赖 OFFSET，避免深翻页退化）；``ts`` 比较沿用
        ``query`` 同口径（服务端入参已是归一 UTC 文本）。调用方负责把行序列化落盘/外发。

        Args:
            action_prefix: 动作名前缀（与 :meth:`query` 同转义逻辑）。
            result: 结果白名单（ok/denied/failed）。
            since/until: ``ts`` 上下界（UTC ISO 文本）。
            batch: 每批行数。
        """
        clauses: list[str] = []
        params: list[str] = []
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

        last_id = 0
        while True:
            rows = await self._db.fetch_all(
                f"SELECT id, ts, action, target, detail, result, created_at FROM audit_logs "
                f"{where} AND id > ? ORDER BY id ASC LIMIT ?",
                tuple(params) + (last_id, batch),
            )
            if not rows:
                break
            for row in rows:
                yield _to_row(row)
            last_id = rows[-1]["id"]

    async def purge_older_than(self, cutoff_iso: str, *, batch: int = 5000) -> int:
        """受控删除 ``ts < cutoff_iso`` 的行（R-020 裁决3：唯一受控 DELETE 出口）。

        分批经 :meth:`Database.write` 串行（A5）；每批先取 id 再按主键 IN 删除——比
        ``DELETE ... LIMIT`` 更稳（SQLite 的 LIMIT 删除跨驱动表现不一）。归档失败路径
        不得调用本方法（fail-safe 由 :class:`RetentionService` 保证）。

        Args:
            cutoff_iso: UTC ISO 文本上界；早于它的行被删除。
            batch: 每批删除行数。

        Returns:
            实际删除行数（幂等：重跑零删除）。
        """
        total = 0
        last_id = 0
        while True:
            ids = await self._db.fetch_all(
                "SELECT id FROM audit_logs WHERE ts < ? AND id > ? ORDER BY id ASC LIMIT ?",
                (cutoff_iso, last_id, batch),
            )
            if not ids:
                break
            id_list = [int(row["id"]) for row in ids]
            last_id = id_list[-1]
            placeholders = ",".join("?" * len(id_list))
            await self._db.write(
                f"DELETE FROM audit_logs WHERE id IN ({placeholders})",
                id_list,
            )
            total += len(id_list)
        return total


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
