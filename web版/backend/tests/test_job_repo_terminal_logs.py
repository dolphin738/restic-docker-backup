"""候选 1 批次 0：``JobRepo.terminal_log_paths()`` 的契约测试。

它是仓库删除改序为「行最后删」的前提——必须在删行之前拿到日志路径，
否则行一删就再也查不到（delete_repo 首行 404 会吞掉重试入口）。

注：``jobs.repo_id`` 外键指向 ``repos(id)``（**无级联**），故这里必须先建真实仓库行；
顺带印证候选 1 决策里那条安全网——活跃任务存在时 ``DELETE FROM repos`` 会被外键挡下。
"""

from __future__ import annotations

import pytest

from app.db.database import Database
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.repo_repo import ReposRepo


@pytest.fixture
async def db(tmp_path):
    """走真实迁移器的临时库（保留外键约束，与生产一致）。"""
    database = Database(tmp_path / "jobs.db")
    await database.connect()
    yield database
    await database.close()


async def _mk(db: Database, job_id: str, status: str, repo_id: str, log_path: str) -> None:
    """建一条 job 行（经仓储自身接口，避免手拼 SQL 与 schema 漂移）。"""
    repo = JobRepo(db)
    await repo.create(
        job_id=job_id, job_type="backup", trigger="manual",
        log_path=log_path, repo_id=repo_id, status=status,
    )


async def test_terminal_log_paths_only_terminal(db):
    """只返回终态任务；活跃任务的行排除在外。"""
    repos = ReposRepo(db)
    await repos.create("r1", "仓库一")
    await _mk(db, "j1", "success", "r1", "/logs/j1.log")
    await _mk(db, "j2", "failed", "r1", "/logs/j2.log")
    await _mk(db, "j3", "running", "r1", "/logs/j3.log")
    await _mk(db, "j4", "pending", "r1", "/logs/j4.log")
    assert sorted(await JobRepo(db).terminal_log_paths("r1")) == ["/logs/j1.log", "/logs/j2.log"]


async def test_terminal_log_paths_scoped_by_repo(db):
    """按 repo_id 隔离。"""
    repos = ReposRepo(db)
    await repos.create("r1", "仓库一")
    await repos.create("r2", "仓库二")
    await _mk(db, "j1", "success", "r1", "/logs/a.log")
    await _mk(db, "j2", "success", "r2", "/logs/b.log")
    assert await JobRepo(db).terminal_log_paths("r1") == ["/logs/a.log"]
    assert await JobRepo(db).terminal_log_paths("r2") == ["/logs/b.log"]


async def test_terminal_log_paths_empty_and_does_not_delete(db):
    """无匹配返回空列表；且**不删任何行**（与 purge_older_than 的关键差别）。"""
    repos = ReposRepo(db)
    await repos.create("r1", "仓库一")
    await _mk(db, "j1", "success", "r1", "/logs/j1.log")
    assert await JobRepo(db).terminal_log_paths("nope") == []
    await JobRepo(db).terminal_log_paths("r1")
    assert await JobRepo(db).terminal_log_paths("r1") == ["/logs/j1.log"]


async def test_active_job_blocks_repo_row_deletion(db):
    """候选 1 的安全网：存在活跃任务时删仓库行会被外键挡下（故不要给它加级联）。"""
    import sqlite3

    repos = ReposRepo(db)
    await repos.create("r1", "仓库一")
    await _mk(db, "j3", "running", "r1", "/logs/j3.log")
    with pytest.raises(sqlite3.IntegrityError):
        await db.write("DELETE FROM repos WHERE id = ?", ("r1",))
    # 行仍在 → 崩溃后可重试
    assert await repos.get("r1") is not None
