"""全站备份数据层（`db/repositories/site_repo.py`）单测——W2 验收门。

锁死四条不变量（方案 §4.4 / §10.2 W2）：
① `replace_all` 的清表顺序不触外键（`PRAGMA foreign_keys=ON`，`jobs.repo_id` 为
   `NOT NULL` 且无级联）；
② config 值按**存储形态**原样落库——已加密的 `enc:v2:` 密文不得被二次加密；
③ `admin_account` 单行覆写覆盖全部八个状态列；
④ 任务 `id` / `tag` 保留（`tag` 按 `task-<id>` 重算，快照 tag 关联不丢）。
"""

from __future__ import annotations

import pytest

from app.db.database import Database
from app.db.repositories.site_repo import SiteRepo

ADMIN_HASH = "$argon2id$v=19$m=65536,t=3,p=4$packed-hash"
ENC_VALUE = "enc:v2:AAAABBBBCCCC-preexisting-ciphertext"


async def _new_db(tmp_path) -> Database:
    """建库并灌一份「目标机既有数据」（含跨表外键引用）。"""
    db = Database(tmp_path / "site.db")
    await db.connect()
    # repos.default 已由 0002 迁移预置，这里只补 url（目标机「已有数据」的证据）
    await db.write("UPDATE repos SET url = ? WHERE id = 'default'", ("/data/repos/default",))
    await db.write(
        "INSERT INTO backup_tasks (id, repo_id, name, tag, sources, excludes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (3, "default", "既有任务", "task-3", '["/sources/etc"]', "[]",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    # jobs.repo_id 是 NOT NULL 且无 ON DELETE 级联 —— 这一行是清表顺序的试金石
    await db.write(
        "INSERT INTO jobs (id, type, status, trigger, log_path, created_at, repo_id, task_id) "
        "VALUES (?, 'backup', 'success', 'manual', '/data/jobs/j1.log', ?, 'default', 3)",
        ("j1", "2026-01-01T00:00:00Z"),
    )
    await db.write(
        "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, 'ui', ?)",
        ("schedule.cron_default", "0 3 * * *", "2026-01-01T00:00:00Z"),
    )
    await db.write(
        "INSERT INTO admin_account (id, password_hash, must_change, bootstrap_shown, updated_at) "
        "VALUES (1, ?, 1, 0, ?)",
        ("$argon2id$old-hash", "2026-01-01T00:00:00Z"),
    )
    await db.write(
        "INSERT INTO rclone_credential_forms (remote, field, obscured, value_sha256, updated_at) "
        "VALUES ('backup', 'pass', 1, 'old-digest', ?)",
        ("2026-01-01T00:00:00Z",),
    )
    await db.write(
        "INSERT INTO sessions (token_hash, last_seen_at, expires_at, created_at) "
        "VALUES ('h1', ?, ?, ?)",
        ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    return db


def _state(*, included: bool = True) -> dict:
    """一份最小可导入载荷（config 值已是存储形态）。"""
    return {
        "schema_version": 10,
        "admin": {"password_hash": ADMIN_HASH, "must_change": 1},
        "repos": [
            {
                "id": "default",
                "name": "搬来的默认仓库",
                "url": "/data/repos/default",
                "host_tag": "host-a",
                "url_source": "env",
                "host_tag_source": "env",
                "created_at": "2026-02-02T00:00:00Z",
                "deleted_at": None,
            }
        ],
        "tasks": [
            {
                "id": 7,
                "repo_id": "default",
                "name": "搬来的任务",
                "enabled": 1,
                # 包内 tag 与 id 对不上：必须按 id 重算，不能照抄
                "tag": "pending",
                "sources": '["/sources/etc"]',
                "excludes": "[]",
                "dry_run_pending": 1,
                "last_run_at": "2026-02-02T03:00:00Z",
                "created_at": "2026-02-02T00:00:00Z",
                "updated_at": "2026-02-02T00:00:00Z",
            }
        ],
        "config": [
            {"key": "schedule.cron_default", "value": "0 4 * * *", "source": "env"},
            {"key": "cred.default.access_key", "value": ENC_VALUE, "source": "ui"},
        ],
        "rclone": {
            "included": included,
            "forms": [
                {
                    "remote": "backup",
                    "field": "pass",
                    "obscured": 1,
                    "value_sha256": "new-digest",
                    "updated_at": "2026-02-02T01:02:03Z",
                }
            ],
        },
    }


async def _count(db: Database, table: str) -> int:
    row = await db.fetch_one(f"SELECT COUNT(*) AS c FROM {table}")
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------- ① 清表顺序


async def test_replace_all_clears_and_rebuilds_without_fk_violation(tmp_path):
    """有 jobs 引用 repos / backup_tasks 时仍能整体替换（顺序正确才不触外键）。"""
    db = await _new_db(tmp_path)
    try:
        repo = SiteRepo(db)
        async with db.transaction() as tx:
            counts = await repo.replace_all(tx, _state())

        assert counts == {"repos": 1, "tasks": 1, "config": 2, "forms": 1}
        # 目标机既有执行历史被清空（方案 §4.4 副作用明示）
        assert await _count(db, "jobs") == 0
        assert await _count(db, "sessions") == 0
        assert await _count(db, "repos") == 1
        assert await _count(db, "backup_tasks") == 1
        assert await _count(db, "config") == 2
        assert await _count(db, "rclone_credential_forms") == 1
    finally:
        await db.close()


async def test_replace_all_keeps_forms_when_rclone_not_included(tmp_path):
    """`rclone.included=false` → 目标机既有形态记录原样保留（不被清空、不被覆盖）。"""
    db = await _new_db(tmp_path)
    try:
        repo = SiteRepo(db)
        async with db.transaction() as tx:
            counts = await repo.replace_all(tx, _state(included=False))

        assert counts["forms"] == 0
        rows = await db.fetch_all(
            "SELECT remote, field, value_sha256 FROM rclone_credential_forms"
        )
        assert [(r["remote"], r["field"], r["value_sha256"]) for r in rows] == [
            ("backup", "pass", "old-digest")
        ]
    finally:
        await db.close()


async def test_replace_all_rolls_back_on_error(tmp_path):
    """事务内抛错 → 目标机数据逐行不变（清表也被回滚）。"""
    db = await _new_db(tmp_path)
    try:
        repo = SiteRepo(db)
        bad = _state()
        bad["tasks"][0]["id"] = None  # 缺 id → ValueError
        with pytest.raises(ValueError, match="缺少 id"):
            async with db.transaction() as tx:
                await repo.replace_all(tx, bad)

        assert await _count(db, "jobs") == 1
        assert await _count(db, "repos") == 1
        assert await _count(db, "config") == 1
        assert await _count(db, "sessions") == 1
        row = await db.fetch_one("SELECT password_hash FROM admin_account WHERE id = 1")
        assert row is not None and str(row["password_hash"]) == "$argon2id$old-hash"
    finally:
        await db.close()


# ---------------------------------------------------------------- ② 存储形态


async def test_config_values_written_verbatim(tmp_path):
    """已加密的 `enc:v2:` 密文原样落库（本层不再加密一次）且 source 归一为 'ui'。"""
    db = await _new_db(tmp_path)
    try:
        repo = SiteRepo(db)
        async with db.transaction() as tx:
            await repo.replace_all(tx, _state())

        row = await db.fetch_one(
            "SELECT value, source FROM config WHERE key = 'cred.default.access_key'"
        )
        assert row is not None
        assert str(row["value"]) == ENC_VALUE
        assert str(row["source"]) == "ui"

        cron = await db.fetch_one(
            "SELECT value, source FROM config WHERE key = 'schedule.cron_default'"
        )
        assert cron is not None
        assert str(cron["value"]) == "0 4 * * *"
        assert str(cron["source"]) == "ui"  # 包内 'env' 被归一
    finally:
        await db.close()


# ---------------------------------------------------------------- forms.updated_at（§2.3）


async def test_forms_updated_at_roundtrip(tmp_path):
    """导出 SELECT 带出 updated_at，导入 INSERT 原样写回（不覆盖为导入时刻）。"""
    db = await _new_db(tmp_path)
    try:
        forms = await SiteRepo(db).dump_state()
        assert forms["forms"][0]["updated_at"] == "2026-01-01T00:00:00Z"
        state = _state()
        state["rclone"]["forms"][0]["updated_at"] = "2026-03-03T04:05:06Z"
        async with db.transaction() as tx:
            await SiteRepo(db).replace_all(tx, state)
        row = await db.fetch_one("SELECT updated_at FROM rclone_credential_forms")
        assert row["updated_at"] == "2026-03-03T04:05:06Z"
    finally:
        await db.close()


async def test_forms_missing_updated_at_falls_back_to_now(tmp_path):
    """旧包缺 updated_at 时回落导入时刻，不写 NULL（列 NOT NULL）。"""
    db = await _new_db(tmp_path)
    try:
        state = _state()
        del state["rclone"]["forms"][0]["updated_at"]
        async with db.transaction() as tx:
            await SiteRepo(db).replace_all(tx, state)
        row = await db.fetch_one("SELECT updated_at FROM rclone_credential_forms")
        assert row["updated_at"] and row["updated_at"] != "2026-01-01T00:00:00Z"
    finally:
        await db.close()


# ---------------------------------------------------------------- ③ admin 覆写


async def test_admin_row_overwritten_with_all_state_columns(tmp_path):
    """admin_account 单行覆写：八个状态列全部按方案 §4.4 归位。"""
    db = await _new_db(tmp_path)
    try:
        repo = SiteRepo(db)
        async with db.transaction() as tx:
            await repo.replace_all(tx, _state())

        row = await db.fetch_one(
            "SELECT password_hash, must_change, bootstrap_shown, bootstrap_password_hash, "
            "failed_attempts, lock_level, locked_until, updated_at FROM admin_account WHERE id = 1"
        )
        assert row is not None
        assert str(row["password_hash"]) == ADMIN_HASH
        assert int(row["must_change"]) == 1
        assert int(row["bootstrap_shown"]) == 1
        assert row["bootstrap_password_hash"] is None
        assert int(row["failed_attempts"]) == 0
        assert int(row["lock_level"]) == 0
        assert row["locked_until"] is None
        assert str(row["updated_at"]) != "2026-01-01T00:00:00Z"
        assert await _count(db, "admin_account") == 1
    finally:
        await db.close()


# ---------------------------------------------------------------- ④ id / tag


async def test_task_id_kept_and_tag_derived_from_id(tmp_path):
    """任务 id 保留、tag 按 `task-<id>` 重算（快照 tag 关联不丢，方案 §1.6-3）。"""
    db = await _new_db(tmp_path)
    try:
        repo = SiteRepo(db)
        async with db.transaction() as tx:
            await repo.replace_all(tx, _state())

        row = await db.fetch_one(
            "SELECT id, tag, name, repo_id, last_run_at, dry_run_pending FROM backup_tasks"
        )
        assert row is not None
        assert int(row["id"]) == 7
        assert str(row["tag"]) == "task-7"
        assert str(row["name"]) == "搬来的任务"
        assert str(row["repo_id"]) == "default"
        # 运行期列不信包内值：last_run_at 归 NULL（否则谎报上次备份时间）、首跑门归 0
        assert row["last_run_at"] is None
        assert int(row["dry_run_pending"]) == 0
    finally:
        await db.close()


# ---------------------------------------------------------------- dump_state


async def test_dump_state_collects_five_tables_and_schema_version(tmp_path):
    """导出一致快照：五张表 + user_version，且不把运行期状态带成默认值。"""
    db = await _new_db(tmp_path)
    try:
        state = await SiteRepo(db).dump_state()

        assert state["schema_version"] == 10
        assert [r["id"] for r in state["repos"]] == ["default"]
        assert [r["id"] for r in state["tasks"]] == [3]
        assert [c["key"] for c in state["config"]] == ["schedule.cron_default"]
        assert state["admin"] == {"password_hash": "$argon2id$old-hash", "must_change": 1}
        assert [f["remote"] for f in state["forms"]] == ["backup"]
        assert state["forms"][0]["updated_at"] == "2026-01-01T00:00:00Z"
    finally:
        await db.close()


async def test_dump_state_empty_admin_is_none(tmp_path):
    """首启未完成的库（admin_account 无行）导出的 admin 为 None，不伪造哈希。"""
    db = Database(tmp_path / "empty.db")
    await db.connect()
    try:
        state = await SiteRepo(db).dump_state()
        assert state["admin"] is None
        assert state["tasks"] == []
    finally:
        await db.close()
