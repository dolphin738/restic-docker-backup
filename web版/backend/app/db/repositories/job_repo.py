"""jobs 表仓储（§4.5）：任务元数据读写（状态机推进、进度节流、启动恢复）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.constants import ACTIVE_JOB_STATUSES
from app.db.database import Database
from app.utils.time import utc_iso

_COLUMNS = (
    "id", "type", "status", "trigger", "policy_id", "snapshot_id", "bytes_done", "files_done",
    "skipped_count", "last_event_id", "log_path", "summary", "started_at", "finished_at",
    "exit_code", "created_at",
)
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM jobs"


@dataclass(frozen=True)
class Job:
    """jobs 表一行（§4.5）。"""

    id: str
    type: str
    status: str
    trigger: str
    policy_id: int | None
    snapshot_id: str | None
    bytes_done: int | None
    files_done: int | None
    skipped_count: int
    last_event_id: int
    log_path: str
    summary: str | None
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    created_at: str


class JobRepo:
    """jobs 表唯一访问点。"""

    def __init__(self, db: Database) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
        """
        self._db = db

    # ---------------------------------------------------------------- 读

    async def get(self, job_id: str) -> Job | None:
        """按 id 读取任务；不存在返回 None。"""
        row = await self._db.fetch_one(f"{_SELECT} WHERE id = ?", (job_id,))
        return _to_job(row) if row else None

    async def list_active(self) -> list[Job]:
        """返回进行中（running/pending）任务，按创建时间升序（先来先服务）。"""
        placeholders = ",".join("?" * len(ACTIVE_JOB_STATUSES))
        rows = await self._db.fetch_all(
            f"{_SELECT} WHERE status IN ({placeholders}) ORDER BY created_at ASC",
            tuple(ACTIVE_JOB_STATUSES),
        )
        return [_to_job(row) for row in rows]

    async def list_page(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        """分页查询任务（P2 完整列表用；P1 亦可复用于进行中列表）。

        Args:
            statuses: 状态过滤；None 表示不过滤。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            ``(jobs, total)``。
        """
        where, params = ("", ())
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            where = f"WHERE status IN ({placeholders})"
            params = tuple(statuses)
        total_row = await self._db.fetch_one(f"SELECT COUNT(*) AS total FROM jobs {where}", params)
        total = int(total_row["total"]) if total_row else 0
        rows = await self._db.fetch_all(
            f"{_SELECT} {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + (max(1, limit), max(0, offset)),
        )
        return [_to_job(row) for row in rows], total

    # ---------------------------------------------------------------- 写

    async def create(
        self,
        *,
        job_id: str,
        job_type: str,
        trigger: str,
        log_path: str,
        policy_id: int | None = None,
        snapshot_id: str | None = None,
        status: str = "pending",
    ) -> Job:
        """插入新任务行（默认 pending）。

        Args:
            job_id: ULID 任务 id。
            job_type: 任务类型（E12 白名单）。
            trigger: ``manual`` / ``scheduled``。
            log_path: 日志文件路径（插入即确定，供 SSE 回放）。
            policy_id: 关联策略；可为空。
            snapshot_id: 关联快照；可为空。
            status: 初始状态，默认 ``pending``。

        Returns:
            新建的 :class:`Job`。
        """
        now = utc_iso()
        await self._db.write(
            "INSERT INTO jobs (id, type, status, trigger, policy_id, snapshot_id, skipped_count, "
            "last_event_id, log_path, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
            (job_id, job_type, status, trigger, policy_id, snapshot_id, log_path, now),
        )
        created = await self.get(job_id)
        if created is None:  # pragma: no cover - 理论不可达
            raise RuntimeError(f"任务 {job_id} 创建后无法读回")
        return created

    async def update_status(
        self,
        job_id: str,
        status: str,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        exit_code: int | None = None,
        summary: str | None = None,
    ) -> None:
        """推进任务状态（started_at/finished_at/exit_code/summary 仅在传值时更新）。"""
        assignments = ["status = ?"]
        params: list[Any] = [status]
        if started_at is not None:
            assignments.append("started_at = ?")
            params.append(started_at)
        if finished_at is not None:
            assignments.append("finished_at = ?")
            params.append(finished_at)
        if exit_code is not None:
            assignments.append("exit_code = ?")
            params.append(exit_code)
        if summary is not None:
            assignments.append("summary = ?")
            params.append(summary)
        params.append(job_id)
        await self._db.write(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", tuple(params))

    async def update_progress(
        self,
        job_id: str,
        *,
        bytes_done: int | None = None,
        files_done: int | None = None,
        skipped_count: int | None = None,
        last_event_id: int | None = None,
    ) -> None:
        """节流写入进度字段（§6.2：≥1s 间隔调用一次）。"""
        assignments: list[str] = []
        params: list[Any] = []
        if bytes_done is not None:
            assignments.append("bytes_done = ?")
            params.append(bytes_done)
        if files_done is not None:
            assignments.append("files_done = ?")
            params.append(files_done)
        if skipped_count is not None:
            assignments.append("skipped_count = ?")
            params.append(skipped_count)
        if last_event_id is not None:
            assignments.append("last_event_id = ?")
            params.append(last_event_id)
        if not assignments:
            return
        params.append(job_id)
        await self._db.write(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", tuple(params))

    async def finalize(
        self,
        job_id: str,
        status: str,
        *,
        exit_code: int,
        summary: str | None = None,
        bytes_done: int | None = None,
        files_done: int | None = None,
        skipped_count: int | None = None,
        last_event_id: int | None = None,
    ) -> None:
        """写入终态（success/failed/interrupted/cancelled）与汇总数据。"""
        await self.update_status(
            job_id,
            status,
            finished_at=utc_iso(),
            exit_code=exit_code,
            summary=summary,
        )
        await self.update_progress(
            job_id,
            bytes_done=bytes_done,
            files_done=files_done,
            skipped_count=skipped_count,
            last_event_id=last_event_id,
        )

    async def add_skipped(self, job_id: str, delta: int) -> None:
        """累加 skipped 计数（非 root 档静默跳过，R-012）。"""
        if delta <= 0:
            return
        await self._db.write(
            "UPDATE jobs SET skipped_count = skipped_count + ? WHERE id = ?", (int(delta), job_id)
        )

    async def set_snapshot(self, job_id: str, snapshot_id: str) -> None:
        """回填任务关联的快照 id（backup 完成后由 summary 事件得到）。

        原先由 JobRunner._set_snapshot 直接拼 SQL，违反 A1「DB 访问只在
        repositories/」；按唯一实现点纪律收敛到此（规范 §3.1 A1）。
        """
        await self._db.write("UPDATE jobs SET snapshot_id = ? WHERE id = ?", (snapshot_id, job_id))

    async def mark_interrupted_on_startup(self) -> int:
        """启动恢复：把遗留的 running/pending 改标 interrupted（§6.5）。

        Returns:
            被改标的条数。
        """
        placeholders = ",".join("?" * len(ACTIVE_JOB_STATUSES))
        row = await self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM jobs WHERE status IN ({placeholders})",
            tuple(ACTIVE_JOB_STATUSES),
        )
        affected = int(row["total"]) if row else 0
        if affected:
            await self._db.write(
                f"UPDATE jobs SET status = 'interrupted', summary = COALESCE(summary, '容器重启中断') "
                f"WHERE status IN ({placeholders})",
                tuple(ACTIVE_JOB_STATUSES),
            )
        return affected


def _to_job(row: Any) -> Job:
    """将 sqlite3.Row 转换为 :class:`Job`。"""
    return Job(
        id=str(row["id"]),
        type=str(row["type"]),
        status=str(row["status"]),
        trigger=str(row["trigger"]),
        policy_id=None if row["policy_id"] is None else int(row["policy_id"]),
        snapshot_id=None if row["snapshot_id"] is None else str(row["snapshot_id"]),
        bytes_done=None if row["bytes_done"] is None else int(row["bytes_done"]),
        files_done=None if row["files_done"] is None else int(row["files_done"]),
        skipped_count=int(row["skipped_count"] or 0),
        last_event_id=int(row["last_event_id"] or 0),
        log_path=str(row["log_path"]),
        summary=None if row["summary"] is None else str(row["summary"]),
        started_at=None if row["started_at"] is None else str(row["started_at"]),
        finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
        exit_code=None if row["exit_code"] is None else int(row["exit_code"]),
        created_at=str(row["created_at"]),
    )
