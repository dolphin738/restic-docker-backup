"""迁移器回归（§10.1）：user_version 推进、幂等重跑、六表+索引、PRAGMA 工程约定。

0002_multi_backup_tasks.sql（选项② R-026）：repos / backup_tasks / jobs.task_id 三结构断言；
0004_retention_within.sql（批次 A2）：backup_tasks keep_within*/keep_tags 七列断言。
greenfield（owner 2026-10-13 裁决）——**无物化断言**（不产生首任务）。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db.database import Database

EXPECTED_TABLES = {
    "admin_account", "sessions", "config", "policies", "jobs", "audit_logs",
    "repos", "backup_tasks",
}

# 0001_init.sql 内的全部 CREATE INDEX（§4.2/4.4/4.5/4.6；共 6 个——任务书口径"5 索引"少计了 1 个）
# + 0002_multi_backup_tasks.sql 的 2 个（idx_backup_tasks_repo / idx_jobs_task）
EXPECTED_INDEXES = {
    "idx_sessions_expires_at",
    "idx_policies_default",
    "idx_jobs_status_created",
    "idx_jobs_policy",
    "idx_audit_ts",
    "idx_audit_action",
    "idx_backup_tasks_repo",
    "idx_jobs_task",
}

_JOB_INSERT = (
    "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, policy_id) "
    "VALUES (?, 'backup', 'pending', 'manual', '/tmp/x.log', '2026-01-01T00:00:00Z', 999)"
)

_TASK_INSERT = (
    "INSERT INTO backup_tasks (name, tag, sources, excludes, dry_run_pending, created_at, updated_at) "
    "VALUES ('每日全量', 'task-1', '[\"/sources/etc\"]', '[]', 1, "
    "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
)


async def test_migrate_advances_user_version(tmp_path):
    db = Database(tmp_path / "restic.db")
    await db.connect()
    assert await db.user_version() == 7
    await db.close()


async def test_migrate_is_idempotent_when_run_twice(tmp_path):
    """connect() 已迁移一次后，再显式连跑 migrate() 必须零应用、零副作用。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    assert await db.migrate() == 0
    assert await db.migrate() == 0
    assert await db.user_version() == 7
    await db.close()


async def test_reconnect_does_not_reapply(tmp_path):
    """重新建立连接（模拟进程重启）后迁移不重复执行。"""
    path = tmp_path / "restic.db"
    db1 = Database(path)
    await db1.connect()
    await db1.close()

    db2 = Database(path)
    await db2.connect()
    assert await db2.user_version() == 7
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


async def test_0002_repos_seeded_with_default_row(tmp_path):
    """0002 种 default 仓库行（身份注册表，不镜像 url）；幂等重跑不产生第二行。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    rows = await db.fetch_all("SELECT id, name, created_at FROM repos")
    assert len(rows) == 1
    assert rows[0]["id"] == "default"
    assert rows[0]["name"]
    assert rows[0]["created_at"]

    assert await db.migrate() == 0
    rows = await db.fetch_all("SELECT id FROM repos")
    assert len(rows) == 1
    await db.close()


async def test_0002_backup_tasks_structure(tmp_path):
    """backup_tasks 关键列在位：repo_id 外键 + dry_run_pending；**无 legacy_policy_id**（greenfield）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(backup_tasks)")}
    assert {"id", "repo_id", "name", "enabled", "tag", "sources", "excludes", "cron"} <= columns
    assert "dry_run_pending" in columns
    assert "last_run_at" in columns
    assert "legacy_policy_id" not in columns
    await db.close()


async def test_0002_jobs_task_id_column_exists(tmp_path):
    """jobs.task_id 列存在（ALTER TABLE ADD COLUMN，由 user_version 守卫幂等）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(jobs)")}
    assert "task_id" in columns
    assert "policy_id" in columns  # legacy 列保留（历史兼容读）
    await db.close()


async def test_0004_retention_within_columns(tmp_path):
    """0004（批次 A2）：backup_tasks 新增 keep_within* 六列 + keep_tags，缺省全空。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(backup_tasks)")}
    assert {
        "keep_within",
        "keep_within_hourly",
        "keep_within_daily",
        "keep_within_weekly",
        "keep_within_monthly",
        "keep_within_yearly",
        "keep_tags",
    } <= columns
    # DDL 缺省口径：时长档空串 / tag 档空 JSON 数组（= 未启用，与 keep_* 的 0 一致）
    row = await db.fetch_one(
        "SELECT keep_within, keep_within_yearly, keep_tags FROM backup_tasks LIMIT 1"
    )
    assert row is None  # greenfield 无存量行；缺省值经新建任务路径断言（test_task_service）
    await db.close()


