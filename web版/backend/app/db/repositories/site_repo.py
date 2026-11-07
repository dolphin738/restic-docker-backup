"""全站备份的数据层（A1 唯一 SQL 出口；A5 全部写经 `Database.transaction()`）。

职责边界（与 `services/site_backup.py` 的分工）：
- 本模块**只碰 DB**，不读也不写任何文件（仓库密码文件 / `rclone.conf` / `rclone.pass` 由
  服务层在事务外处理）——`Database.transaction()` 内不得有非 SQL await（见 `database.py`
  事务 docstring），故 `replace_all` 收到的 `config` 值必须是**已加密好的存储形态**。
- 导出：`dump_state()` 在单个事务里取一致快照（五张表 + `PRAGMA user_version`）。
- 导入：`replace_all(tx, state)` 在一个事务内完成「清表 + 重建」。清表顺序受
  `PRAGMA foreign_keys=ON`（`database.py:105`）约束——`jobs.repo_id` 是 `NOT NULL` 且无
  `ON DELETE` 级联（`0003_multi_repos.sql:15`），不先删 jobs 就删不掉 repos。

列的单一真源仍是 `task_repo._COLUMNS` / `task_repo._SEED_DEFAULTS`（方案 §2.4 列映射口径：
多余列丢弃、缺失列取默认），本模块**不复制第二份清单**。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.db.database import Database, Tx
from app.db.repositories import task_repo
from app.db.repositories.task_repo import TaskRepo
from app.utils.time import utc_iso

__all__ = ["SiteRepo"]

# 清表顺序：jobs 必须最先删（外键无级联）；forms 由「是否随包搬 conf」单独决定。
_DELETE_ORDER = (
    "DELETE FROM jobs",
    "DELETE FROM backup_tasks",
    "DELETE FROM repos",
    "DELETE FROM config",
    "DELETE FROM sessions",
)

_REPO_COLUMNS = (
    "id",
    "name",
    "url",
    "host_tag",
    "url_source",
    "host_tag_source",
    "created_at",
    "deleted_at",
)
_REPO_SELECT = "SELECT " + ", ".join(_REPO_COLUMNS) + " FROM repos ORDER BY id"
_REPO_INSERT_SQL = (
    "INSERT INTO repos (id, name, url, host_tag, url_source, host_tag_source, "
    "created_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_CONFIG_INSERT_SQL = "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, ?, ?)"
_FORM_SELECT = (
    "SELECT remote, field, obscured, value_sha256, updated_at "
    "FROM rclone_credential_forms ORDER BY remote, field"
)
_FORM_INSERT_SQL = (
    "INSERT INTO rclone_credential_forms (remote, field, obscured, value_sha256, updated_at) "
    "VALUES (?, ?, ?, ?, ?)"
)
_ADMIN_SELECT = "SELECT password_hash, must_change FROM admin_account WHERE id = 1"
_ADMIN_UPDATE_SQL = (
    "UPDATE admin_account SET password_hash = ?, must_change = ?, bootstrap_shown = 1, "
    "bootstrap_password_hash = NULL, failed_attempts = 0, lock_level = 0, "
    "locked_until = NULL, updated_at = ? WHERE id = 1"
)

# 目标机现状行数（inspect 摘要 + 「目标非空」判定）；语句是字面量，不拼 SQL。
_COUNT_SQL: dict[str, str] = {
    "repos": "SELECT COUNT(*) FROM repos",
    # 预置的 default 仓库不算「已配置」：0002 迁移无条件插入它，否则全新实例永远判为非空。
    "repos_configured": "SELECT COUNT(*) FROM repos WHERE url IS NOT NULL OR host_tag IS NOT NULL",
    "tasks": "SELECT COUNT(*) FROM backup_tasks",
    "config": "SELECT COUNT(*) FROM config",
    "jobs": "SELECT COUNT(*) FROM jobs",
}

# 导入时强制覆盖的运行期列（方案 §3.4）：导入不是待确认草稿，一律取 0 / 空，不信任包内值。
_IMPORT_DEFAULTS: dict[str, Any] = {"dry_run_pending": 0, "last_run_at": None}


class SiteRepo:
    """全站备份的数据层仓储（五张纳入表 + `sessions` / `jobs` 的清空）。"""

    def __init__(self, db: Database) -> None:
        """绑定数据库连接。

        Args:
            db: 进程内唯一写连接。
        """
        self._db = db

    async def counts(self) -> dict[str, int]:
        """目标机现状行数（导入预检摘要与「目标非空」判定用；只读）。

        Returns:
            键：repo / tasks / config / jobs 行数，外加 `repos_configured`（url 或 host_tag
            非空的仓库数）——全新实例里 `repos` 恒有 0002 预置的一行，不能拿它判非空。
        """
        out: dict[str, int] = {}
        for key, sql in _COUNT_SQL.items():
            row = await self._db.fetch_one(sql)
            out[key] = int(row[0]) if row is not None else 0
        return out

    # ---------------------------------------------------------------- 导出

    async def dump_state(self) -> dict[str, Any]:
        """取 DB 侧一致快照（纯 SQL，事务内无 IO）。

        用 `BEGIN IMMEDIATE` 包住五次读：单写连接下这是唯一能保证「导出的五张表属于
        同一时刻」的手段（两次读之间可能有任务被创建）。

        Returns:
            含 `schema_version` / `config` / `repos` / `tasks` / `admin` / `forms`。
            `tasks` 是原始行（含 `last_run_at` 等运行期列），剔除策略属导出编排，
            由服务层按方案 §3.4 的 `_TASK_DROP` 处理。
        """
        task_select = "SELECT " + ", ".join(task_repo._COLUMNS) + " FROM backup_tasks ORDER BY id"
        async with self._db.transaction():
            config = [
                dict(row)
                for row in await self._db.fetch_all(
                    "SELECT key, value, source FROM config ORDER BY key"
                )
            ]
            repos = [dict(row) for row in await self._db.fetch_all(_REPO_SELECT)]
            tasks = [dict(row) for row in await self._db.fetch_all(task_select)]
            admin_row = await self._db.fetch_one(_ADMIN_SELECT)
            forms = [dict(row) for row in await self._db.fetch_all(_FORM_SELECT)]
            version_row = await self._db.fetch_one("PRAGMA user_version")
        return {
            "schema_version": int(version_row[0]) if version_row else 0,
            "config": config,
            "repos": repos,
            "tasks": tasks,
            "admin": dict(admin_row) if admin_row is not None else None,
            "forms": forms,
        }

    # ---------------------------------------------------------------- 导入

    async def replace_all(self, tx: Tx, state: Mapping[str, Any]) -> dict[str, int]:
        """清空目标机全站数据并重建（单事务；提交/回滚由调用方的 `transaction()` 负责）。

        Args:
            tx: 事务句柄（本方法不提交）。
            state: 已校验的载荷。`config[].value` 必须是**存储形态**（明文/密文已由
                调用方在事务外用 `ConfigRepo.encode_for_storage` 算好）；
                `rclone.included=false` 时保留目标机既有的 `rclone_credential_forms`。
                `rclone.forms[].updated_at` 随包往返（§2.3）；缺值才回落 `now`。

        Returns:
            各段写入行数：`repos` / `tasks` / `config` / `forms`。

        Raises:
            ValueError: 任务行缺少 `id`（全站包必须保留任务 id，方案 §1.6-3）。
        """
        for sql in _DELETE_ORDER:
            await tx.execute(sql)
        rclone = state.get("rclone") or {}
        if rclone.get("included"):
            await tx.execute("DELETE FROM rclone_credential_forms")
        now = utc_iso()

        admin = state.get("admin")
        if admin:
            await tx.execute(
                _ADMIN_UPDATE_SQL,
                (
                    str(admin.get("password_hash") or ""),
                    1 if admin.get("must_change") else 0,
                    now,
                ),
            )

        repos = 0
        for row in state.get("repos") or ():
            await tx.execute(_REPO_INSERT_SQL, _repo_values(row, now))
            repos += 1

        tasks = 0
        for row in state.get("tasks") or ():
            values = _task_values(row)
            columns = list(values)
            placeholders = ", ".join("?" * len(columns))
            await tx.execute(
                f"INSERT INTO backup_tasks ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(task_repo._serialize(values[key]) for key in columns),
            )
            tasks += 1

        config = 0
        for row in state.get("config") or ():
            key = str(row.get("key") or "")
            if not key:
                continue
            # source 一律归一为 'ui'（方案 §1.6-1）：源机的 'env' 标记在新机没有对应环境
            # 变量，留着会在启动期 sync_env_overrides 里被删掉，等于静默丢配置。
            await tx.execute(_CONFIG_INSERT_SQL, (key, row.get("value"), "ui", now))
            config += 1

        forms = 0
        if rclone.get("included"):
            for row in rclone.get("forms") or ():
                await tx.execute(
                    _FORM_INSERT_SQL,
                    (
                        str(row.get("remote") or ""),
                        str(row.get("field") or ""),
                        1 if row.get("obscured") else 0,
                        str(row.get("value_sha256") or ""),
                        str(row.get("updated_at") or now),
                    ),
                )
                forms += 1

        return {"repos": repos, "tasks": tasks, "config": config, "forms": forms}


def _repo_values(row: Mapping[str, Any], now: str) -> tuple[Any, ...]:
    """把包内仓库行整理成 `repos` 八列（两个来源列归一为 'ui'，缺列取安全缺省）。"""
    repo_id = str(row.get("id") or "")
    return (
        repo_id,
        str(row.get("name") or repo_id),
        row.get("url"),
        row.get("host_tag"),
        "ui",
        "ui",
        str(row.get("created_at") or now),
        row.get("deleted_at"),
    )


def _task_values(row: Mapping[str, Any]) -> dict[str, Any]:
    """把包内任务行整理成 `backup_tasks` 列字典（白名单 + 缺列补默认 + tag 派生）。"""
    values = {key: row[key] for key in task_repo._COLUMNS if key in row}
    for key in task_repo._SEED_DEFAULTS:
        if key not in values:
            values[key] = task_repo._SEED_DEFAULTS[key]
    # 运行期列一律不信包内值（导出侧已剔除，这里再强制一遍）：导入是既定事实，不是
    # 待确认草稿，带回来的 last_run_at 会谎报「上次备份时间」、dry_run_pending 会拦下首跑。
    values.update(_IMPORT_DEFAULTS)
    raw_id = values.get("id")
    if raw_id is None:
        raise ValueError("备份包任务缺少 id（全站包必须保留任务 id，见方案 §1.6-3）")
    task_id = int(raw_id)
    values["id"] = task_id
    # tag 一律按 task-<id> 重算而不取包内值：列上有 UNIQUE 约束，且 restic 快照 tag 必须
    # 与任务 id 对应（方案 §1.6-3）——包里那份只作参考。
    values["tag"] = TaskRepo.generate_tag(task_id)
    return values