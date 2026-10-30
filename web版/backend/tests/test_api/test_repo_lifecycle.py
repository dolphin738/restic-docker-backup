"""候选 1 批次 ①：``RepoLifecycleService.hard_delete`` 的顺序契约与可重试性。

证明的不是「事务能用」，而是**顺序真的让失败可收敛**——这是候选 1 的核心主张：
崩在删行之前的任何一步，行都还在，整请求重发即收敛。

三条断言线（决策件 §9）：
1. 双并发：恰好 1 次成功，墓碑只一条，行只删一次；
2. 故障注入：在第 N 步抛 OSError → 行仍在 + 有 failed 审计 → 去掉 mock 后重发收敛；
3. 顺序契约：日志路径先取（行还在时）、删行在事务内、日志文件在删行之后。
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import finish_bootstrap

from app.constants import (
    AUDIT_REPO_DELETE,
    AUDIT_REPO_LIFECYCLE_BEGIN,
    AUDIT_REPO_LIFECYCLE_FAILED,
)
from app.errors import RepoNotFoundError


async def _mk_repo(ns, repo_id: str = "rl1") -> None:
    """建一个可删的仓库（非 default、无任务、无活跃作业）。"""
    await ns.app.state.repos_repo.create(repo_id, "待删仓库")


async def _audit_total(ns, action: str) -> int:
    """某动作的审计条数。"""
    _, total = await ns.app.state.audit.query(action=action)
    return total


async def _tombstones(ns) -> list[dict]:
    """墓碑清单。"""
    return await ns.app.state.repos_repo.list_tombstones()


# ---------------------------------------------------------------- 双并发

async def test_concurrent_hard_delete_only_one_succeeds(app_client):
    """双并发删除：恰好 1 次成功，另 1 次 404；行只删一次、墓碑只一条。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_repo(ns, "rl1")
    svc = ns.app.state.repo_lifecycle

    results = await asyncio.gather(
        svc.hard_delete("rl1"),
        svc.hard_delete("rl1"),
        return_exceptions=True,
    )

    ok = [r for r in results if not isinstance(r, BaseException)]
    errs = [r for r in results if isinstance(r, BaseException)]
    assert len(ok) == 1, f"应恰好 1 次成功：{results}"
    assert len(errs) == 1
    assert isinstance(errs[0], RepoNotFoundError)

    # 行只删一次：墓碑只有一条（INSERT OR REPLACE 也不会叠出第二条）
    tombs = [t for t in await _tombstones(ns) if t["repo_id"] == "rl1"]
    assert len(tombs) == 1
    assert await ns.app.state.repos_repo.get("rl1") is None
    assert await _audit_total(ns, AUDIT_REPO_DELETE) == 1


# ---------------------------------------------------------------- 故障注入

async def test_failure_before_row_delete_keeps_row_and_is_retryable(app_client, monkeypatch):
    """崩在删行之前（凭据清理抛错）→ 行仍在 + 有 failed 审计；去掉 mock 后重发收敛。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_repo(ns, "rl2")
    svc = ns.app.state.repo_lifecycle

    # 注入：凭据清理阶段抛 OSError（删行之前）
    async def _boom_creds(repo_id: str) -> int:
        raise OSError("模拟凭据清理失败")

    monkeypatch.setattr(svc, "_clear_credentials", _boom_creds)
    with pytest.raises(OSError):
        await svc.hard_delete("rl2")

    # 行仍在 → 这就是「可重试」的全部前提
    assert await ns.app.state.repos_repo.get("rl2") is not None
    # 意图与失败都留了痕
    assert await _audit_total(ns, AUDIT_REPO_LIFECYCLE_BEGIN) == 1
    assert await _audit_total(ns, AUDIT_REPO_LIFECYCLE_FAILED) == 1
    # 没有墓碑、没有收尾审计（删行根本没发生）
    assert await _audit_total(ns, AUDIT_REPO_DELETE) == 0
    assert [t for t in await _tombstones(ns) if t["repo_id"] == "rl2"] == []

    # 去掉 mock：重发一次即收敛
    monkeypatch.undo()
    await svc.hard_delete("rl2")
    assert await ns.app.state.repos_repo.get("rl2") is None
    assert await _audit_total(ns, AUDIT_REPO_DELETE) == 1


async def test_disk_purge_failure_leaves_row_intact(app_client, monkeypatch):
    """purge 的磁盘清理失败 → 422 零损失：行与数据都还在，可重试（决策件 §10 风险 1）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_repo(ns, "rl3")
    await ns.app.state.repos_repo.update("rl3", url="/tmp/rl3-repo")
    svc = ns.app.state.repo_lifecycle

    import app.services.repo_lifecycle as lc

    async def _boom_disk(repo) -> bool:
        raise lc.ValidationError("模拟清空失败（EBUSY）")

    monkeypatch.setattr(svc, "_purge_disk", _boom_disk)
    with pytest.raises(lc.ValidationError):
        await svc.hard_delete("rl3", purge_disk=True)

    # 零损失：行仍在，无墓碑，无收尾审计
    assert await ns.app.state.repos_repo.get("rl3") is not None
    assert [t for t in await _tombstones(ns) if t["repo_id"] == "rl3"] == []
    assert await _audit_total(ns, AUDIT_REPO_LIFECYCLE_FAILED) == 1


# ---------------------------------------------------------------- 顺序契约

async def test_log_paths_captured_before_row_deletion(app_client, monkeypatch):
    """日志路径在删行**之前**取好：删行后 JobRepo 已查不到（旧实现的结构性缺陷）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _mk_repo(ns, "rl4")
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    log = settings.jobs_dir / "rl4-job.log"
    log.write_text("pre-recorded\n", encoding="utf-8")
    await ns.app.state.job_repo.create(
        job_id="rl4job", job_type="init", trigger="manual",
        log_path=str(log), repo_id="rl4", status="success",
    )

    order: list[str] = []
    svc = ns.app.state.repo_lifecycle

    orig_paths = svc._jobs.terminal_log_paths
    orig_drop = ns.app.state.repos_repo.drop_row

    async def _spy_paths(repo_id: str):
        order.append("log_paths")
        return await orig_paths(repo_id)

    async def _spy_drop(repo_id: str, *, tx=None) -> None:
        order.append("drop_row")
        await orig_drop(repo_id, tx=tx)

    monkeypatch.setattr(svc._jobs, "terminal_log_paths", _spy_paths)
    monkeypatch.setattr(ns.app.state.repos_repo, "drop_row", _spy_drop)

    report = await svc.hard_delete("rl4")
    assert order == ["log_paths", "drop_row"]
    assert report.removed_logs == 1
    assert not log.exists()  # 删行之后才删文件