async def test_0004_new_task_defaults_unset(tmp_path):
    """0004 缺省落地：新建任务不显式给新档位 → 空串/空列表（未启用）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    await db.write(_TASK_INSERT)
    row = await db.fetch_one(
        "SELECT keep_within, keep_within_daily, keep_tags FROM backup_tasks WHERE tag = 'task-1'"
    )
    assert row is not None
    assert str(row["keep_within"]) == ""
    assert str(row["keep_within_daily"]) == ""
    assert str(row["keep_tags"]) == "[]"
    await db.close()


async def test_0005_task_retry_column(tmp_path):
    """0005（批次 D C3）：backup_tasks 新增 retry_count，缺省 2（首次 + 2 次重试）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(backup_tasks)")}
    assert "retry_count" in columns
    await db.close()


async def test_0005_new_task_retry_default(tmp_path):
    """0005 缺省落地：新建任务不填 retry_count → 2。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    await db.write(_TASK_INSERT)
    row = await db.fetch_one("SELECT retry_count FROM backup_tasks WHERE tag = 'task-1'")
    assert row is not None
    assert int(row["retry_count"]) == 2
    await db.close()


async def test_0006_task_mirror_column(tmp_path):
    """0006（批次 D C4）：backup_tasks 新增 mirror_repo_ids，缺省 '[]'（= 单目的地）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    columns = {str(row["name"]) for row in await db.fetch_all("PRAGMA table_info(backup_tasks)")}
    assert "mirror_repo_ids" in columns
    await db.close()


async def test_0006_new_task_mirror_default_empty(tmp_path):
    """0006 缺省落地：新建任务不填镜像仓 → '[]'（执行层据此跳过镜像阶段）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    await db.write(_TASK_INSERT)
    row = await db.fetch_one("SELECT mirror_repo_ids FROM backup_tasks WHERE tag = 'task-1'")
    assert row is not None
    assert str(row["mirror_repo_ids"]) == "[]"
    await db.close()


async def test_0002_no_task_materialized(tmp_path):
    """greenfield：迁移不物化首任务——全新部署从「新建首个任务」开始（Q4 裁决）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    row = await db.fetch_one("SELECT COUNT(*) AS total FROM backup_tasks")
    assert int(row["total"]) == 0
    await db.close()


async def test_0002_task_id_foreign_key_enforced(tmp_path):
    """jobs.task_id → backup_tasks(id) 外键真实生效（E7：FK 字面落地，拒绝悬挂引用）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    with pytest.raises(sqlite3.IntegrityError):
        await db.write(
            "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, task_id) "
            "VALUES ('job-fk-task', 'backup', 'pending', 'manual', '/tmp/x.log', "
            "'2026-01-01T00:00:00Z', 999)"
        )
    await db.close()


async def test_0002_task_delete_sets_jobs_task_id_null(tmp_path):
    """删除任务 → jobs.task_id 经 ON DELETE SET NULL 置空，jobs 行保留（US-026-03 AC1）。"""
    db = Database(tmp_path / "restic.db")
    await db.connect()
    await db.write(_TASK_INSERT)
    task_id = int((await db.fetch_one("SELECT last_insert_rowid() AS id"))["id"])
    await db.write(
        "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, task_id) "
        "VALUES ('job-bound', 'backup', 'success', 'scheduled', '/tmp/x.log', "
        "'2026-01-01T00:00:00Z', ?)",
        (task_id,),
    )

    await db.write("DELETE FROM backup_tasks WHERE id = ?", (task_id,))

    job = await db.fetch_one("SELECT task_id FROM jobs WHERE id = 'job-bound'")
    assert job["task_id"] is None
    assert await db.fetch_one("SELECT COUNT(*) AS total FROM backup_tasks") is not None
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
