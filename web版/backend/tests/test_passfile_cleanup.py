"""孤儿密码文件回收（retention 第四档，`retention.repo_passfile_days`）。

存在理由：密码文件的删除原先只有两条路——`purge_disk=1`（仅本地仓库）与
归档到期（仅**仍处在归档状态**的仓库）。``?hard=1`` 删除、「释放 ID」、以及上一轮
unlink 失败都会把仓库行删掉、留下 ``<passfile_root>/<repo_id>/password`` 孤儿，
而孤儿的 repo_id 已不在 `list_archived()` 里，旧实现**永远扫不到**。

本规格锁死第四档的四条性质（与既有三档同口径）：
- 无主 + 超龄 → 回收（文件 + 空目录），落 `retention.cleanup` 审计；
- 有主（在用 / 归档 / 墓碑）→ 一个字节都不动；
- 冷却期内（mtime 未超龄）→ 不动，避开「行已建、密码文件刚写」的中间态；
- ``RESTIC_PASSWORD_FILE`` 全局共用密码文件 → **整段跳过**（删它会毁掉全部仓库）。

反向断言（最重要的两条）：
- 有主仓库的密码文件**绝不能**被回收（回收 = 该仓库立刻不可读）；
- 全局覆盖模式下**绝不能**删那个共用文件。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from conftest import ORIGIN, finish_bootstrap

from app.constants import AUDIT_RETENTION_CLEANUP

PASSFILE_DAYS_KEY = "retention.repo_passfile_days"


def _age(path: Path, days: int) -> None:
    """把文件 mtime 改写成 N 天前（模拟「早就删掉的仓库」）。"""
    ts = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))


def _iso(days_ago: int) -> str:
    """N 天前的 ISO 串（与后端 utc_iso 同 `%Y-%m-%dT%H:%M:%SZ` 形态）。"""
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_passfile(settings, repo_id: str, *, age_days: int = 0) -> Path:
    """直接写一个密码文件（**不建仓库行**），即「孤儿」现场。"""
    target = settings.passfile_path(repo_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("pw", encoding="utf-8")
    if age_days:
        _age(target, age_days)
    return target


async def _retention_audit(ns) -> list[dict]:
    rows, _ = await ns.app.state.audit.query(action=AUDIT_RETENTION_CLEANUP)
    return [json.loads(row.detail or "{}") for row in rows]


async def test_reap_orphan_passfile_and_reclaim_empty_dir(app_client):
    """无主 + 超龄 → 密码文件删除、``<passfile_root>/<repo_id>/`` 空目录一并回收，并落审计。"""
    ns = app_client
    await finish_bootstrap(ns)
    target = _write_passfile(ns.app.state.settings, "gone", age_days=40)

    report = await ns.app.state.retention.cleanup_archives()

    assert report.passfiles_reaped == ["gone"]
    assert not target.exists()
    assert not target.parent.exists()
    details = await _retention_audit(ns)
    assert details and details[-1]["passfiles_reaped"] == 1


async def test_keep_when_repo_row_owns_passfile(app_client):
    """有主（仓库行仍在）→ 绝不回收：回收会让该仓库立刻不可读。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("alive", "在用仓库", url="/data/alive")
    target = _write_passfile(ns.app.state.settings, "alive", age_days=999)

    report = await ns.app.state.retention.cleanup_archives()

    assert report.passfiles_reaped == []
    assert target.read_text(encoding="utf-8") == "pw"


async def test_keep_when_tombstone_owns_passfile(app_client):
    """有主（墓碑仍在）→ 不回收：墓碑期就是宽限期，删墓碑后才进入可回收集合。"""
    ns = app_client
    await finish_bootstrap(ns)
    # 墓碑要「年轻」：否则会在同一轮的第二档（墓碑到期）被先删掉，测不到第四档
    await ns.app.state.repos_repo.insert_tombstone(
        repo_id="buried", name="已删仓库", repo_type="local", url_display="/data/buried",
        archived_at=_iso(days_ago=1), snapshot_count=None, data_destroyed=False,
    )
    target = _write_passfile(ns.app.state.settings, "buried", age_days=999)

    report = await ns.app.state.retention.cleanup_archives()
    assert report.passfiles_reaped == []
    assert target.exists()

    # 墓碑删除（归档页「删除记录」）后，下一轮即回收
    await ns.app.state.repos_repo.delete_tombstone("buried")
    report2 = await ns.app.state.retention.cleanup_archives()
    assert report2.passfiles_reaped == ["buried"]
    assert not target.exists()


