"""backup_tasks 表仓储（增量设计 §1.2 决策一/§4.7）：任务即策略实体（1:1）。

与 :mod:`app.db.repositories.policy_repo` 的关系：字段同构、地位取代（policies 退
legacy 只读）；本仓储是 ``backup_tasks`` 的唯一 SQL 落点（红线 A1）。

tag 生成规则（共享知识 §9.1）：新建任务 ``task-<自增 id>``——插入后**同方法内**回写
（单写连接串行，无竞态）；更新永不变更；DDL ``UNIQUE(tag)`` 兜底。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.db.database import Database
from app.db.repositories.config_repo import ConfigRepo
from app.utils.time import utc_iso

# 备份任务 ``--host`` 的来源键（§4.3：可由环境变量 RESTIC_HOST 覆盖写入）
# 与 policy_repo.HOST_TAG_KEY 同键同义——仓库级配置，不落在任务/策略列上。
HOST_TAG_KEY = "repo.host_tag"

# P2 唯一仓库 id（R-023 起多仓库：repos 表加行 + 仓库切换器，路由/调度器不动——
# 共享知识 §9.8）。定义在仓储层：路由作用域守卫与调度器任务列举共用同一真源。
DEFAULT_REPO_ID = "default"

_COLUMNS = (
    "id", "repo_id", "name", "enabled", "tag", "sources", "excludes",
    "keep_last", "keep_hourly", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly",
    "group_by", "cron", "one_file_system", "compression", "check_sample_ratio",
    "monthly_full_day", "prune_interval_days", "limit_up_kibps", "limit_down_kibps",
    "dry_run_pending", "last_run_at", "created_at", "updated_at",
)
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM backup_tasks"

# 建任务时的插入列缺省（与 TaskService.normalize 输出对应；缺省值即 DDL 缺省值）
_SEED_DEFAULTS: dict[str, Any] = {
    "repo_id": "default",
    "name": "",
    "enabled": 1,
    "tag": "pending",  # 插入后同方法内回写为 task-<id>
    "sources": "[]",
    "excludes": "[]",
    "keep_last": 0,
    "keep_hourly": 0,
    "keep_daily": 0,
    "keep_weekly": 0,
    "keep_monthly": 0,
    "keep_yearly": 0,
    "group_by": "host,paths,tags",
    "cron": None,
    "one_file_system": 0,
    "compression": "auto",
    "check_sample_ratio": 0.02,
    "monthly_full_day": 1,
    "prune_interval_days": 7,
    "limit_up_kibps": 0,
    "limit_down_kibps": 0,
    "dry_run_pending": 1,  # R-026-08：创建即置 1（首跑须先过 E36 dry-run 预览）
    "last_run_at": None,
}


@dataclass(frozen=True)
class Task:
    """backup_tasks 表一行（sources/excludes 以 JSON 文本落库，此处为解析后的列表）。"""

    id: int
    repo_id: str
    name: str
    enabled: bool
    tag: str
    sources: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    keep_last: int = 0
    keep_hourly: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0
    group_by: str = "host,paths,tags"
    cron: str | None = None
    one_file_system: bool = False
    compression: str = "auto"
    check_sample_ratio: float = 0.02
    monthly_full_day: int = 1
    prune_interval_days: int = 7
    limit_up_kibps: int = 0
    limit_down_kibps: int = 0
    dry_run_pending: bool = False
    last_run_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class TaskRepo:
    """backup_tasks 表唯一访问点。"""

    def __init__(self, db: Database, config_repo: ConfigRepo | None = None) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
            config_repo: 配置仓储；缺省按同一 db 构造（``get_dict`` 需要读
                ``repo.host_tag``，它是**仓库级配置**而非任务列，与 PolicyRepo 同口径）。
        """
        self._db = db
        self._config = config_repo or ConfigRepo(db)

    # ---------------------------------------------------------------- 读

    async def get(self, task_id: int) -> Task | None:
        """按 id 读取任务；不存在返回 None。"""
        row = await self._db.fetch_one(f"{_SELECT} WHERE id = ?", (task_id,))
        return _to_task(row) if row else None

    async def get_by_tag(self, tag: str) -> Task | None:
        """按 tag 读取任务；不存在返回 None（forget --tag 归组与迁移首任务用）。"""
        row = await self._db.fetch_one(f"{_SELECT} WHERE tag = ?", (tag,))
        return _to_task(row) if row else None

    async def list_all(self, repo_id: str = "default") -> list[Task]:
        """返回指定仓库下的全部任务（按 id 升序）。"""
        rows = await self._db.fetch_all(f"{_SELECT} WHERE repo_id = ? ORDER BY id ASC", (repo_id,))
        return [_to_task(row) for row in rows]

    async def get_dict(self, task_id: int) -> dict[str, Any] | None:
        """按 JobRunner ``task_loader`` 契约返回任务字典（列名口径，与 policy_loader 同形）。

        Args:
            task_id: 任务 id。

        Returns:
            含 sources/excludes/tag/group_by/one_file_system/compression/keep_*/limit_*_kibps
            以及 ``host_tag``（取自 config 表 ``repo.host_tag``，缺失为 None）的字典；
            任务不存在返回 None。
        """
        task = await self.get(task_id)
        if task is None:
            return None
        return {
            # name 随 profile 透出：JobRunner 冲突判定需要运行中 job 的任务名
            # （E12 409 data 的 running_task_name，US-026-04 AC2）
            "name": task.name,
            "host_tag": await self._config.get_value(HOST_TAG_KEY),
            "sources": list(task.sources),
            "excludes": list(task.excludes),
            "tag": task.tag,
            "group_by": task.group_by,
            "one_file_system": task.one_file_system,
            "compression": task.compression,
            "keep_last": task.keep_last,
            "keep_hourly": task.keep_hourly,
            "keep_daily": task.keep_daily,
            "keep_weekly": task.keep_weekly,
            "keep_monthly": task.keep_monthly,
            "keep_yearly": task.keep_yearly,
            "limit_up_kibps": task.limit_up_kibps,
            "limit_down_kibps": task.limit_down_kibps,
        }

    # ---------------------------------------------------------------- 写

    async def create(self, values: dict[str, Any]) -> Task:
        """插入一条任务，并在同方法内回写 ``tag = 'task-<id>'``（共享知识 §9.1）。

        Args:
            values: 列名字典（sources/excludes 可为 JSON 文本或 list，内部统一序列化）。

        Returns:
            新建的 :class:`Task`（tag 已回写）。
        """
        payload = dict(_SEED_DEFAULTS)
        payload.update(values)
        now = utc_iso()
        columns = list(payload.keys()) + ["created_at", "updated_at"]
        placeholders = ",".join("?" * len(columns))
        params = [_serialize(payload[key]) for key in payload] + [now, now]
        await self._db.write(
            f"INSERT INTO backup_tasks ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(params),
        )
        row = await self._db.fetch_one("SELECT last_insert_rowid() AS id")
        if row is None:  # pragma: no cover - 理论不可达
            raise RuntimeError("任务创建后无法读回自增 id")
        task_id = int(row["id"])
        # tag 回写：task-<id> 形态天然不撞 UNIQUE(tag)；单写连接串行无竞态。
        # 客户端提交的 tag 字段在此被忽略（API 不接收，TaskService.normalize 显式剔除）。
        await self._db.write(
            "UPDATE backup_tasks SET tag = ? WHERE id = ?", (self.generate_tag(task_id), task_id)
        )
        created = await self.get(task_id)
        if created is None:  # pragma: no cover - 理论不可达
            raise RuntimeError("任务创建后无法读回")
        return created

    async def update(self, task_id: int, values: dict[str, Any]) -> Task | None:
        """按列名字典更新任务；不存在返回 None。

        ``tag`` / ``repo_id`` / ``id`` 不接受更新（调用方剔除；仓储层同样显式剔除，
        双保险防 tag 被改导致 forget ``--tag`` 误删他任务快照）。
        """
        payload = {key: value for key, value in values.items() if key not in ("id", "tag", "repo_id")}
        if not payload:
            return await self.get(task_id)
        assignments = []
        params: list[Any] = []
        for key, value in payload.items():
            assignments.append(f"{key} = ?")
            params.append(_serialize(value))
        assignments.append("updated_at = ?")
        params.append(utc_iso())
        params.append(task_id)
        await self._db.write(
            f"UPDATE backup_tasks SET {', '.join(assignments)} WHERE id = ?", tuple(params)
        )
        return await self.get(task_id)

    async def delete(self, task_id: int) -> bool:
        """删除任务；不存在返回 False。

        历史 jobs 的 ``task_id`` 经外键 ``ON DELETE SET NULL`` 自动解绑（行与日志保留）；
        仓库内 tag 快照**不动**（forget 是唯一删快照路径，owner Q7 追问口径）。
        """
        existing = await self.get(task_id)
        if existing is None:
            return False
        await self._db.write("DELETE FROM backup_tasks WHERE id = ?", (task_id,))
        return True

    async def set_last_run_at(self, task_id: int, ts: str) -> None:
        """调度记账：先记账后执行（防崩溃重复触发，§6.4/共享知识 §9.9）。"""
        await self._db.write(
            "UPDATE backup_tasks SET last_run_at = ?, updated_at = ? WHERE id = ?",
            (ts, utc_iso(), task_id),
        )

    async def clear_dry_run_pending(self, task_id: int) -> None:
        """清除首跑门标记（E36 预览成功或首次成功备份后调用，R-026-08）。"""
        await self._db.write(
            "UPDATE backup_tasks SET dry_run_pending = 0, updated_at = ? WHERE id = ?",
            (utc_iso(), task_id),
        )

    @staticmethod
    def generate_tag(task_id: int) -> str:
        """任务 tag 生成规则（单一实现点）：``task-<自增 id>``，只读。"""
        return f"task-{task_id}"


