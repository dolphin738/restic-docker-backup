"""E5 密码文件 per-repo 布局（架构 §13 E5，owner 2026-10-16 裁决 A）。

锁死三件事：① 布局为 ``<passfile_root>/<repo_id>/password``；② ``RESTIC_PASSWORD_FILE``
显式设置时覆盖布局（运维逃生舱）；③ 子进程环境变量 ``RESTIC_PASSWORD_FILE`` 由该布局派生
（``ResticCmd.build_env`` 不再读已被移除的 ``settings.passfile`` 单文件字段）。
"""

from __future__ import annotations

from pathlib import Path

from app.services.env_sync import passfile_path, passfile_status
from app.services.restic import ResticCmd
from app.settings import Settings


def test_layout_is_per_repo(monkeypatch) -> None:
    """默认按 repo_id 派生；当前仅 default 一行。"""
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    settings = Settings(passfile_root="/data/repos")
    assert settings.passfile_path() == Path("/data/repos/default/password")
    assert settings.passfile_path("repo-2") == Path("/data/repos/repo-2/password")


def test_env_override_wins(monkeypatch, tmp_path: Path) -> None:
    """RESTIC_PASSWORD_FILE 显式设置时优先于 per-repo 布局。"""
    override = tmp_path / "custom-pass"
    override.write_text("x", encoding="utf-8")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(override))
    settings = Settings(passfile_root="/data/repos")
    assert settings.passfile_path("repo-2") == override
    assert passfile_status(settings) == "configured"


def test_status_missing_when_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    settings = Settings(passfile_root=str(tmp_path / "nope"))
    assert passfile_status(settings) == "missing"
    assert str(passfile_path(settings)) == str(tmp_path / "nope" / "default" / "password")


def test_build_env_uses_layout(monkeypatch, tmp_path: Path) -> None:
    """子进程 env 的密码文件路径源自 E5 布局（唯一实现点）。"""
    monkeypatch.delenv("RESTIC_PASSWORD_FILE", raising=False)
    root = tmp_path / "repos"
    settings = Settings(passfile_root=str(root))
    cmd = ResticCmd(settings)
    env = cmd.build_env(repo_url="s3:s3.amazonaws.com/bk")
    assert env["RESTIC_PASSWORD_FILE"] == str(root / "default" / "password")
