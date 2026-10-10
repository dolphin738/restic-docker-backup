"""迁移器回归（§10.1）：user_version 推进、幂等重跑、六表+索引、PRAGMA 工程约定。"""

from __future__ import annotations

import sqlite3

import pytest

from app.db.database import Database

EXPECTED_TABLES = {"admin_account", "sessions", "config", "policies", "jobs", "audit_logs"}

# 0001_init.sql 内的全部 CREATE INDEX（§4.2/4.4/4.5/4.6；共 6 个——任务书口径"5 索引"少计了 1 个）
EXPECTED_INDEXES = {
    "idx_sessions_expires_at",
    "idx_policies_default",
    "idx_jobs_status_created",
    "idx_jobs_policy",
    "idx_audit_ts",
    "idx_audit_action",
}

_JOB_INSERT = (
    "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, policy_id) "
    "VALUES (?, 'backup', 'pending', 'manual', '/tmp/x.log', '2026-01-01T00:00:00Z', 999)"
)


async def test_migrate_advances_user_version(tmp_path):
    db = Database(tmp_path / "restic.db")
    await db.connect()
    assert await db.user_version() == 1
    await db.close()


async def test_migrate_is_idempotent_when_run_twice(tmp_path):
    """connect() 已迁移一次后，再显式连跑 migrate() 必须零应用、零副作用。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    assert await db.migrate() == 0
    assert await db.migrate() == 0
    assert await db.user_version() == 1
    await db.close()


async def test_reconnect_does_not_reapply(tmp_path):
    """重新建立连接（模拟进程重启）后迁移不重复执行。"""
    path = tmp_path / "restic.db"
    db1 = Database(path)
    await db1.connect()
    await db1.close()

    db2 = Database(path)
    await db2.connect()
    assert await db2.user_version() == 1
    assert await db2.migrate() == 0
    await db2.close()


async def test_six_tables_and_indexes_exist(tmp_path):
    db = Database(tmp_path / "restic.db")
    await db.connect()
    tables = {
        str(row["name"])
        for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert tables >= EXPECTED_TABLES

    indexes = {
        str(row["name"])
        for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert indexes >= EXPECTED_INDEXES
    await db.close()


async def test_pragmas_wal_and_foreign_keys(tmp_path):
    db = Database(tmp_path / "restic.db")
    await db.connect()

    journal = await db.fetch_one("PRAGMA journal_mode")
    assert str(journal[0]).lower() == "wal"

    fk = await db.fetch_one("PRAGMA foreign_keys")
    assert int(fk[0]) == 1
    await db.close()


async def test_foreign_key_enforced(tmp_path):
    """jobs.policy_id → policies(id) 外键真实生效（拒绝悬挂引用）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    with pytest.raises(sqlite3.IntegrityError):
        await db.write(_JOB_INSERT, ("job-fk",))
    await db.close()


async def test_admin_account_single_row_check(tmp_path):
    """admin_account CHECK(id=1)：恒单行约束生效。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    await db.write(
        "INSERT INTO admin_account (id, password_hash, updated_at) VALUES (1, 'hash', '2026-01-01T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await db.write(
            "INSERT INTO admin_account (id, password_hash, updated_at) VALUES (2, 'hash', '2026-01-01T00:00:00Z')"
        )
    await db.close()


async def test_audit_logs_append_only_columns(tmp_path):
    """audit_logs 具备 append-only 形态：自增主键 + result 默认 ok。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    await db.write(
        "INSERT INTO audit_logs (ts, action, created_at) "
        "VALUES ('2026-01-01T00:00:00Z', 'login.ok', '2026-01-01T00:00:00Z')"
    )
    row = await db.fetch_one("SELECT id, result FROM audit_logs WHERE action = 'login.ok'")
    assert int(row["id"]) == 1
    assert str(row["result"]) == "ok"
    await db.close()
