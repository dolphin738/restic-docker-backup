"""快照目录服务（UI 改造 P1）：一次快照列表 → 按 tag 分组计数。

动机：任务卡片的「该任务快照数」按 ``task.tag`` 统计。逐任务走 ``restic snapshots
--tag`` 会退化为 N 次子进程调用（``snapshots.py:12`` 既有裁决：不把过滤下推进 restic
argv，S2 白名单不扩面），故改为**一次列表 + Python 侧分组**。

缓存：进程内 TTL（:data:`~app.constants.SNAPSHOT_LIST_CACHE_SECONDS`），key = ``repo_id``；
**失败结论同样入缓存**——仓库不可达时若每次列表都真探一次，页面会被探测超时拖住。

失败退避（B4，owner 2026-10-23 裁决）：探测超时取 :data:`SNAPSHOT_COUNT_TIMEOUT_SECONDS`
（5s，而非列表页 20s）；连续失败按 ``30s → 60s → 120s → 240s → 300s``（封顶
:data:`SNAPSHOT_COUNT_BACKOFF_MAX_SECONDS`）指数退避，一旦成功即复位——避免「每 30s 到期
再白等一次超时」的锯齿体验。

失败语义：``counts_by_tag`` 返回 ``None`` 表示「快照数此刻不可知」（未配置仓库地址 /
restic 非 0 退出 / 解析无结果），调用方据此把卡片字段置 ``null``——**不可用 ≠ 0**，
0 会让用户误判「任务从未产出快照」。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.constants import (
    SNAPSHOT_COUNT_BACKOFF_MAX_SECONDS,
    SNAPSHOT_COUNT_TIMEOUT_SECONDS,
    SNAPSHOT_LIST_CACHE_SECONDS,
)
from app.services.parser import OutputParser
from app.services.restic import ResticCmd, WhitelistArgs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotTally:
    """一次快照列表探测的汇总（只读快照）。

    Attributes:
        counts: ``{tag: 快照数}``（同一快照带多个 tag 时各 tag 各计一次）。
        total: 仓库内快照**总条数**——**含**无 tag / 被 ``tag --remove`` 摘掉
            task tag 的手工快照，故 ``total >= sum(counts.values())``。
            删除门/归档页显示「快照数」必须取 ``total``（裁决 D6）。
    """

    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0


# 进程内 TTL 缓存：{repo_id: (monotonic_ts, tally|None, ttl_seconds, consecutive_failures)}
_tag_counts_cache: dict[str, tuple[float, SnapshotTally | None, float, int]] = {}


class SnapshotCatalog:
    """按 tag 统计快照数的只读服务（stateless，只持有 TTL / 超时配置）。"""

    def __init__(self, cache_seconds: float | None = None, timeout_seconds: float | None = None) -> None:
        """构造服务。

        Args:
            cache_seconds: 成功结论的缓存 TTL（秒）；缺省取 ``SNAPSHOT_LIST_CACHE_SECONDS``。
            timeout_seconds: 单次探测超时（秒）；缺省取 ``SNAPSHOT_COUNT_TIMEOUT_SECONDS``。
        """
        self._cache_seconds = SNAPSHOT_LIST_CACHE_SECONDS if cache_seconds is None else cache_seconds
        self._timeout_seconds = SNAPSHOT_COUNT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        self._lock = asyncio.Lock()

    async def _cached_tally(
        self,
        *,
        restic: ResticCmd,
        parser: OutputParser,
        repo_id: str,
        repo_url: str,
        env: dict[str, str],
        timeout_seconds: float | None = None,
    ) -> SnapshotTally | None:
        """带 TTL/退避的缓存层：一次探测、按 repo_id 复用（未命中的实现见 :meth:`_probe`）。"""
        if not (repo_url or "").strip():
            return None

        cached = _tag_counts_cache.get(repo_id)
        if cached is not None and time.monotonic() - cached[0] < cached[2]:
            return cached[1]

        async with self._lock:
            cached = _tag_counts_cache.get(repo_id)  # 双检：等锁期间可能已被刷新
            if cached is not None and time.monotonic() - cached[0] < cached[2]:
                return cached[1]
            prev_failures = cached[3] if cached is not None else 0
            try:
                tally = await self._probe(
                    restic=restic,
                    parser=parser,
                    repo_url=repo_url,
                    env=env,
                    timeout_seconds=timeout_seconds or self._timeout_seconds,
                )
            except Exception:  # 探测异常（子进程启动失败等）不冒泡：卡片字段降级为 null
                logger.warning("快照列表探测异常（snapshot_count 降级为 null）：repo=%s", repo_id, exc_info=True)
                tally = None

            if tally is None:
                # 失败/不可知：指数退避（首次失败即 2× 起跳，避免立刻再白等一次超时）
                failures = prev_failures + 1
                ttl = min(SNAPSHOT_COUNT_BACKOFF_MAX_SECONDS, self._cache_seconds * (2**failures))
                logger.warning("快照计数不可用（第 %s 次失败，%.0fs 内不再探测）：repo=%s", failures, ttl, repo_id)
                _tag_counts_cache[repo_id] = (time.monotonic(), None, ttl, failures)
                return None

            _tag_counts_cache[repo_id] = (time.monotonic(), tally, self._cache_seconds, 0)
            return tally

    async def counts_by_tag(
        self,
        *,
        restic: ResticCmd,
        parser: OutputParser,
        repo_id: str,
        repo_url: str,
        env: dict[str, str],
        timeout_seconds: float | None = None,
    ) -> dict[str, int] | None:
        """返回 ``{tag: 快照数}``；快照列表不可用时返回 ``None``。

        Args:
            restic: restic 命令构造器（S2 白名单拼装唯一出处）。
            parser: 输出解析器（纯函数，零 IO）。
            repo_id: 仓库 id（缓存 key）。
            repo_url: 仓库地址；空串视为未配置 → 直接 ``None``（不跑 restic）。
            env: 已含凭据的子进程环境（由调用方 ``build_env`` 产出，本服务不接触凭据明文）。
            timeout_seconds: 覆盖构造时的默认探测超时（调用方可按场景收紧）。

        Returns:
            ``{tag: count}``（同一快照带多个 tag 时各 tag 各计一次）；
            ``None`` = 不可用（未配置 / 探测失败 / 探测异常）。
        """
        tally = await self._cached_tally(
            restic=restic,
            parser=parser,
            repo_id=repo_id,
            repo_url=repo_url,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        return tally.counts if tally is not None else None

    async def snapshot_tally(
        self,
        *,
        restic: ResticCmd,
        parser: OutputParser,
        repo_id: str,
        repo_url: str,
        env: dict[str, str],
        timeout_seconds: float | None = None,
    ) -> SnapshotTally | None:
        """返回 ``SnapshotTally``（含**总条数**）；不可用时返回 ``None``。

        删除门与归档页展示「快照数」应取 :attr:`SnapshotTally.total`——
        tag 求和只覆盖带 tag 的快照，会漏计手工备份或摘掉 task tag 的快照。
        """
        return await self._cached_tally(
            restic=restic,
            parser=parser,
            repo_id=repo_id,
            repo_url=repo_url,
            env=env,
            timeout_seconds=timeout_seconds,
        )

    async def _probe(
        self,
        *,
        restic: ResticCmd,
        parser: OutputParser,
        repo_url: str,
        env: dict[str, str],
        timeout_seconds: float,
    ) -> SnapshotTally | None:
        """执行一次 ``restic snapshots --json`` → 按 tag 分组计数 + 总条数（未缓存实现）。"""
        argv = restic.build("snapshots", args=WhitelistArgs(json_output=True))
        probe = await restic.run_json(argv, env=env, timeout_seconds=timeout_seconds)
        if probe.exit_code != 0:
            logger.warning(
                "快照列表探测失败（exit=%s，超时 %.0fs）：snapshot_count 降级为 null",
                probe.exit_code,
                timeout_seconds,
            )
            return None
        records = await asyncio.to_thread(parser.parse_snapshot_list, "\n".join(probe.lines))
        valid = [record for record in records if record.snapshot_id]
        counts: dict[str, int] = {}
        for record in valid:
            for tag in record.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return SnapshotTally(counts=counts, total=len(valid))


def invalidate_snapshot_catalog() -> None:
    """清除快照目录缓存（全仓库，含退避计数；配置写入/任务终态时随 E30 状态缓存一起失效）。

    无锁——字典赋空是原子的（与 ``invalidate_repo_status_cache`` 同口径）。
    """
    _tag_counts_cache.clear()