async def test_hard_delete_orphan_reaped_after_tombstone_removed(app_client):
    """``?hard=1`` 删除留下的孤儿：墓碑期内保留，墓碑清掉后由第四档回收（不再永久堆积）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r9", "彻底删除仓库", url="/data/r9")
    target = ns.app.state.settings.passfile_path("r9")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old-password", encoding="utf-8")

    resp = await ns.client.delete("/api/repos/r9?hard=1", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    _age(target, 40)

    # 墓碑仍在 → 保留（宽限）
    first = await ns.app.state.retention.cleanup_archives()
    assert first.passfiles_reaped == []
    assert target.exists()

    await ns.app.state.repos_repo.delete_tombstone("r9")
    second = await ns.app.state.retention.cleanup_archives()
    assert second.passfiles_reaped == ["r9"]
    assert not target.exists()


async def test_keep_within_cooldown(app_client):
    """冷却期内（mtime 未超龄）不回收：可能是刚建仓、行还没落库的中间态。"""
    ns = app_client
    await finish_bootstrap(ns)
    target = _write_passfile(ns.app.state.settings, "fresh", age_days=1)

    report = await ns.app.state.retention.cleanup_archives()

    assert report.passfiles_reaped == []
    assert target.exists()


async def test_zero_disables_reap(app_client):
    """``retention.repo_passfile_days = 0`` = 不回收（永久保留），与既有三档同语义。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.config_repo.set(PASSFILE_DAYS_KEY, "0")
    target = _write_passfile(ns.app.state.settings, "forever", age_days=999)

    report = await ns.app.state.retention.cleanup_archives()

    assert report.passfiles_reaped == []
    assert target.exists()


async def test_reap_is_idempotent(app_client):
    """幂等：同一现场连跑两轮，第二轮零删除（不报错、不重复记审计）。"""
    ns = app_client
    await finish_bootstrap(ns)
    _write_passfile(ns.app.state.settings, "once", age_days=40)

    first = await ns.app.state.retention.cleanup_archives()
    second = await ns.app.state.retention.cleanup_archives()

    assert first.passfiles_reaped == ["once"]
    assert second.passfiles_reaped == []
    assert len(await _retention_audit(ns)) == 1


async def test_shared_passfile_env_skips_reap(app_client, monkeypatch, tmp_path):
    """``RESTIC_PASSWORD_FILE`` 全局共用 → 整段跳过，那个共用文件**必须**还在。"""
    ns = app_client
    await finish_bootstrap(ns)
    shared = tmp_path / "shared-password"
    shared.write_text("all-repos-password", encoding="utf-8")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(shared))
    # 全局覆盖下 `passfile_path()` 一律返回 shared，孤儿只能手工造在 passfile_root 下
    orphan = Path(ns.app.state.settings.passfile_root) / "orphan" / "password"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("pw", encoding="utf-8")
    _age(orphan, 40)

    report = await ns.app.state.retention.cleanup_archives()

    assert report.passfiles_reaped == []
    assert orphan.exists()
    assert shared.read_text(encoding="utf-8") == "all-repos-password"


async def test_purge_disk_keeps_shared_passfile_when_env_override(app_client, monkeypatch, tmp_path):
    """purge_disk=1 在全局覆盖模式下：数据照清，但**不删**共用密码文件（否则全仓不可读）。"""
    ns = app_client
    await finish_bootstrap(ns)
    shared = tmp_path / "shared-password"
    shared.write_text("all-repos-password", encoding="utf-8")

    data_dir = tmp_path / "repo-p1"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "config").write_text("old", encoding="utf-8")
    await ns.app.state.repos_repo.create("p1", "本地仓库", url=str(data_dir))
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(shared))

    resp = await ns.client.delete("/api/repos/p1?purge_disk=1", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    assert not (data_dir / "config").exists(), "本地数据照常清空"
    assert shared.read_text(encoding="utf-8") == "all-repos-password", "共用密码文件绝不能被删"
