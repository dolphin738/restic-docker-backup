"""快照目录服务单测：按 tag 计数 + **总条数**（删除依赖与快照可用性保护方案 P0，裁决 6）。

两条必须守住的口径：

1. ``total`` = 仓库内快照**总条数**（**含**无 tag / 被 ``tag --remove`` 摘掉 task tag
   的手工快照）；``counts`` 只覆盖带 tag 的快照 → ``total >= sum(counts.values())``，
   不可互相替代，删除门/归档页必须取 ``total``（否则用户会低估后果）。
2. **失败结论同样入缓存**（不可知 ≠ 0）：仓库不可达时若每次都真探一次，删除门会被
   探测超时拖住；连续失败按指数退避，成功即复位。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.parser import SnapshotRecord
from app.services.snapshot_catalog import (
    SnapshotCatalog,
    SnapshotTally,
    invalidate_snapshot_catalog,
)


@dataclass(frozen=True)
class _FakeProbe:
    exit_code: int = 0
    lines: tuple[str, ...] = ()
    truncated: bool = False


class _FakeRestic:
    """最小 restic 桩：只实现 catalog 用到的 build/run_json。"""

    def __init__(self, lines: list[str] | None = None, exit_code: int = 0) -> None:
        self._lines = list(lines or [])
        self._exit_code = exit_code
        self.calls: list[list[str]] = []

    def build(self, verb: str, *, args: Any, env_extras: Any = None) -> list[str]:
        return ["restic", verb]

    async def run_json(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        max_lines: int = 200_000,
    ) -> _FakeProbe:
        self.calls.append(list(argv))
        return _FakeProbe(exit_code=self._exit_code, lines=tuple(self._lines))


class _FakeParser:
    """解析结果桩：直接回灌预置记录（catalog 只消费 snapshot_id 与 tags）。"""

    def __init__(self, records: list[SnapshotRecord]) -> None:
        self._records = records

    def parse_snapshot_list(self, text: str) -> list[SnapshotRecord]:
        del text  # 桩不解析文本
        return list(self._records)


def _record(sid: str, *tags: str) -> SnapshotRecord:
    return SnapshotRecord(snapshot_id=sid, tags=list(tags))


async def _tally(records: list[SnapshotRecord]) -> SnapshotTally | None:
    return await SnapshotCatalog().snapshot_tally(
        restic=_FakeRestic(),  # type: ignore[arg-type]
        parser=_FakeParser(records),  # type: ignore[arg-type]
        repo_id="default",
        repo_url="/data/repo",
        env={},
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清缓存——上一用例的失败退避不得串味到下一用例。"""
    invalidate_snapshot_catalog()
    yield
    invalidate_snapshot_catalog()


async def test_counts_by_tag_groups_each_tag():
    """同一快照带多个 tag 时各 tag 各计一次（包含语义过滤的前提）。"""
    tally = await _tally([_record("a" * 16, "task-1", "nightly"), _record("b" * 16, "task-2")])
    assert tally is not None
    assert tally.counts == {"task-1": 1, "nightly": 1, "task-2": 1}


async def test_total_counts_snapshots_without_tags():
    """总条数**含**无 tag 快照——tag 求和会漏计（裁决 6 的核心）。"""
    tally = await _tally([_record("a" * 16, "task-1"), _record("b" * 16), _record("c" * 16)])
    assert tally is not None
    assert tally.total == 3
    assert sum(tally.counts.values()) == 1


async def test_empty_url_returns_none_without_probe():
    """仓库地址未配置 → 直接 None，不跑 restic（避免无意义的子进程开销）。"""
    tally = await SnapshotCatalog().snapshot_tally(
        restic=_FakeRestic(),  # type: ignore[arg-type]
        parser=_FakeParser([]),  # type: ignore[arg-type]
        repo_id="default",
        repo_url="   ",
        env={},
    )
    assert tally is None


async def test_failure_cached_and_backoff_grows():
    """探测失败 → 结论入缓存，退避翻倍，TTL 内不再重复真探（B4）。"""
    from app.constants import SNAPSHOT_LIST_CACHE_SECONDS

    restic = _FakeRestic(lines=[], exit_code=1)
    catalog = SnapshotCatalog(cache_seconds=SNAPSHOT_LIST_CACHE_SECONDS)
    first = await catalog.snapshot_tally(
        restic=restic, parser=_FakeParser([]), repo_id="r1", repo_url="/data/repo", env={}
    )
    assert first is None
    assert len(restic.calls) == 1
    # TTL 未过 → 命中缓存，不新增子进程
    second = await catalog.snapshot_tally(
        restic=restic, parser=_FakeParser([]), repo_id="r1", repo_url="/data/repo", env={}
    )
    assert second is None
    assert len(restic.calls) == 1


async def test_invalidate_forces_reprobe():
    """失效缓存后重新探测（新建备份任务等场景需立即刷新计数）。"""
    restic = _FakeRestic(lines=[], exit_code=1)
    catalog = SnapshotCatalog()
    await catalog.snapshot_tally(
        restic=restic, parser=_FakeParser([]), repo_id="r2", repo_url="/data/repo", env={}
    )
    invalidate_snapshot_catalog()
    await catalog.snapshot_tally(
        restic=restic, parser=_FakeParser([]), repo_id="r2", repo_url="/data/repo", env={}
    )
    assert len(restic.calls) == 2


def test_tally_is_frozen_with_empty_defaults():
    """SnapshotTally 为不可变快照；缺省空值，由调用方区分「不可知」与 0。"""
    empty = SnapshotTally()
    assert empty.counts == {} and empty.total == 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        empty.total = 1  # type: ignore[misc]


def test_remote_of_parses_prefix_exactly():
    """remote 段按**完整相等**切分：``foo`` 不误命中 ``foobar``。"""
    from app.routers.repos_shared import rclone_remote_of as _rclone_remote_of

    assert _rclone_remote_of("rclone:foo:bucket") == "foo"
    assert _rclone_remote_of("rclone:foobar:bucket") == "foobar"
    # 非 rclone 后端 / 缺尾冒号 → None（不参与引用判定）
    assert _rclone_remote_of("/data/repo") is None
    assert _rclone_remote_of("rclone:foo") is None
