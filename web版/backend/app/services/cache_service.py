"""restic 缓存维护（R-021 E25 / 设计 §3.1）：占用统计与清空。

成本兜底（Q14 基线）：
- du 在 ``asyncio.to_thread`` 执行（不阻塞事件循环，AGENTS「CPU 密集与阻塞 IO 放 to_thread」）；
- 超时 30s（``CACHE_USAGE_TIMEOUT_SECONDS``）：超时回**部分统计 + truncated=True**，
  不 5xx（du 线程无法强杀，靠增量 sink 拿到当时已统计的值，线程自行结束）；
- 30s 进程内结果缓存（``CACHE_STATUS_CACHE_SECONDS``，援 E30 ``REPO_STATUS_CACHE_SECONDS``
  先例）：仪表盘轮询不反复扫盘。

红线落点：S1——读写只触碰 ``settings.cache_dir``（路径字面量唯一出处 settings.py），
clear 只删其**一级子项**、不跟随符号链接、不递归出根；绝不接受用户输入路径。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from app.constants import CACHE_STATUS_CACHE_SECONDS, CACHE_USAGE_TIMEOUT_SECONDS
from app.settings import Settings, get_settings
from app.utils.time import utc_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheUsage:
    """E25 占用查询响应（设计 §2.3 字段全集）。"""

    path: str
    size_bytes: int
    items_count: int
    measured_at: str
    truncated: bool


def _measure_sync(root: Path, sink: dict[str, int]) -> None:
    """同步 du：迭代栈遍历 root 统计文件字节与条目数，**增量**写 sink。

    增量写的意义：du 线程无法被 asyncio 超时强杀，主协程超时后从 sink 取
    「当时已统计」的部分值生成 truncated 结果（线程自行走完后写 sink["done"]=1，
    其结果被丢弃，不产生共享可变状态竞争——消费只发生在超时分支）。
    """
    size = 0
    items = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            items += 1
            try:
                if entry.is_symlink():
                    pass  # 符号链接只计数，不跟随出根、不计体积（S1 纵深）
                elif entry.is_dir():
                    stack.append(entry)
                else:
                    size += entry.stat().st_size
            except OSError:
                continue
        sink["size_bytes"] = size
        sink["items_count"] = items
    sink["done"] = 1


class CacheService:
    """restic 缓存占用统计与清空（E25；装配于 main.py，路由层只做护栏与审计）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache_seconds: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        """构造缓存服务。

        Args:
            settings: 运行期配置（cache_dir 白名单来源）；缺省取进程单例。
            cache_seconds: 结果缓存 TTL（秒）；缺省取 ``CACHE_STATUS_CACHE_SECONDS``。
            timeout_seconds: du 超时（秒）；缺省取 ``CACHE_USAGE_TIMEOUT_SECONDS``。
                可注入以便测试确定性（AGENTS「新增阈值按可注入模式，不得裸写」）。
        """
        self._settings = settings or get_settings()
        self._cache_seconds = CACHE_STATUS_CACHE_SECONDS if cache_seconds is None else cache_seconds
        self._timeout_seconds = CACHE_USAGE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        self._cached: CacheUsage | None = None
        self._cached_at = 0.0  # time.monotonic()

    async def usage(self) -> CacheUsage:
        """返回缓存占用（命中 TTL 缓存直接返回；否则 to_thread 统计）。"""
        cached = self._cached
        if cached is not None and time.monotonic() - self._cached_at < self._cache_seconds:
            return cached

        root = Path(self._settings.cache_dir)
        sink: dict[str, int] = {"size_bytes": 0, "items_count": 0, "done": 0}
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await asyncio.to_thread(_measure_sync, root, sink)
        except TimeoutError:
            logger.warning("缓存占用统计超时（>%ss），返回部分统计（truncated）", self._timeout_seconds)
        usage = CacheUsage(
            path=self._settings.cache_dir,
            size_bytes=sink["size_bytes"],
            items_count=sink["items_count"],
            measured_at=utc_iso(),
            truncated=sink["done"] != 1,
        )
        self._cached = usage
        self._cached_at = time.monotonic()
        return usage

    async def clear(self) -> None:
        """清空缓存目录**内容**（保留目录本身）；完成后立即失效占用缓存。"""
        await asyncio.to_thread(_clear_sync, Path(self._settings.cache_dir))
        self._cached = None


def _clear_sync(root: Path) -> None:
    """同步清空 root 的一级子项（S1：绝不越出 root，不跟随符号链接）。"""
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return
    for entry in root.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError as exc:
            logger.warning("缓存清理单项失败（继续其余项）：%s（%s）", entry, exc)
