"""RetentionService 单测（R-020 / 设计 §3.4）：90 天边界、幂等、fallback、fail-safe、S1 白名单。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.database import Database
from app.db.repositories.audit_repo import AuditRepo
from app.db.repositories.config_repo import ConfigRepo
from app.db.repositories.job_repo import JobRepo
from app.services.retention import (
    RETENTION_JOBS_DAYS_KEY,
    RETENTION_LOGS_DAYS_KEY,
    RetentionService,
)
from app.settings import Settings
from app.utils.time import utc_iso

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


async def make_service(
    tmp_path: Path,
) -> tuple[Database, RetentionService, JobRepo, ConfigRepo, AuditRepo, Settings]:
    """构造真实仓储支撑的保留服务（DB/日志目录落 tmp_path 隔离，settings 一并返回复用）。"""
    db = Database(tmp_path / "retention.db")
    await db.connect()
    settings = Settings(data_dir=str(tmp_path / "data"))
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    job_repo = JobRepo(db)
    config_repo = ConfigRepo(db)
    audit_repo = AuditRepo(db)
    service = RetentionService(job_repo, config_repo, audit_repo, settings)
    return db, service, job_repo, config_repo, audit_repo, settings


async def seed_job(
    db: Database,
    job_repo: JobRepo,
    settings: Settings,
    job_id: str,
    *,
    status: str = "success",
    age_days: int,
) -> Path:
    """落一条任务并把 created_at 回拨 age_days；日志文件同步回拨 mtime。返回日志路径。"""
    await job_repo.create(
        job_id=job_id,
        job_type="backup",
        trigger="scheduled",
        log_path=str(settings.jobs_dir / f"{job_id}.log"),
        status=status,
    )
    created = utc_iso(NOW - timedelta(days=age_days))
    await db.write("UPDATE jobs SET created_at = ? WHERE id = ?", (created, job_id))
    log_path = settings.jobs_dir / f"{job_id}.log"
    log_path.write_bytes(b"log" * 10)
    old_epoch = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(log_path, (old_epoch, old_epoch))
    return log_path


async def test_purges_old_terminal_keeps_recent_and_active(tmp_path):
    """90 天边界：过期终态行删、近期行留、**过期活跃行及其日志绝不触碰**。"""
    db, service, job_repo, _, _, settings = await make_service(tmp_path)
    old_log = await seed_job(db, job_repo, settings, "job-old", age_days=100)
    await seed_job(db, job_repo, settings, "job-recent", age_days=1)
    await seed_job(db, job_repo, settings, "job-active", status="running", age_days=100)

    report = await service.cleanup_once(now=NOW)

    assert report.jobs_deleted == 1
    assert report.jobs_days == 90 and report.logs_days == 90
    assert not old_log.exists()  # 被删行的孤儿日志一并移除
    assert (settings.jobs_dir / "job-recent.log").exists()
    assert (settings.jobs_dir / "job-active.log").exists()  # 活跃行日志受保护
    assert (await job_repo.get("job-old")) is None
    assert (await job_repo.get("job-recent")) is not None
    assert (await job_repo.get("job-active")) is not None  # 活跃行不动
    await db.close()


async def test_cleanup_is_idempotent_and_audited(tmp_path):
    """幂等（验收要点）：重跑零删除；每次执行都落审计 retention.cleanup（detail 含计数）。"""
    db, service, job_repo, _, audit_repo, settings = await make_service(tmp_path)
    await seed_job(db, job_repo, settings, "job-old", age_days=100)

    first = await service.cleanup_once(now=NOW)
    assert first.jobs_deleted == 1 and first.log_files_deleted >= 1
    second = await service.cleanup_once(now=NOW)
    assert second.jobs_deleted == 0 and second.log_files_deleted == 0

    rows, total = await audit_repo.query(action="retention.cleanup")
    assert total == 2
    detail = rows[0].detail or ""
    assert '"jobs_deleted": 0' in detail and '"log_bytes_freed"' in detail
    await db.close()


async def test_jobs_days_zero_disables_cleanup(tmp_path):
    """``retention.jobs_days=0``（不启用）→ 零删除、不记审计（fail-safe）。"""
    db, service, job_repo, config_repo, audit_repo, settings = await make_service(tmp_path)
    await config_repo.set(RETENTION_JOBS_DAYS_KEY, "0")
    await seed_job(db, job_repo, settings, "job-old", age_days=365)

    report = await service.cleanup_once(now=NOW)

    assert report.jobs_deleted == 0
    assert (await job_repo.get("job-old")) is not None
    _, total = await audit_repo.query(action="retention.cleanup")
    assert total == 0
    await db.close()


async def test_invalid_jobs_days_falls_back_to_default(tmp_path):
    """坏值回退缺省 90（WARNING）：91 天删、89 天留。"""
    db, service, job_repo, config_repo, _, settings = await make_service(tmp_path)
    await config_repo.set(RETENTION_JOBS_DAYS_KEY, "abc")
    await seed_job(db, job_repo, settings, "job-91", age_days=91)
    await seed_job(db, job_repo, settings, "job-89", age_days=89)

    report = await service.cleanup_once(now=NOW)

    assert report.jobs_deleted == 1
    assert (await job_repo.get("job-91")) is None
    assert (await job_repo.get("job-89")) is not None
    await db.close()


async def test_logs_days_fallback_and_independence(tmp_path):
    """Q12：logs_days 缺省回退 jobs_days；显式设置后独立生效（孤儿日志按 mtime 清理）。"""
    db, service, job_repo, config_repo, _, settings = await make_service(tmp_path)
    aged_95 = settings.jobs_dir / "aged-95.log"
    aged_95.write_bytes(b"a" * 5)
    aged_50 = settings.jobs_dir / "aged-50.log"
    aged_50.write_bytes(b"b" * 5)
    for path, days in ((aged_95, 95), (aged_50, 50)):
        epoch = (NOW - timedelta(days=days)).timestamp()
        os.utime(path, (epoch, epoch))

    # 缺省（logs_days 回退 jobs_days=90）：95 天删、50 天留
    report = await service.cleanup_once(now=NOW)
    assert report.logs_days == 90
    assert not aged_95.exists() and aged_50.exists()

    # 显式 logs_days=10：50 天的日志也被清
    await config_repo.set(RETENTION_LOGS_DAYS_KEY, "10")
    aged_50.write_bytes(b"b" * 5)
    epoch = (NOW - timedelta(days=50)).timestamp()
    os.utime(aged_50, (epoch, epoch))
    report = await service.cleanup_once(now=NOW)
    assert report.logs_days == 10
    assert not aged_50.exists()
    await db.close()


async def test_log_purge_never_leaves_jobs_dir(tmp_path):
    """S1 白名单：清理只触碰 settings.jobs_dir，目录外同名后缀文件不受影响。"""
    db, service, job_repo, _, _, settings = await make_service(tmp_path)
    outside = settings.jobs_dir.parent / "outside.log"
    outside.write_bytes(b"keep")
    await seed_job(db, job_repo, settings, "job-old", age_days=100)

    await service.cleanup_once(now=NOW)

    assert outside.exists()  # jobs_dir 之外绝不触碰
    await db.close()