def _serialize(value: Any) -> Any:
    """把列表字段序列化为 JSON 文本，其余原样返回。"""
    if isinstance(value, list | tuple):
        return json.dumps(list(value), ensure_ascii=False)
    return value


def _to_task(row: Any) -> Task:
    """将 sqlite3.Row 转换为 :class:`Task`。"""
    return Task(
        id=int(row["id"]),
        repo_id=str(row["repo_id"]),
        name=str(row["name"]),
        enabled=bool(int(row["enabled"] or 0)),
        tag=str(row["tag"]),
        sources=_load_list(row["sources"]),
        excludes=_load_list(row["excludes"]),
        keep_last=int(row["keep_last"] or 0),
        keep_hourly=int(row["keep_hourly"] or 0),
        keep_daily=int(row["keep_daily"] or 0),
        keep_weekly=int(row["keep_weekly"] or 0),
        keep_monthly=int(row["keep_monthly"] or 0),
        keep_yearly=int(row["keep_yearly"] or 0),
        group_by=str(row["group_by"] or "host,paths,tags"),
        cron=None if row["cron"] is None else str(row["cron"]),
        one_file_system=bool(int(row["one_file_system"] or 0)),
        compression=str(row["compression"] or "auto"),
        check_sample_ratio=float(row["check_sample_ratio"] or 0.0),
        monthly_full_day=int(row["monthly_full_day"] or 1),
        prune_interval_days=int(row["prune_interval_days"] or 7),
        limit_up_kibps=int(row["limit_up_kibps"] or 0),
        limit_down_kibps=int(row["limit_down_kibps"] or 0),
        dry_run_pending=bool(int(row["dry_run_pending"] or 0)),
        last_run_at=None if row["last_run_at"] is None else str(row["last_run_at"]),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


def _load_list(raw: Any) -> list[str]:
    """把 JSON 文本列解析为字符串列表；异常时回落空列表。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list | tuple):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []
