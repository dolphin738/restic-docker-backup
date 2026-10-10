"""policies 表仓储（§4.4）：P1 建表即含全字段，UI 分期暴露（P1 仅默认策略可写）。

``is_default`` 恒一条的约束由**应用层**保证（本仓储不建唯一索引，便于 P2 平滑开放多策略）：
- :meth:`PolicyRepo.ensure_default` 在无默认策略时补建一条；
- 写入路径由 :class:`PolicyService` 与路由层做"仅默认策略可写"的门禁。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.db.database import Database
from app.utils.time import utc_iso

_COLUMNS = (
    "id", "name", "is_default", "enabled", "tag", "sources", "excludes",
    "keep_last", "keep_hourly", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly",
    "group_by", "cron", "one_file_system", "compression", "check_sample_ratio",
    "monthly_full_day", "prune_interval_days", "limit_up_kibps", "limit_down_kibps",
    "last_run_at", "created_at", "updated_at",
)
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM policies"

# 建默认策略时的插入列（与 _SEED_DEFAULTS 对应）
_SEED_DEFAULTS: dict[str, Any] = {
    "name": "default",
    "is_default": 1,
    "enabled": 1,
    "tag": "auto",
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
    "last_run_at": None,
}


@dataclass(frozen=True)
class Policy:
    """policies 表一行（sources/excludes 以 JSON 文本落库，此处为解析后的列表）。"""

    id: int
    name: str
    is_default: bool
    enabled: bool
    tag: str | None
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
    last_run_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class PolicyRepo:
    """policies 表唯一访问点。"""

    def __init__(self, db: Database) -> None:
        """构造仓储。

        Args:
            db: 数据库单例。
        """
        self._db = db

    # ---------------------------------------------------------------- 读

    async def get(self, policy_id: int) -> Policy | None:
        """按 id 读取策略；不存在返回 None。"""
        row = await self._db.fetch_one(f"{_SELECT} WHERE id = ?", (policy_id,))
        return _to_policy(row) if row else None

    async def get_default(self) -> Policy | None:
        """读取默认策略（``is_default=1``）；不存在返回 None。"""
        row = await self._db.fetch_one(f"{_SELECT} WHERE is_default = 1 ORDER BY id ASC LIMIT 1")
        return _to_policy(row) if row else None

    async def list_all(self) -> list[Policy]:
        """返回全部策略（按 id 升序）。"""
        rows = await self._db.fetch_all(f"{_SELECT} ORDER BY id ASC")
        return [_to_policy(row) for row in rows]

    async def get_dict(self, policy_id: int) -> dict[str, Any] | None:
        """按 JobRunner ``policy_loader`` 契约返回策略字典（列名口径）。

        Args:
            policy_id: 策略 id。

        Returns:
            含 sources/excludes/tag/group_by/one_file_system/compression/keep_*/limit_*_kibps 的字典；
            策略不存在返回 None。
        """
        policy = await self.get(policy_id)
        if policy is None:
            return None
        return {
            "sources": list(policy.sources),
            "excludes": list(policy.excludes),
            "tag": policy.tag,
            "group_by": policy.group_by,
            "one_file_system": policy.one_file_system,
            "compression": policy.compression,
            "keep_last": policy.keep_last,
            "keep_hourly": policy.keep_hourly,
            "keep_daily": policy.keep_daily,
            "keep_weekly": policy.keep_weekly,
            "keep_monthly": policy.keep_monthly,
            "keep_yearly": policy.keep_yearly,
            "limit_up_kibps": policy.limit_up_kibps,
            "limit_down_kibps": policy.limit_down_kibps,
        }

    # ---------------------------------------------------------------- 写

    async def create(self, values: dict[str, Any]) -> Policy:
        """插入一条策略。

        Args:
            values: 列名字典（sources/excludes 为 JSON 文本或 list，内部统一序列化）。

        Returns:
            新建的 :class:`Policy`。
        """
        payload = dict(_SEED_DEFAULTS)
        payload.update(values)
        now = utc_iso()
        columns = list(payload.keys()) + ["created_at", "updated_at"]
        placeholders = ",".join("?" * len(columns))
        params = [_serialize(payload[key]) for key in payload] + [now, now]
        await self._db.write(
            f"INSERT INTO policies ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(params),
        )
        row = await self._db.fetch_one("SELECT last_insert_rowid() AS id")
        created = await self.get(int(row["id"])) if row else None
        if created is None:  # pragma: no cover - 理论不可达
            raise RuntimeError("策略创建后无法读回")
        return created

    async def update(self, policy_id: int, values: dict[str, Any]) -> Policy | None:
        """按列名字典更新策略；不存在返回 None。

        Args:
            policy_id: 策略 id。
            values: 待更新列（不含 id/created_at）。

        Returns:
            更新后的 :class:`Policy`；不存在返回 None。
        """
        if not values:
            return await self.get(policy_id)
        assignments = []
        params: list[Any] = []
        for key, value in values.items():
            assignments.append(f"{key} = ?")
            params.append(_serialize(value))
        assignments.append("updated_at = ?")
        params.append(utc_iso())
        params.append(policy_id)
        await self._db.write(
            f"UPDATE policies SET {', '.join(assignments)} WHERE id = ?", tuple(params)
        )
        return await self.get(policy_id)

    async def delete(self, policy_id: int) -> bool:
        """删除策略；不存在返回 False。"""
        existing = await self.get(policy_id)
        if existing is None:
            return False
        await self._db.write("DELETE FROM policies WHERE id = ?", (policy_id,))
        return True

    async def ensure_default(self, *, one_file_system: bool = False) -> Policy:
        """保证默认策略存在：无则按缺省值补建一条（供启动期调用）。

        Args:
            one_file_system: 缺省取自运行模式（sources 模式缺省 true，方案 §3.2）。

        Returns:
            已存在的或新建的默认策略。
        """
        existing = await self.get_default()
        if existing is not None:
            return existing
        seed = dict(_SEED_DEFAULTS)
        seed["one_file_system"] = 1 if one_file_system else 0
        return await self.create(seed)


def _serialize(value: Any) -> Any:
    """把列表字段序列化为 JSON 文本，其余原样返回。"""
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    return value


def _to_policy(row: Any) -> Policy:
    """将 sqlite3.Row 转换为 :class:`Policy`。"""
    return Policy(
        id=int(row["id"]),
        name=str(row["name"]),
        is_default=bool(int(row["is_default"] or 0)),
        enabled=bool(int(row["enabled"] or 0)),
        tag=None if row["tag"] is None else str(row["tag"]),
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
        last_run_at=None if row["last_run_at"] is None else str(row["last_run_at"]),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


def _load_list(raw: Any) -> list[str]:
    """把 JSON 文本列解析为字符串列表；异常时回落空列表。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []
