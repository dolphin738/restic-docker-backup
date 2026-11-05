"""P7 / 方案 §5.5：validate_repo_url 对「conf 已加密但口令不可达」收窄为 422。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.errors import ValidationError
from app.routers.repos_shared import validate_repo_url


class _FakeProbe:
    """list_remotes 替身：返回预录结果（不 spawn 真子进程）。"""

    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result
        self.calls = 0

    async def list_remotes(self) -> SimpleNamespace:
        self.calls += 1
        return self._result


def _remotes(*, available: bool, locked: bool | None = False, names: tuple[str, ...] = ()) -> SimpleNamespace:
    """locked=None 表示 probe 结果没有 conf_locked 字段（P3 落地前的兼容形态）。"""
    result = SimpleNamespace(
        available=available,
        conf_present=bool(names),
        conf_path="/etc/rclone/rclone.conf",
        remotes=names,
        reason=None if available else "探测失败",
    )
    if locked is not None:
        result.conf_locked = locked
    return result


async def test_conf_locked_probe_rejects_with_422():
    """配置错误不是临时故障：锁死时拒绝 rclone URL，文案指向恢复路径。"""
    probe = _FakeProbe(_remotes(available=False, locked=True))
    with pytest.raises(ValidationError) as excinfo:
        await validate_repo_url("rclone:myremote:bucket/restic", probe)
    assert excinfo.value.status == 422
    assert "口令" in excinfo.value.message
    assert "明文 conf 备份" in excinfo.value.message
    assert probe.calls == 1


async def test_infrastructure_unavailable_still_passes():
    """缺二进制 / 超时 / 事件循环不支持 / conf 缺失：继续放行，由 E29 终判。"""
    for locked in (False, None):
        probe = _FakeProbe(_remotes(available=False, locked=locked))
        await validate_repo_url("rclone:myremote:bucket/restic", probe)
        assert probe.calls == 1


async def test_available_remote_in_list_still_passes():
    """remote 在册的正常路径不受影响。"""
    probe = _FakeProbe(_remotes(available=True, names=("myremote",)))
    await validate_repo_url("rclone:myremote:bucket/restic", probe)
    assert probe.calls == 1
