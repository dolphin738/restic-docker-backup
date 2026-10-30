"""候选 1 批次 ③：``rebuild()`` 的顺序契约与失败态语义。

核心主张（审查 🔴 修复提炼成的契约）：**清空必须先于覆盖密码文件**。
顺序反了的话，清空失败（EBUSY/权限）时旧密码已被覆盖且无回滚 → 盘上快照永久
不可读，「自救」变「自毁」。
"""

from __future__ import annotations

import pytest
from conftest import finish_bootstrap

from app.constants import AUDIT_REPO_REBUILD
from app.errors import JobConflictError, ValidationError
from app.services import repo_lifecycle as lc


def _stub_restic(ns) -> None:
    """让 restic init 恒成功（API 测试里没有真实 restic 二进制）。"""

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        class _FakeProc:
            async def lines(self):  # noqa: ANN202
                return
                yield  # pragma: no cover

            async def wait(self) -> int:
                return 0

            def terminate(self) -> None:  # pragma: no cover
                pass

            def kill(self) -> None:  # pragma: no cover
                pass

        return _FakeProc()

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]


async def _seed(ns, tmp_path, repo_id: str = "rb1"):
    """建仓库 + 数据目录 + 旧密码文件。"""
    data_dir = tmp_path / "repo"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config").write_text("old-repo-config", encoding="utf-8")
    await ns.app.state.repos_repo.create(repo_id, "重建仓库")
    await ns.app.state.repos_repo.update(repo_id, url=str(data_dir))
    settings = ns.app.state.settings
    passfile = settings.passfile_path(repo_id)
    passfile.parent.mkdir(parents=True, exist_ok=True)
    passfile.write_text("old-password", encoding="utf-8")
    return data_dir, passfile


async def test_clear_failure_preserves_old_password(app_client, tmp_path, monkeypatch):
    """清空失败 → 零损失：旧密码文件**未被覆盖**，数据原样。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir, passfile = await _seed(ns, tmp_path, "rb1")

    async def _boom(_local_path):  # noqa: ANN001, ANN202
        raise ValidationError("模拟清空失败（EBUSY）")

    monkeypatch.setattr(lc, "_clear_dir_contents", _boom)
    with pytest.raises(ValidationError):
        await ns.app.state.repo_lifecycle.rebuild("rb1", password="new-password")

    # 关键断言：旧密码仍在（顺序正确的直接证据）
    assert passfile.read_text(encoding="utf-8") == "old-password"
    # 数据也还在
    assert (data_dir / "config").exists()


async def test_rebuild_clears_data_then_writes_new_password(app_client, tmp_path, monkeypatch):
    """正常路径：目录被清空、密码被换成新的。"""
    ns = app_client
    await finish_bootstrap(ns)
    _stub_restic(ns)
    data_dir, passfile = await _seed(ns, tmp_path, "rb2")

    calls: list[str] = []
    real_clear = lc._clear_dir_contents  # 先存原函数，否则 patch 后自调用成递归

    async def _clear(local_path):  # noqa: ANN001, ANN202
        calls.append("clear")
        await real_clear(local_path)

    monkeypatch.setattr(lc, "_clear_dir_contents", _clear)
    await ns.app.state.repo_lifecycle.rebuild("rb2", password="brand-new-pw")

    assert calls == ["clear"]
    assert passfile.read_text(encoding="utf-8") == "brand-new-pw"
    assert not (data_dir / "config").exists()  # 旧数据已清
    _, total = await ns.app.state.audit.query(action=AUDIT_REPO_REBUILD)
    assert total == 1


async def test_rebuild_rejected_when_job_running(app_client, tmp_path):
    """有活跃作业 → 409，且**零副作用**（目录与密码都没动）。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir, passfile = await _seed(ns, tmp_path, "rb3")
    await ns.app.state.job_repo.create(
        job_id="rb3job", job_type="backup", trigger="manual",
        log_path=str(tmp_path / "x.log"), repo_id="rb3", status="running",
    )

    with pytest.raises(JobConflictError):
        await ns.app.state.repo_lifecycle.rebuild("rb3", password="pw")

    assert passfile.read_text(encoding="utf-8") == "old-password"
    assert (data_dir / "config").exists()
