"""Windows 子进程可用性护栏（2026-10-19 真机实证回归）。

背景：uvicorn ``--reload``（或 ``--workers>1``）经 ``use_subprocess`` 分支把 Windows
事件循环强制选成 SelectorEventLoop——它不支持 ``asyncio.create_subprocess_exec``
（NotImplementedError，asyncio/base_events.py:539）。用裸 ``--reload`` 启动后端时，
restic/rclone 全部子进程操作 500（用户报告：「仓库配置页选 rclone 提示
rclone 探测失败：服务器内部错误」）。正确启动形如
``uvicorn app.main:app --reload --loop none``（dev-scripts/dev.ps1 已内置）。

防护三层（app 侧）：
1. :meth:`RcloneProbe.list_remotes`——NotImplementedError 降级为
   available=False + 可操作原因（指认 ``--loop none``），与缺二进制/超时同口径；
2. :meth:`ResticCmd.spawn`——NotImplementedError 转 ResticFailedError
   （原因明示，禁止静默），覆盖备份/测试/快照等全部 restic 操作；
3. ``main.lifespan`` 启动自检——win32 且非 ProactorEventLoop 时 ERROR 日志明示
   修法（win32-only 日志路径，CI 无 Windows 不跑，不在本文件固化）。

本文件固化 1、2 两条行为回归。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.constants import SUBPROCESS_LOOP_HINT
from app.errors import ResticFailedError
from app.services.rclone import RcloneProbe
from app.services.restic import ResticCmd
from app.settings import Settings


class _NotImplementedProbe(RcloneProbe):
    """_exec 替身：模拟 SelectorEventLoop 下 create_subprocess_exec 的失败形态。"""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.called = False

    async def _exec(self, argv: list[str]) -> tuple[int, str]:
        self.called = True
        raise NotImplementedError


async def test_rclone_probe_not_implemented_degrades(tmp_path):
    """SelectorEventLoop（裸 --reload）→ 探测降级 available=False + 指认 --loop none。"""
    probe = _NotImplementedProbe(Settings(data_dir=str(tmp_path / "data")))
    result = await probe.list_remotes()
    assert probe.called is True
    assert result.available is False
    assert result.remotes == ()
    assert result.reason == SUBPROCESS_LOOP_HINT
    assert "--loop none" in (result.reason or "")


async def test_restic_spawn_not_implemented_raises_actionable(tmp_path, monkeypatch):
    """SelectorEventLoop（裸 --reload）→ spawn 抛 ResticFailedError 且原因指认 --loop none。"""

    async def _raise(*args: object, **kwargs: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)
    cmd = ResticCmd(Settings(data_dir=str(tmp_path / "data")))
    log_path: Path = tmp_path / "jobs" / "job.log"
    with pytest.raises(ResticFailedError) as exc_info:
        await cmd.spawn(["restic", "snapshots"], log_path)
    # 日志文件仍先 touch（spawn 语义不变），失败发生在 exec 一步
    assert log_path.is_file()
    assert "--loop none" in str(exc_info.value)
    assert exc_info.value.data["reason"] == SUBPROCESS_LOOP_HINT
