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


async def test_log_purge_unlinks_symlink_never_target(tmp_path, monkeypatch):
    """QA 补测（批次 B）：jobs_dir 内 ``.log`` 符号链接只删链接本身、绝不跟随触达目标。

    攻击形态：jobs_dir 下 ``evil.log`` 指向白名单外的目标文件——若清理按
    ``stat().st_mtime``（跟随链接）判定后 unlink，目标是否被删取决于平台语义；
    显式 ``is_symlink`` 分支保证：链接被摘除（目录卫生、计 0 字节）、目标保留。

    Windows（本仓库 CI/开发环境）无法稳定创建真实符号链接（无特权时行为各异），
    故以 monkeypatch 将两个具名文件标记为「链接形态」做确定性分支测试；真实
    链接路径在 POSIX CI 上由同分支原生覆盖。
    """
    db, service, _, _, _, settings = await make_service(tmp_path)
    target = settings.jobs_dir.parent / "victim.txt"
    target.write_bytes(b"precious")
    link = settings.jobs_dir / "evil.log"
    link.write_bytes(b"")  # 链接本体占位（真实环境下为符号链接）
    dangling = settings.jobs_dir / "dangling.log"
    dangling.write_bytes(b"")  # 悬空链接占位

    real_is_symlink = Path.is_symlink

    def fake_is_symlink(self: Path) -> bool:
        if self.name in {"evil.log", "dangling.log"}:
            return True
        return real_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    report = await service.cleanup_once(now=NOW)

    assert not link.exists()  # 链接本体被摘除
    assert target.read_bytes() == b"precious"  # 目标绝不跟随触达
    assert not dangling.exists()  # 悬空链接同样摘除、不抛异常
    assert report.log_files_deleted >= 2  # 计数进报告（0 字节，不影响 freed 统计）

    # S-1（2026-10-15 审查）：缺此 close 曾致 aiosqlite worker 线程泄漏——用例正常
    # 返回时 db 引用计数归零、被 GC 抢在 conftest 兜底 close 之前，兜底（WeakSet）
    # 永远关不到它；非 daemon worker 线程带未处理项存活，在后续用例 loop 关闭后
    # 爆 RuntimeError: Event loop is closed（挂到 test_scheduler 首用例的 warning）。
    # 全仓唯一缺 close 的用例（本文件其余 6 例及各测试文件均已显式关闭）。
    await db.close()
