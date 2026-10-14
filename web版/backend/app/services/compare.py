"""覆盖比对服务（§7.8，R-015）：快照内容 × 目标磁盘现状 → 将被覆盖清单。

比对口径（增量设计 §3.3 / Q4 裁决）：

- 快照侧取 ``restic ls --json`` 的 ``mtime`` / ``size``，磁盘侧取 ``os.stat``；
- **两侧 mtime 或 size 任一不同**即记为覆盖项；目标不存在 = 新建而非覆盖，不记；
- 预览只承诺「调用时点」的磁盘现状——TOCTOU 窗口内不保证不变，故每次调用都
  重新比对、**不做任何缓存**（Q4 G3 DoD：预览时点一致 + 执行后回报实际值）。

落点映射（与执行层**同基底**，S7 v1.3 落点纪律）：执行层恒为
``--target /`` + 快照内绝对路径，即**原样写回**快照路径，故预览的磁盘落点直接取
``ls`` 记录的 ``path``（不再以 target 重定基点）。清单范围同步收紧为 **target
子树**内的条目——与执行层 ``--include=<target>`` 的实际写入范围一致，避免「报了
执行层根本不会写的文件」。``target`` 命中哪条快照根只用于定位与越界兜底。

异步纪律（C4）：``ls`` 输出解析与 ``os.stat`` 批量取状态都走 ``asyncio.to_thread``，
不裸跑在协程里。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from app.constants import (
    COMPARE_MTIME_TOLERANCE_SECONDS,
    RESTORE_PREVIEW_TIMEOUT_SECONDS,
    SNAPSHOT_LIST_TIMEOUT_SECONDS,
)
from app.db.repositories.config_repo import ConfigRepo
from app.errors import ResticFailedError
from app.services.env_sync import load_credentials
from app.services.parser import FileRecord, OutputParser
from app.services.restic import ProbeResult, ResticCmd, WhitelistArgs, failure_reason
from app.utils.time import parse_iso, utc_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverwriteItem:
    """单条将被覆盖的文件（设计 §2.5 响应字段）。

    Attributes:
        path: 磁盘落点（**容器形态**，如 ``/host/etc/nginx/nginx.conf``）——
            即快照内绝对路径原样写回的位置；出 API 时由路由层派生
            ``display_path``（S1/A2）。
        snapshot_mtime: 快照侧 mtime（UTC ISO）。
        disk_mtime: 磁盘侧 mtime（UTC ISO）。
        snapshot_size: 快照侧字节数（restic 缺失时为 None）。
        disk_size: 磁盘侧字节数。
    """

    path: str
    snapshot_mtime: str | None = None
    disk_mtime: str | None = None
    snapshot_size: int | None = None
    disk_size: int = 0


class CompareScan:
    """一次比对扫描句柄：``async for`` 产出覆盖项，迭代结束后 ``truncated`` 有效。

    为什么要句柄而不是裸 async generator：``restic ls`` 的输出截断标记
    （:class:`ProbeResult.truncated`）必须回给 E20 响应的 ``truncated`` 字段，
    而 async generator 无法在 ``async for`` 之外回传值。
    """

    def __init__(self, service: CompareService, snapshot_id: str, target: str, roots: list[str] | None) -> None:
        """构造扫描句柄（惰性：真正的子进程调用发生在迭代时）。

        Args:
            service: 比对服务（持有 restic / parser / config_repo）。
            snapshot_id: 快照 id（可为 restic 接受的前缀）。
            target: 原位恢复目标（容器形态，已过双重锁）。
            roots: 快照备份根（路由层已取到时传入，省一次 ``snapshots --json``）。
        """
        self._service = service
        self._snapshot_id = snapshot_id
        self._target = target
        self._roots = roots
        self.truncated = False

    def __aiter__(self) -> AsyncIterator[OverwriteItem]:
        """返回覆盖项迭代器。"""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[OverwriteItem]:
        """执行扫描并逐条产出覆盖项。"""
        items, truncated = await self._service.scan(self._snapshot_id, self._target, self._roots)
        self.truncated = truncated
        for item in items:
            yield item


class CompareService:
    """快照 × 磁盘的覆盖比对（E20 预览与原位恢复审计计数的唯一数据源）。"""

    def __init__(self, restic: ResticCmd, parser: OutputParser, config_repo: ConfigRepo) -> None:
        """构造比对服务。

        Args:
            restic: restic 命令器（``ls`` / ``snapshots`` 走白名单拼装）。
            parser: 输出解析器（纯函数，解析在线程池里跑）。
            config_repo: 配置仓储（取 ``repo.url`` 与后端凭据）。
        """
        self._restic = restic
        self._parser = parser
        self._config_repo = config_repo

    def diff(
        self, snapshot_id: str, target: str, *, roots: list[str] | None = None
    ) -> CompareScan:
        """开启一次覆盖比对扫描（设计 §3.3 签名）。

        Args:
            snapshot_id: 快照 id。
            target: 原位恢复目标（容器形态）。
            roots: 快照备份根；缺省时内部再查一次 ``snapshots --json``。

        Returns:
            :class:`CompareScan` 句柄（``async for`` 迭代）。
        """
        return CompareScan(self, snapshot_id, target, roots)

    async def snapshot_roots(self, snapshot_id: str) -> list[str]:
        """取该快照的备份根路径（容器形态），供双重锁的「快照 paths 交集」收紧。

        Args:
            snapshot_id: 快照 id（restic 允许前缀，故用 startswith 匹配）。

        Returns:
            快照 ``paths`` 清单（容器形态）；无匹配或无 paths 时为空列表。

        Raises:
            ResticFailedError: 仓库未配置或 ``snapshots --json`` 失败（原因明示）。
        """
        probe = await self._probe("snapshots", WhitelistArgs(json_output=True), SNAPSHOT_LIST_TIMEOUT_SECONDS)
        records = await asyncio.to_thread(self._parser.parse_snapshot_list, "\n".join(probe.lines))
        for record in records:
            if not (record.snapshot_id or "").startswith(snapshot_id):
                continue
            return [str(item) for item in record.paths]
        return []

    async def scan(
        self, snapshot_id: str, target: str, roots: list[str] | None = None
    ) -> tuple[list[OverwriteItem], bool]:
        """执行一次完整比对（供 :class:`CompareScan` 与单测直接调用）。

        Args:
            snapshot_id: 快照 id。
            target: 原位恢复目标（容器形态）。
            roots: 快照备份根；None 时内部查询。

        Returns:
            ``(覆盖项清单, restic 输出是否被截断)``；目标与快照 paths 无交集时
            返回空清单（越界由路由层双重锁拦在前面，此处只做兜底）。
        """
        resolved = roots if roots is not None else await self.snapshot_roots(snapshot_id)
        root = match_snapshot_root(resolved, target)
        if root is None:
            logger.warning(
                "覆盖比对跳过：目标 %s 不在快照路径 %s 范围内", target, resolved
            )
            return [], False
        # 取数范围直接收紧到 target 子树（与执行层 --include=<target> 同范围），
        # 不为整棵快照根做 ls；root 只用于定位「目标属于哪条快照根」与上面的兜底。
        records, truncated = await self._list_files(snapshot_id, target)
        return await self._compare(records, target), truncated

    # ---------------------------------------------------------------- 内部实现

    async def _list_files(self, snapshot_id: str, subtree: str) -> tuple[list[FileRecord], bool]:
        """``restic ls --json <id> <子树>`` → 文件条目清单（子树即原位目标）。"""
        args = WhitelistArgs(
            snapshot_id=snapshot_id,
            # 子树走 extra_positionals：它是**快照内**路径，不能过 PathMapper 的容器前缀校验
            extra_positionals=(subtree,),
            json_output=True,
        )
        probe = await self._probe("ls", args, RESTORE_PREVIEW_TIMEOUT_SECONDS)
        records = await asyncio.to_thread(self._parser.parse_file_list, "\n".join(probe.lines))
        return records, probe.truncated

    async def _probe(self, verb: str, args: WhitelistArgs, timeout_seconds: float) -> ProbeResult:
        """执行一次只读探测（仓库未配置 / restic 失败 → 500 且原因明示）。"""
        repo_url = (await self._config_repo.get_value("repo.url")) or ""
        if not repo_url:
            raise ResticFailedError("仓库地址未配置（请先在仓库配置页填写 repo.url）")
        argv = self._restic.build(verb, args=args)
        env = self._restic.build_env(
            repo_url=repo_url, env_extras=await load_credentials(self._config_repo)
        )
        probe = await self._restic.run_json(argv, env=env, timeout_seconds=timeout_seconds)
        if probe.exit_code != 0:
            reason = failure_reason(probe.exit_code, probe.lines, timeout_seconds)
            logger.warning("覆盖比对探测失败（verb=%s）：%s", verb, reason)
            raise ResticFailedError(reason, exit_code=probe.exit_code)
        return probe

    async def _compare(self, records: list[FileRecord], target: str) -> list[OverwriteItem]:
        """逐条比对：快照 mtime/size × 磁盘现状（os.stat 批量在线程池里跑）。

        落点直接取快照内绝对路径（执行层 ``--target /`` 的落点 = ``record.path`` 本身），
        清单范围限定在 ``target`` 子树内——与执行层的写入范围逐条对齐。
        """
        candidates: list[FileRecord] = []
        for record in records:
            # 只比对文件：目录的 size/mtime 语义与文件不同（目录 size 是目录项长度），
            # 纳入比对会把整棵目录树标成"待覆盖"（噪声，非真实覆盖语义）。
            if (record.type or "file") != "file" or not record.path:
                continue
            # target 之外的文件不进清单：执行层 --include=<target> 不会写它们
            if not in_snapshot_paths([target], record.path):
                continue
            candidates.append(record)

        stats = await asyncio.to_thread(_stat_many, [record.path for record in candidates])
        items: list[OverwriteItem] = []
        for record, stat in zip(candidates, stats, strict=True):
            if stat is None:
                continue  # 目标不存在 = 新建，不算覆盖
            disk_mtime, disk_size = stat
            snapshot_moment = parse_iso(record.mtime)
            snapshot_epoch = snapshot_moment.timestamp() if snapshot_moment else None
            if not _differs(snapshot_epoch, disk_mtime, record.size, disk_size):
                continue
            items.append(
                OverwriteItem(
                    path=record.path,
                    snapshot_mtime=utc_iso(snapshot_moment) if snapshot_moment else None,
                    disk_mtime=utc_iso(datetime.fromtimestamp(disk_mtime, UTC)),
                    snapshot_size=record.size,
                    disk_size=disk_size,
                )
            )
        return items


def in_snapshot_paths(roots: list[str], path: str) -> bool:
    """判断路径是否落在任一快照备份根内（根本身或其子目录）。

    Args:
        roots: 快照 ``paths``（容器形态）。
        path: 待判定路径（容器形态）。

    Returns:
        命中返回 True。
    """
    for root in roots:
        prefix = root.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def match_snapshot_root(roots: list[str], target: str) -> str | None:
    """取与目标命中的快照备份根（最长前缀优先，避免嵌套根取到短的）。

    Args:
        roots: 快照 ``paths``（容器形态）。
        target: 原位恢复目标（容器形态）。

    Returns:
        命中的根；无命中返回 None。
    """
    matched = [root for root in roots if in_snapshot_paths([root], target)]
    return max(matched, key=len) if matched else None




def _stat_many(paths: list[str]) -> list[tuple[float, int] | None]:
    """批量取磁盘现状（在 :func:`asyncio.to_thread` 里跑，C4）。

    Args:
        paths: 容器形态的磁盘路径清单。

    Returns:
        与输入等长的 ``(mtime_epoch, size)`` 清单；stat 失败（不存在/无权限）置 None。
    """
    stats: list[tuple[float, int] | None] = []
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            stats.append(None)
        else:
            stats.append((stat.st_mtime, stat.st_size))
    return stats


def _differs(
    snapshot_epoch: float | None, disk_epoch: float, snapshot_size: int | None, disk_size: int
) -> bool:
    """判定两侧是否不同（mtime 或 size 任一不同即为覆盖项）。"""
    if snapshot_size is not None and snapshot_size != disk_size:
        return True
    if snapshot_epoch is None:
        return True  # 快照 mtime 缺失/不可解析 → 保守记为覆盖
    return abs(snapshot_epoch - disk_epoch) > COMPARE_MTIME_TOLERANCE_SECONDS
