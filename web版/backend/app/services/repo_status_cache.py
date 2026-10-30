"""仓库状态卡的进程内 TTL 缓存（E30）——唯一持有者与失效出口。

从 ``routers/repos.py`` 独立出来（候选 1 批次 0）的**纯搬迁**：
编排器 ``services/repo_lifecycle.py`` 要在数据变更后失效本缓存，而它住在 services 层——
若从 routers 反向 import 会形成 ``services → routers`` 的层次倒置，且 ``routers/repos``
本就 import services，两者成环。故本模块搬出缓存状态本身，路由只保留薄薄的引用。

**不合并的两个失效入口**（候选 1 决策 7）：
- :func:`invalidate_repo_status_cache`——本缓存（E30 仓库可连性探测结果）；
- ``invalidate_snapshot_catalog``——快照目录缓存（快照计数）。

两者各有独立的 TTL 与 ``on_finish`` 接线，调用时机不同（仓库资料更新 vs 备份终态），
合并会让无关的缓存被连带清空。
"""

from __future__ import annotations

import asyncio
import time

from app.constants import REPO_STATUS_CACHE_SECONDS
from app.schemas.api_schemas import RepoStatusResponse
from app.services.snapshot_catalog import invalidate_snapshot_catalog

__all__ = ["invalidate_repo_status_cache", "read_cached", "status_cache", "status_lock", "write_cached"]

# E30 进程内 TTL 缓存：按 repo_id 分桶（多仓库互不污染）
status_cache: dict[str, tuple[float, RepoStatusResponse]] = {}
status_lock = asyncio.Lock()


def invalidate_repo_status_cache() -> None:
    """清除 E30 的进程内 TTL 缓存（全仓库；配置写入/任务终态时调用）。

    调用时机：仓库资料更新（url/凭据变了）、任务终态（init/unlock 会改变仓库可连性，
    由 JobRunner 的 ``on_finish`` 回调触发）。无锁——字典赋空是原子的。

    P1：连带清快照目录缓存——backup 终态会新增快照，任务卡片的 ``snapshot_count``
    不该继续吃旧快照列表（TTL 之外的即时失效）。
    """
    status_cache.clear()
    invalidate_snapshot_catalog()


def read_cached(repo_id: str) -> RepoStatusResponse | None:
    """读缓存命中项；未命中或已过 TTL 返回 None（E30 口径）。

    Args:
        repo_id: 仓库标识。

    Returns:
        命中的状态响应；无命中或已过 TTL 返回 None。
    """
    cached = status_cache.get(repo_id)
    if cached is None:
        return None
    if time.monotonic() - cached[0] >= REPO_STATUS_CACHE_SECONDS:
        return None
    return cached[1]


def write_cached(repo_id: str, status: RepoStatusResponse) -> None:
    """写入一条缓存（调用方保证已持 :data:`status_lock`）。

    Args:
        repo_id: 仓库标识。
        status: 本次探测得到的状态。
    """
    status_cache[repo_id] = (time.monotonic(), status)