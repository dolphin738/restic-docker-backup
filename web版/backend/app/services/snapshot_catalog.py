"""快照目录服务（UI 改造 P1）：一次快照列表 → 按 tag 分组计数。

动机：任务卡片的「该任务快照数」按 ``task.tag`` 统计。逐任务走 ``restic snapshots
--tag`` 会退化为 N 次子进程调用（``snapshots.py:12`` 既有裁决：不把过滤下推进 restic
argv，S2 白名单不扩面），故改为**一次列表 + Python 侧分组**。

缓存：进程内 TTL（:data:`~app.constants.SNAPSHOT_LIST_CACHE_SECONDS`，援 E30
``REPO_STATUS_CACHE_SECONDS`` 先例），key = ``repo_id``；**失败结论同样入缓存**——
仓库不可达时若每次列表都真探一次，页面会被探测超时拖住（最多 20s/次）。

失败语义：``counts_by_tag`` 返回 ``None`` 表示「快照数此刻不可知」（未配置仓库地址 /
restic 非 0 退出 / 解析无结果），调用方据此把卡片字段置 ``null``——**不可用 ≠ 0**，
0 会让用户误判「任务从未产出快照」。
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.constants import SNAPSHOT_LIST_CACHE_SECONDS, SNAPSHOT_LIST_TIMEOUT_SECONDS
from app.services.parser import OutputParser
from app.services.restic import ResticCmd, WhitelistArgs

logger = logging.getLogger(__name__)

# 进程内 TTL 缓存：{repo_id: (monotonic_ts, counts|None)}
_tag_counts_cache: dict[str, tuple[float, dict[str, int] | None]] = {}


class SnapshotCatalog:
    """按 tag 统计快照数的只读服务（stateless，只持有 TTL 配置）。"""

    def __init__(self, cache_seconds: float | None = None) -> None:
        """构造服务。

        Args:
            cache_seconds: 缓存 TTL（秒）；缺省取 ``SNAPSHOT_LIST_CACHE_SECONDS``。
        """
        self._cache_seconds = SNAPSHOT_LIST_CACHE_SECONDS if cache_seconds is None else cache_seconds
        self._lock = asyncio.Lock()

    async def counts_by_tag(
        self,
        *,
        restic: ResticCmd,
        parser: OutputParser,
        repo_id: str,
        repo_url: str,
        env: dict[str, str],
    ) -> dict[str, int] | None:
        """返回 ``{tag: 快照数}``；快照列表不可用时返回 ``None``。

        Args:
            restic: restic 命令构造器（S2 白名单拼装唯一出处）。
            parser: 输出解析器（纯函数，零 IO）。
            repo_id: 仓库 id（缓存 key）。
            repo_url: 仓库地址；空串视为未配置 → 直接 ``None``（不跑 restic）。
            env: 已含凭据的子进程环境（由调用方 ``build_env`` 产出，本服务不接触凭据明文）。

        Returns:
            ``{tag: count}``（同一快照带多个 tag 时各 tag 各计一次）；
            ``None`` = 不可用（未配置 / 探测失败 / 探测异常）。
        """
        if not (repo_url or "").strip():
            return None

        cached = _tag_counts_cache.get(repo_id)
        if cached is not None and time.monotonic() - cached[0] < self._cache_seconds:
            return cached[1]

        async with self._lock:
            cached = _tag_counts_cache.get(repo_id)  # 双检：等锁期间可能已被刷新
            if cached is not None and time.monotonic() - cached[0] < self._cache_seconds:
                return cached[1]
            try:
                counts = await self._probe(restic=restic, parser=parser, repo_url=repo_url, env=env)
            except Exception:  # 探测异常（子进程启动失败等）不冒泡：卡片字段降级为 null
                logger.warning("快照列表探测异常（snapshot_count 降级为 null）：repo=%s", repo_id, exc_info=True)
                counts = None
            _tag_counts_cache[repo_id] = (time.monotonic(), counts)
            return counts

    async def _probe(
        self,
        *,
        restic: ResticCmd,
        parser: OutputParser,
        repo_url: str,
        env: dict[str, str],
    ) -> dict[str, int] | None:
        """执行一次 ``restic snapshots --json`` 并按 tag 分组计数（未缓存实现）。"""
        argv = restic.build("snapshots", args=WhitelistArgs(json_output=True))
        probe = await restic.run_json(argv, env=env, timeout_seconds=SNAPSHOT_LIST_TIMEOUT_SECONDS)
        if probe.exit_code != 0:
            logger.warning("快照列表探测失败（exit=%s）：snapshot_count 降级为 null", probe.exit_code)
            return None
        records = await asyncio.to_thread(parser.parse_snapshot_list, "\n".join(probe.lines))
        counts: dict[str, int] = {}
        for record in records:
            if not record.snapshot_id:
                continue
            for tag in record.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts


def invalidate_snapshot_catalog() -> None:
    """清除快照目录缓存（全仓库；配置写入/任务终态时随 E30 状态缓存一起失效）。

    无锁——字典赋空是原子的（与 ``invalidate_repo_status_cache`` 同口径）。
    """
    _tag_counts_cache.clear()
