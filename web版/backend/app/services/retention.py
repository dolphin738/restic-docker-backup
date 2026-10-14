"""任务历史与日志保留（R-020 / 设计 §3.4）：每日维护清理 jobs 行与 /data/jobs 日志。

语义（Q12 基线）：
- jobs 行：``retention.jobs_days``（缺省 90，E07 可调）之前创建的**终态**行删除
  （running/pending 绝不触碰，见 :meth:`JobRepo.purge_older_than`）；
- 日志文件：``retention.logs_days``（可选键）之前修改的 ``<jobs_dir>/*.log`` 删除，
  **键缺省时回退 ``retention.jobs_days``**（实现为读取时 fallback，无新列——设计 §1.2）；
- ``days <= 0`` 视为「不启用」（与 ``prune_interval_days`` 同口径），整次清理跳过；
- 删除动作记审计 ``retention.cleanup``（detail 含行数/文件数/字节数，S6 只增不改）；
- 幂等：清理是按阈值的绝对判定，重跑零删除（验收要点）。

红线落点：A1（SQL 只在 repo）、A5（写走 Database.write 串行）、C8（时间只走
utils/time）、S1（文件只触碰 ``settings.jobs_dir`` 白名单内的 ``*.log``，不递归子目录）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.constants import AUDIT_RETENTION_CLEANUP, RETENTION_JOBS_DAYS_DEFAULT
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.settings import Settings, get_settings
from app.utils.time import now_utc, utc_iso

logger = logging.getLogger(__name__)

# config 键（单一真源；E07 读写与运行期读取共用）
RETENTION_JOBS_DAYS_KEY = "retention.jobs_days"
RETENTION_LOGS_DAYS_KEY = "retention.logs_days"


@dataclass(frozen=True)
class RetentionReport:
    """一次清理的结果（审计 detail 与调度日志共用）。"""

    jobs_deleted: int
    log_files_deleted: int
    log_bytes_freed: int
    jobs_days: int
    logs_days: int


class RetentionService:
    """任务历史/日志保留服务（挂调度器每日维护任务 ``maintenance-retention``）。"""

    def __init__(
        self,
        jobs: JobRepo,
        config: ConfigRepo,
        audit: AuditRepo,
        settings: Settings | None = None,
    ) -> None:
        """构造保留服务。

        Args:
            jobs: jobs 表仓储（行清理经 :meth:`JobRepo.purge_older_than`）。
            config: 配置仓储（``retention.jobs_days`` / ``retention.logs_days``）。
            audit: 审计仓储（``retention.cleanup`` 埋点，S6）。
            settings: 运行期配置（jobs_dir 白名单来源）；缺省取进程单例。
        """
        self._jobs = jobs
        self._config = config
        self._audit = audit
        self._settings = settings or get_settings()

    async def cleanup_once(self, *, now: datetime | None = None) -> RetentionReport:
        """执行一次清理并记审计；可注入 ``now`` 供测试确定化（C8 时间语义）。

        Returns:
            :class:`RetentionReport`；``retention.jobs_days`` 非法/<=0 时返回零值报告
            （不删任何数据、不记审计——fail-safe：坏配置宁可不清理也不误删）。
        """
        moment = now or now_utc()
        jobs_days = await self._resolve_days(RETENTION_JOBS_DAYS_KEY, RETENTION_JOBS_DAYS_DEFAULT)
        if jobs_days <= 0:
            logger.info("retention.jobs_days=%s（不启用），跳过每日清理", jobs_days)
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
            "每日 retention 清理完成：jobs=%s log_files=%s freed_bytes=%s",
            report.jobs_deleted,
            report.log_files_deleted,
            report.log_bytes_freed,
        )
        return report

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
