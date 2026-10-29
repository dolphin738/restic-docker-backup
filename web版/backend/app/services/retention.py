"""任务历史与日志保留（R-020 / 设计 §3.4）：每日维护清理 jobs 行与 /data/jobs 日志。

语义（Q12 基线）：
- jobs 行：``retention.jobs_days``（缺省 90，E07 可调）之前创建的**终态**行删除
  （running/pending 绝不触碰，见 :meth:`JobRepo.purge_older_than`）；
- 日志文件：``retention.logs_days``（可选键）之前修改的 ``<jobs_dir>/*.log`` 删除，
  **键缺省时回退 ``retention.jobs_days``**（实现为读取时 fallback，无新列——设计 §1.2）；
- ``days <= 0`` 视为「不启用」（与 ``prune_interval_days`` 同口径），整次清理跳过；
- 删除动作记审计 ``retention.cleanup``（detail 含行数/文件数/字节数，S6 只增不改）；
- 幂等：清理是按阈值的绝对判定，重跑零删除（验收要点）。

审计日志保留（R-020 裁决1 / 裁决3，方案 §6.1）：
- ``retention.audit_days``（缺省 365）之前的审计行先**压缩归档**为
  ``<audit_archive_dir>/audit-<YYYY>.jsonl.gz``（追加不覆盖），**归档成功后才按
  受控 DELETE 出口 purge 对应行**（fail-safe：归档失败即跳过删除，宁可多留也不丢）；
- 归档文件自身按 ``retention.audit_archive_days``（缺省 365）清理（``<=0`` 不清理）；
- 归档目录默认 ``settings.audit_archive_dir``（``/data/audit/archive``），可由
  ``retention.audit_archive_dir`` DB 键覆盖；
- ``retention.audit_archive_cold_target``（冷存目标）**仅占位/提示**，应用不自动同步
  （裁决3 选 B：外送交给既有 restic 备份 / rclone / 挂卷，部署文档指路）；
- 归档/清理分别记 ``audit.archive`` / ``audit.cleanup``（S6）。

红线落点：A1（SQL 只在 repo）、A5（写走 Database.write 串行）、C8（时间只走
utils/time）、S1（文件只触碰 ``settings.jobs_dir`` 与 ``audit_archive_dir`` 白名单，
不递归子目录）、S6（审计只增不改，DELETE 唯一出口在 AuditRepo）。
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.constants import (
    AUDIT_ARCHIVE,
    AUDIT_ARCHIVE_FILE_GLOB,
    AUDIT_CLEANUP,
    AUDIT_RETENTION_CLEANUP,
    RETENTION_AUDIT_ARCHIVE_DAYS_DEFAULT,
    RETENTION_AUDIT_ARCHIVE_DAYS_KEY,
    RETENTION_AUDIT_ARCHIVE_DIR_KEY,
    RETENTION_AUDIT_DAYS_DEFAULT,
    RETENTION_AUDIT_DAYS_KEY,
    RETENTION_JOBS_DAYS_DEFAULT,
    RETENTION_JOBS_DAYS_KEY,
    RETENTION_LOGS_DAYS_KEY,
    RETENTION_RCLONE_ARCHIVE_DAYS_DEFAULT,
    RETENTION_RCLONE_ARCHIVE_DAYS_KEY,
    RETENTION_REPO_ARCHIVE_DAYS_DEFAULT,
    RETENTION_REPO_ARCHIVE_DAYS_KEY,
    RETENTION_REPO_PASSFILE_DAYS_DEFAULT,
    RETENTION_REPO_PASSFILE_DAYS_KEY,
    RETENTION_REPO_TOMBSTONE_DAYS_DEFAULT,
    RETENTION_REPO_TOMBSTONE_DAYS_KEY,
)
from app.db.repositories.audit_repo import AuditRepo, AuditRow
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.rclone_archive_repo import RcloneArchiveRepo
from app.db.repositories.repo_repo import ReposRepo
from app.settings import Settings, get_settings
from app.utils.passfile import reap_orphan_passfiles, remove_passfile
from app.utils.time import now_utc, utc_iso

logger = logging.getLogger(__name__)

# 单次流式归档时，攒够多少行再落盘一次（控制内存，避免超期行一次性进内存）
ARCHIVE_BUFFER_ROWS = 2000


@dataclass(frozen=True)
class RetentionReport:
    """一次清理的结果（审计 detail 与调度日志共用）。

    注意：本报告只描述 **jobs 行 + 任务日志** 的清理（任务历史页「立即清理」与每日
    维护共用的口径）；审计归档/清理走独立的 ``audit.archive`` / ``audit.cleanup``
    审计条目，不混入本报告。
    """

    jobs_deleted: int
    log_files_deleted: int
    log_bytes_freed: int
    jobs_days: int
    logs_days: int


@dataclass(frozen=True)
class AuditArchiveReport:
    """一次审计归档/清理的结果（供测试与调度日志断言）。"""

    archived_rows: int
    archived_bytes: int
    purged_rows: int
    audit_days: int
    archive_file: str | None


@dataclass
class ArchiveCleanupReport:
    """一次归档生命周期清理的结果（删除保护方案 §5 四档）。

    只记「记录」层面的变化：``repos_expired`` = 归档期到期转墓碑的仓库 id；
    ``tombstones_deleted`` = 墓碑到期删除；``rclone_archives_expired`` = 配置归档到期删除；
    ``passfiles_reaped`` = 无主且超龄的**密码文件**被回收的 repo_id（第四档，
    兜住 hard 删除 / 释放 ID / 上一轮清理失败留下的孤儿）。
    **一个字节的备份数据都不会出现在这里**——数据永不自动删。
    """

    repos_expired: list[str] = field(default_factory=list)
    tombstones_deleted: list[str] = field(default_factory=list)
    rclone_archives_expired: list[str] = field(default_factory=list)
    passfiles_reaped: list[str] = field(default_factory=list)


class RetentionService:
    """任务历史/日志保留服务（挂调度器每日维护任务 ``maintenance-retention``）。"""

    def __init__(
        self,
        jobs: JobRepo,
        config: ConfigRepo,
        audit: AuditRepo,
        settings: Settings | None = None,
        *,
        repos: ReposRepo | None = None,
        rclone_archives: RcloneArchiveRepo | None = None,
    ) -> None:
        """构造保留服务。

        Args:
            jobs: jobs 表仓储（行清理经 :meth:`JobRepo.purge_older_than`）。
            config: 配置仓储（``retention.*`` 键族）。
            audit: 审计仓储（``retention.cleanup`` / ``audit.archive`` / ``audit.cleanup`` 埋点，S6）。
            settings: 运行期配置（jobs_dir / audit_archive_dir 白名单来源）；缺省取进程单例。
        """
        self._jobs = jobs
        self._config = config
        self._audit = audit
        self._settings = settings or get_settings()
        # 归档生命周期（删除保护方案 §5）：可选端口——测试基座不注入时整段跳过，
        # 行为差异仅「不做归档到期清理」，既有 jobs/审计清理口径完全不变。
        self._repos = repos
        self._rclone_archives = rclone_archives

    async def cleanup_once(self, *, now: datetime | None = None) -> RetentionReport:
        """执行一次**完整**每日维护：jobs 清理 + 审计归档/清理 + 旧归档清理。

        自动清理（调度器触发）；审计部分 fail-safe（归档失败不 purge）。

        Returns:
            jobs 清理的 :class:`RetentionReport`（审计统计见各自审计条目）。
        """
        moment = now or now_utc()
        report = await self._cleanup_jobs(moment)
        await self._archive_and_purge_audit(moment)
        await self._cleanup_old_archives(moment)
        # 归档生命周期（删除保护方案 §5）：与审计保留同一挂载点，不另开调度域
        await self.cleanup_archives(now=moment)
        return report

    async def cleanup_jobs_only(self, *, now: datetime | None = None) -> RetentionReport:
        """任务历史页「立即清理」（裁决5）：**仅**清理 jobs 行与孤儿任务日志。

        不触碰审计日志（审计的清理/归档只由每日维护 ``cleanup_once`` 负责，避免用户在
        任务历史页误触发审计删除）。``retention.jobs_days <= 0`` 时返回零值报告
        （fail-safe：坏配置宁可不清理也不误删）。

        Returns:
            :class:`RetentionReport`。
        """
        moment = now or now_utc()
        return await self._cleanup_jobs(moment)

    # ------------------------------------------------------------------ jobs 清理

    async def _cleanup_jobs(self, moment: datetime) -> RetentionReport:
        """清理 jobs 行 + 孤儿/过期任务日志，并记 ``retention.cleanup`` 审计。"""
        jobs_days = await self._resolve_days(RETENTION_JOBS_DAYS_KEY, RETENTION_JOBS_DAYS_DEFAULT)
        if jobs_days <= 0:
            logger.info("retention.jobs_days=%s（不启用），跳过 jobs 清理", jobs_days)
            return RetentionReport(0, 0, 0, jobs_days, 0)
        logs_days = await self._resolve_days(
            RETENTION_LOGS_DAYS_KEY, jobs_days, fallback_key=RETENTION_JOBS_DAYS_KEY
        )
        cutoff_jobs = utc_iso(moment - timedelta(days=jobs_days))
        removed_log_paths = await self._jobs.purge_older_than(cutoff_jobs)
        # 行已删，其日志成孤儿：无论 mtime 是否过 logs 阈值一并移除（行没了，日志没有保留价值）
        orphan_freed = await asyncio.to_thread(_unlink_paths_sync, removed_log_paths)

        # 活跃任务的日志受保护（病态老化亦然——running/pending 行绝不受 retention 影响）
        protected = frozenset(str(Path(job.log_path)) for job in await self._jobs.list_active())
        if logs_days <= 0:
            # 日志保留单独关闭（0）时跳过 mtime 清理，但 jobs 行清理照常
            deleted_paths, freed = 0, 0
            logs_days = 0
        else:
            cutoff_logs = moment - timedelta(days=logs_days)
            deleted_paths, freed = await asyncio.to_thread(
                _purge_old_logs_sync, self._settings.jobs_dir, cutoff_logs.timestamp(), protected
            )

        report = RetentionReport(
            jobs_deleted=len(removed_log_paths),
            log_files_deleted=deleted_paths + len(removed_log_paths),
            log_bytes_freed=freed + orphan_freed,
            jobs_days=jobs_days,
            logs_days=logs_days,
        )
        await self._audit.record(
            AUDIT_RETENTION_CLEANUP,
            target="retention",
            detail={
                "jobs_deleted": report.jobs_deleted,
                "log_files_deleted": report.log_files_deleted,
                "log_bytes_freed": report.log_bytes_freed,
                "jobs_days": report.jobs_days,
                "logs_days": report.logs_days,
            },
            result="ok",
        )
        logger.info(
            "每日 retention jobs 清理完成：jobs=%s log_files=%s freed_bytes=%s",
            report.jobs_deleted,
            report.log_files_deleted,
            report.log_bytes_freed,
        )
        return report

    # ------------------------------------------------------------------ 审计归档/清理

    async def _resolve_archive_dir(self) -> Path:
        """解析归档目录：DB 键 ``retention.audit_archive_dir`` 优先，缺省回落 settings。"""
        raw = await self._config.get_value(RETENTION_AUDIT_ARCHIVE_DIR_KEY)
        if raw and str(raw).strip():
            return Path(str(raw).strip())
        return self._settings.audit_archive_dir

    async def _archive_and_purge_audit(self, moment: datetime) -> AuditArchiveReport:
        """归档超期审计行并清理（fail-safe：归档失败跳过 purge）。

        1. 计算 ``audit_days`` 阈值；``<=0`` 不启用（永不删审计）；
        2. 把 ``ts < cutoff`` 的行流式追加写入 ``audit-<YYYY>.jsonl.gz``（压缩）；
        3. 归档**成功**后才经 :meth:`AuditRepo.purge_older_than` 删除对应行；
        4. 归档/清理分别记 ``audit.archive`` / ``audit.cleanup``。

        Returns:
            :class:`AuditArchiveReport`（含归档行数/字节、删除行数、文件名）。
        """
        audit_days = await self._resolve_days(RETENTION_AUDIT_DAYS_KEY, RETENTION_AUDIT_DAYS_DEFAULT)
        if audit_days <= 0:
            logger.info("retention.audit_days=%s（不启用），跳过审计归档/清理", audit_days)
            return AuditArchiveReport(0, 0, 0, audit_days, None)

        cutoff = utc_iso(moment - timedelta(days=audit_days))
        archive_dir = await self._resolve_archive_dir()
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"audit-{moment.strftime('%Y')}.jsonl.gz"

        # 流式归档：攒够 ARCHIVE_BUFFER_ROWS 行落盘一次，避免一次性进内存
        buffer: list[AuditRow] = []
        archived_rows = 0
        try:
            async for row in self._audit.iter_rows(until=cutoff, batch=ARCHIVE_BUFFER_ROWS):
                buffer.append(row)
                if len(buffer) >= ARCHIVE_BUFFER_ROWS:
                    archived_rows += await asyncio.to_thread(_append_jsonl_gz, archive_path, buffer)
                    buffer.clear()
            if buffer:
                archived_rows += await asyncio.to_thread(_append_jsonl_gz, archive_path, buffer)
                buffer.clear()
        except Exception as exc:  # noqa: BLE001 — 归档任何异常都 fail-safe：不 purge
            logger.exception("审计归档失败（%s），跳过 purge 以防数据丢失", archive_path)
            await self._audit.record(
                AUDIT_ARCHIVE,
                target=str(archive_path),
                detail={"error": str(exc), "archived_rows_before_failure": archived_rows},
                result="failed",
            )
            return AuditArchiveReport(0, 0, 0, audit_days, None)

        archived_bytes = await asyncio.to_thread(lambda p: p.stat().st_size if p.exists() else 0, archive_path)

        # 归档成功才 purge（唯一受控 DELETE 出口）
        purged_rows = await self._audit.purge_older_than(cutoff)

        await self._audit.record(
            AUDIT_ARCHIVE,
            target=str(archive_path),
            detail={
                "file": archive_path.name,
                "rows": archived_rows,
                "bytes": archived_bytes,
                "year": moment.strftime("%Y"),
            },
            result="ok",
        )
        if purged_rows:
            await self._audit.record(
                AUDIT_CLEANUP,
                target="audit_logs",
                detail={"rows_deleted": purged_rows, "cutoff": cutoff},
                result="ok",
            )
        logger.info(
            "审计归档/清理完成：归档=%s 行（%s 字节，文件 %s）清理=%s 行",
            archived_rows,
            archived_bytes,
            archive_path.name,
            purged_rows,
        )
        return AuditArchiveReport(archived_rows, archived_bytes, purged_rows, audit_days, archive_path.name)

    async def _cleanup_old_archives(self, moment: datetime) -> None:
        """清理过期的压缩归档文件（按 ``retention.audit_archive_days``，缺省 365）。"""
        archive_days = await self._resolve_days(
            RETENTION_AUDIT_ARCHIVE_DAYS_KEY, RETENTION_AUDIT_ARCHIVE_DAYS_DEFAULT
        )
        if archive_days <= 0:
            return
        cutoff_epoch = (moment - timedelta(days=archive_days)).timestamp()
        archive_dir = await self._resolve_archive_dir()
        if not archive_dir.is_dir():
            return
        deleted, freed = await asyncio.to_thread(
            _purge_old_archives_sync, archive_dir, cutoff_epoch
        )
        if deleted:
            logger.info("过期审计归档清理：%s 文件，释放 %s 字节", deleted, freed)

    async def list_archives(self) -> list[dict]:
        """只读列出归档目录下的 ``.jsonl.gz`` 文件（供前端展示）。

        Returns:
            按文件名升序的列表，每项为 ``{"name", "size_bytes", "mtime_iso"}``。
        """
        archive_dir = await self._resolve_archive_dir()
        return await asyncio.to_thread(_list_archives_sync, archive_dir)

    # ------------------------------------------------------------------ 归档生命周期（删除保护方案 §5）

    async def cleanup_archives(self, *, now: datetime | None = None) -> ArchiveCleanupReport:
        """按四个保留期键推进归档生命周期（每日维护的一部分）。

        四档（与《删除依赖与快照可用性保护方案》§5 一一对应，第四档为其补充）：

        1. ``retention.repo_archive_days``（缺省 30）到期 → 清**凭据 + 密码文件**、
           仓库行转墓碑。**不碰磁盘数据**（数据自始至终不归应用删）。
        2. ``retention.repo_tombstone_days``（缺省 365）到期 → 删墓碑记录。
        3. ``retention.rclone_archive_days``（缺省 365）到期 → 删 rclone 配置归档。
        4. ``retention.repo_passfile_days``（缺省 30）→ 回收**无主且超龄**的密码文件：
           ``?hard=1`` 删除、归档页「释放 ID」、上一轮 unlink 失败都会留下
           ``<passfile_root>/<repo_id>/password`` 孤儿；判定「无主」= repo_id 不在
           在用仓库 / 归档仓库 / 墓碑三者之中，「超龄」= mtime 早于冷却期
           （避开刚建仓、行还没落库的中间态）。
           ``RESTIC_PASSWORD_FILE`` 全局共用密码文件时整段跳过（删它会毁掉全部仓库）。

        四者 ``<=0`` 一律不启用（永久保留），与 ``retention.audit_days`` 同语义。

        Returns:
            :class:`ArchiveCleanupReport`（零删除也返回，便于调用方记录一条审计）。
        """
        moment = now or now_utc()
        report = ArchiveCleanupReport()

        if self._repos is not None:
            archive_days = await self._resolve_days(
                RETENTION_REPO_ARCHIVE_DAYS_KEY, RETENTION_REPO_ARCHIVE_DAYS_DEFAULT
            )
            if archive_days > 0:
                cutoff = utc_iso(moment - timedelta(days=archive_days))
                for repo in await self._repos.list_archived():
                    if not repo.deleted_at or repo.deleted_at >= cutoff:
                        continue
                    # 先清恢复能力（凭据 + 口令文件），再把行转墓碑——顺序不能反：
                    # 中途失败时行还在，下一轮还会重试（幂等）。
                    for row in await self._config.list_all_raw():
                        if (
                            row.key.startswith(f"cred.{repo.id}.")
                            and row.source != "env"
                            and row.value
                        ):
                            await self._config.delete(row.key)
                    await asyncio.to_thread(remove_passfile, self._settings, repo.id)
                    await self._repos.hard_delete(repo.id, data_destroyed=False)
                    report.repos_expired.append(repo.id)

            tomb_days = await self._resolve_days(
                RETENTION_REPO_TOMBSTONE_DAYS_KEY, RETENTION_REPO_TOMBSTONE_DAYS_DEFAULT
            )
            if tomb_days > 0:
                cutoff = utc_iso(moment - timedelta(days=tomb_days))
                for row in await self._repos.list_tombstones():
                    if str(row["archived_at"]) < cutoff:
                        await self._repos.delete_tombstone(str(row["repo_id"]))
                        report.tombstones_deleted.append(str(row["repo_id"]))

            # 第四档：孤儿密码文件回收（无主 + 超龄）。放在墓碑清理之后——墓碑期内
            # 该 id 仍算「有主」，墓碑到期后才进入可回收集合，顺序即天然的宽限。
            passfile_days = await self._resolve_days(
                RETENTION_REPO_PASSFILE_DAYS_KEY, RETENTION_REPO_PASSFILE_DAYS_DEFAULT
            )
            if passfile_days > 0:
                cutoff_ts = (moment - timedelta(days=passfile_days)).timestamp()
                known: set[str] = set()
                known.update(repo.id for repo in await self._repos.list_all())
                known.update(repo.id for repo in await self._repos.list_archived())
                known.update(str(row["repo_id"]) for row in await self._repos.list_tombstones())
                report.passfiles_reaped = await asyncio.to_thread(
                    reap_orphan_passfiles, self._settings, known, cutoff_ts=cutoff_ts
                )

        if self._rclone_archives is not None:
            rclone_days = await self._resolve_days(
                RETENTION_RCLONE_ARCHIVE_DAYS_KEY, RETENTION_RCLONE_ARCHIVE_DAYS_DEFAULT
            )
            if rclone_days > 0:
                cutoff = utc_iso(moment - timedelta(days=rclone_days))
                report.rclone_archives_expired = await self._rclone_archives.purge_older_than(cutoff)

        if (
            report.repos_expired
            or report.tombstones_deleted
            or report.rclone_archives_expired
            or report.passfiles_reaped
        ):
            await self._audit.record(
                AUDIT_RETENTION_CLEANUP,
                target="archive",
                detail={
                    "repos_expired": len(report.repos_expired),
                    "tombstones_deleted": len(report.tombstones_deleted),
                    "rclone_archives_expired": len(report.rclone_archives_expired),
                    "passfiles_reaped": len(report.passfiles_reaped),
                },
                result="ok",
            )
            logger.info(
                "归档生命周期清理：归档到期 %s / 墓碑删除 %s / rclone 归档到期 %s / 孤儿密码文件 %s",
                len(report.repos_expired),
                len(report.tombstones_deleted),
                len(report.rclone_archives_expired),
                len(report.passfiles_reaped),
            )
        return report

    # ------------------------------------------------------------------ 配置读取

    async def _resolve_days(self, key: str, default: int, *, fallback_key: str | None = None) -> int:
        """读取保留天数配置：非法值 WARNING 回退（缺省或 fallback 键），``<=0`` 原样返回（=不启用）。"""
        raw = await self._config.get_value(key)
        if raw is None or str(raw).strip() == "":
            if fallback_key is not None:
                return await self._resolve_days(fallback_key, default)
            return default
        try:
            value = int(str(raw).strip())
        except ValueError:
            logger.warning("%s 配置非法：%r，回退缺省 %s 天", key, raw, default)
            return default
        return value


# ----------------------------------------------------------------------- 同步文件辅助（在线程内执行）

def _append_jsonl_gz(path: Path, rows: list[AuditRow]) -> int:
    """把审计行追加写入 gzip JSONL 文件；返回写入行数。

    以追加模式打开（同一 ``audit-<YYYY>.jsonl.gz`` 跨日维护多次 append，兼容 gzip
    多 member 顺序读取）；字段顺序固定便于后续解析。
    """
    written = 0
    with gzip.open(path, "ab") as fh:
        for row in rows:
            obj = {
                "id": row.id,
                "ts": row.ts,
                "action": row.action,
                "target": row.target,
                "detail": row.detail,
                "result": row.result,
                "created_at": row.created_at,
            }
            fh.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            written += 1
    return written


def _purge_old_logs_sync(jobs_dir: Path, cutoff_epoch: float, protected: frozenset[str]) -> tuple[int, int]:
    """同步删除 ``jobs_dir`` 下一级 ``*.log`` 中 mtime 早于 cutoff 的文件（不递归，S1）。

    Args:
        jobs_dir: 日志目录（settings.jobs_dir 白名单来源，路径字面量唯一出处 settings.py）。
        cutoff_epoch: mtime 早于此 epoch 秒的文件删除。
        protected: 受保护文件路径集合（活跃任务日志，绝不触碰）。

    Returns:
        ``(deleted_count, freed_bytes)``。
    """
    deleted = 0
    freed = 0
    if not jobs_dir.is_dir():
        return deleted, freed
    for entry in jobs_dir.glob("*.log"):
        if str(entry) in protected:
            continue
        try:
            if entry.is_symlink():
                # 符号链接只删链接本身、绝不跟随（S1：白名单外的目标不可被间接触达；
                # 链接无保留价值，link 字节计 0）
                entry.unlink()
                deleted += 1
                continue
            stat = entry.stat()
            if not entry.is_file() or stat.st_mtime >= cutoff_epoch:
                continue
            entry.unlink()
        except OSError:
            continue  # 单文件失败不中断整轮清理（逐项隔离）
        deleted += 1
        freed += stat.st_size
    return deleted, freed


def _purge_old_archives_sync(archive_dir: Path, cutoff_epoch: float) -> tuple[int, int]:
    """同步删除归档目录下 mtime 早于 cutoff 的 ``.jsonl.gz`` 文件（只删 glob 命中项，不递归）。

    Args:
        archive_dir: 归档目录（S1：路径字面量唯一出处 settings.audit_archive_dir / DB 键）。
        cutoff_epoch: mtime 早于此 epoch 秒的归档文件删除。

    Returns:
        ``(deleted_count, freed_bytes)``。
    """
    deleted = 0
    freed = 0
    if not archive_dir.is_dir():
        return deleted, freed
    for entry in archive_dir.glob(AUDIT_ARCHIVE_FILE_GLOB):
        try:
            stat = entry.stat()
            if not entry.is_file() or stat.st_mtime >= cutoff_epoch:
                continue
            entry.unlink()
        except OSError:
            continue  # 单文件失败不中断（逐项隔离）
        deleted += 1
        freed += stat.st_size
    return deleted, freed


def _list_archives_sync(archive_dir: Path) -> list[dict]:
    """同步列出归档目录下的 ``.jsonl.gz`` 文件（按文件名升序）。"""
    items: list[dict] = []
    if not archive_dir.is_dir():
        return items
    for entry in sorted(archive_dir.glob(AUDIT_ARCHIVE_FILE_GLOB), key=lambda p: p.name):
        try:
            stat = entry.stat()
            if not entry.is_file():
                continue
            items.append(
                {
                    "name": entry.name,
                    "size_bytes": stat.st_size,
                    "mtime_iso": utc_iso(datetime.fromtimestamp(stat.st_mtime)),
                }
            )
        except OSError:
            continue
    return items


def _unlink_paths_sync(paths: list[str]) -> int:
    """同步删除给定文件路径（孤儿日志），返回释放字节数；缺失/失败静默跳过。"""
    freed = 0
    for raw in paths:
        path = Path(raw)
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        freed += size
    return freed
