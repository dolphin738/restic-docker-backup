"""候选 1 批次 ②：``release()`` 与 ``expire_archived()``。

重点在**收敛性**与**单一实现点**：
- release/expire 与 hard_delete 同形（行最后删），崩在删行之前可重发收敛；
- retention.cleanup_archives 的归档到期分支改走编排器后，行为与旧实现一致
  （清凭据 + 删密码文件 + 转墓碑），不再有两份顺序。
"""

from __future__ import annotations

import pytest
from conftest import finish_bootstrap

from app.constants import AUDIT_REPO_ID_RELEASE


async def _mk_archived(ns, repo_id: str, url: str | None = None) -> None:
    """建一个已归档的仓库。"""
    await ns.app.state.repos_repo.create(repo_id, "归档仓库")
    if url:
        await ns.app.state.repos_repo.update(repo_id, url=url)
    # B1 严格语义：cred.<repo>.* 用该仓库自己的口令文件派生密钥
    path = ns.app.state.settings.passfile_path(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("archived-password", encoding="utf-8")
    await ns.app.state.repos_repo.archive(repo_id)


async def _tombstones(ns, repo_id: str) -> list[dict]:
    """取某仓库的墓碑。"""
    return [t for t in await ns.app.state.repos_repo.list_tombstones() if t["repo_id"] == repo_id]


async def test_release_deletes_row_leaves_tombstone(app_client):
    """释放 ID：行删掉、留墓碑（released_at 置位）、凭据清掉。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_archived(ns, "ra1")
    await ns.app.state.config_repo.set("cred.ra1.ak", "plain-secret")

    svc = ns.app.state.repo_lifecycle
    report = await svc.release("ra1")

    assert report.cleared_credentials == 1
    assert await ns.app.state.repos_repo.get("ra1") is None
    tombs = await _tombstones(ns, "ra1")
    assert len(tombs) == 1
    assert tombs[0]["released_at"] is not None
    assert tombs[0]["data_destroyed"] == 0
    _, total = await ns.app.state.audit.query(action=AUDIT_REPO_ID_RELEASE)
    assert total == 1


async def test_release_captures_logs_before_row_deletion(app_client):
    """同 hard_delete：日志路径在删行之前取好，删行之后文件被清掉。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_archived(ns, "ra2")
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    log = settings.jobs_dir / "ra2-job.log"
    log.write_text("pre-recorded\n", encoding="utf-8")
    await ns.app.state.job_repo.create(
        job_id="ra2job", job_type="init", trigger="manual",
        log_path=str(log), repo_id="ra2", status="success",
    )

    report = await ns.app.state.repo_lifecycle.release("ra2")
    assert report.removed_logs == 1
    assert not log.exists()


async def test_expire_archived_clears_passfile_and_credentials(app_client):
    """归档到期：清凭据 + 删密码文件 + 转墓碑（不置 released_at）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_archived(ns, "ra3")
    settings = ns.app.state.settings
    passfile = settings.passfile_path("ra3")
    passfile.parent.mkdir(parents=True, exist_ok=True)
    passfile.write_text("irrelevant", encoding="utf-8")
    assert passfile.exists()
    await ns.app.state.config_repo.set("cred.ra3.ak", "plain-secret")

    report = await ns.app.state.repo_lifecycle.expire_archived("ra3")

    assert report.cleared_credentials == 1
    assert not passfile.exists()
    assert await ns.app.state.repos_repo.get("ra3") is None
    tombs = await _tombstones(ns, "ra3")
    assert len(tombs) == 1
    assert tombs[0]["released_at"] is None  # 与 release 的差别


async def test_expire_failure_before_row_delete_is_retryable(app_client, monkeypatch):
    """归档到期崩在删行之前 → 行仍在 + 有 failed 审计；重发收敛。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_archived(ns, "ra4")
    svc = ns.app.state.repo_lifecycle

    async def _boom(repo_id: str) -> int:
        raise OSError("模拟凭据清理失败")

    monkeypatch.setattr(svc, "_clear_credentials", _boom)
    with pytest.raises(OSError):
        await svc.expire_archived("ra4")

    assert await ns.app.state.repos_repo.get("ra4") is not None
    _, failed = await ns.app.state.audit.query(action="repo.lifecycle.failed")
    assert failed == 1

    monkeypatch.undo()
    await svc.expire_archived("ra4")
    assert await ns.app.state.repos_repo.get("ra4") is None


async def test_retention_expiry_uses_orchestrator(app_client, monkeypatch):
    """retention 的归档到期确实走编排器（单一实现点），不是自己再排一遍。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_archived(ns, "ra5")
    svc = ns.app.state.repo_lifecycle

    calls: list[str] = []
    orig = svc.expire_archived

    async def _spy(repo_id: str):
        calls.append(repo_id)
        return await orig(repo_id)

    monkeypatch.setattr(svc, "expire_archived", _spy)
    # 把归档时间推到保留期之前，让 cleanup_archives 判定为到期
    from datetime import timedelta

    from app.utils.time import now_utc

    report = await ns.app.state.retention.cleanup_archives(now=now_utc() + timedelta(days=400))
    assert "ra5" in report.repos_expired
    assert "ra5" in calls  # 走了编排器而不是 retention 自己的循环
