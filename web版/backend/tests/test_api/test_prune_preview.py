"""API 集成：R-016 prune 预览（``restic prune --dry-run`` 只读探测）。

restic 探测以 ``run_json`` 替身覆盖（预录真机采集的 0.18.0 dry-run 输出），
路由/解析/白名单全走真实代码路径。断言点：

- **单位口径**：restic 0.17+ 输出 ``to delete: N blobs``（0.15-0.16 为 packs），
  解析必须取到块数而非恒 0（曾因只认 packs 导致预览显示「删除 0 个 pack」却
  给出非零释放量的自相矛盾文案）；
- **只读**：argv 必含 ``--dry-run``、必不含 ``--prune``（预览绝不落删除）；
- **探测失败不静默**：restic 非 0 退出 → 500 RESTIC_FAILED；
- **clean 语义**：全零输出 → clean=true；有未引用块 → clean=false。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

from app.services.restic import ProbeResult

# restic 0.18.0 prune --dry-run 真机采集（dev 仓库，2026-10-25）：
# 被删快照与保留快照共享 tree、源为空目录 → restic 结论确实是「没有可回收数据」。
CLEAN_LINES = [
    "loading indexes...",
    "loading all snapshots...",
    "finding data that is still in use for 3 snapshots",
    "searching used packs...",
    "collecting packs for deletion and repacking",
    "Would have made the following changes:",
    "to repack:             0 blobs / 0 B",
    "this removes:          0 blobs / 0 B",
    "to delete:             0 blobs / 0 B",
    "total prune:           0 blobs / 0 B",
    "remaining:             3 blobs / 552 B",
    "unused size after prune: 0 B (0.00% of remaining size)",
]

# 同格式的有未引用块场景（to delete / total prune 非零）。
DIRTY_LINES = [
    "loading indexes...",
    "finding data that is still in use for 3 snapshots",
    "searching used packs...",
    "collecting packs for deletion and repacking",
    "Would have made the following changes:",
    "to repack:             2 blobs / 5.120 KiB",
    "this removes:          3 blobs / 1.234 KiB",
    "to delete:             3 blobs / 1.234 KiB",
    "total prune:           3 blobs / 1.234 KiB",
    "remaining:             9 blobs / 552 B",
    "unused size after prune: 1.234 KiB (12.34% of remaining size)",
]


def _patch_run_json(ns, *, lines: list[str], exit_code: int = 0) -> list[list[str]]:
    """替身 run_json：记录 argv 供只读断言。返回 argv 收集器。"""
    argv_log: list[list[str]] = []

    async def _run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return ProbeResult(exit_code=exit_code, lines=list(lines), truncated=False)

    ns.app.state.restic.run_json = _run_json
    return argv_log


async def _setup_repo(ns) -> None:
    """R-016 前置：配置仓库地址（R-023 后 repos 表事实源）。"""
    await ns.app.state.repos_repo.update("default", url="/repo")


async def test_prune_preview_parses_blob_count(app_client):
    """restic 0.18 的 ``to delete: N blobs`` 必须解析出块数（不能恒 0）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=DIRTY_LINES)

    resp = await ns.client.post("/api/prune/preview", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["would_remove_packs"] == 3
    assert body["freed_bytes"] == 1263
    assert body["freed_text"] == "1.234 KiB"
    assert body["clean"] is False
    assert body["lines"] == DIRTY_LINES
    # 只读铁律：argv 必含 --dry-run、必不含 --prune；仓库地址走 RESTIC_REPOSITORY 环境变量
    argv = argv_log[0]
    assert "--dry-run" in argv
    assert "--prune" not in argv


async def test_prune_preview_clean_repo(app_client):
    """全零输出 → clean=true（真机采集：共享 tree + 空源场景 restic 结论为干净）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns, lines=CLEAN_LINES)

    resp = await ns.client.post("/api/prune/preview", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["would_remove_packs"] == 0
    assert body["freed_bytes"] == 0
    assert body["freed_text"] == "0 B"
    assert body["clean"] is True


async def test_prune_preview_restic_failure_maps_to_500(app_client):
    """探测失败不静默：restic 非 0 退出 → 500 RESTIC_FAILED。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns, lines=["unable to create lock"], exit_code=1)

    resp = await ns.client.post("/api/prune/preview", headers=ORIGIN)
    assert resp.status_code == 500
    assert resp.json()["code"] == "RESTIC_FAILED"


async def test_prune_preview_writes_readonly_audit(app_client):
    """预览入审计 ``prune.preview``（只读留痕，非危险清单项）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns, lines=DIRTY_LINES)

    resp = await ns.client.post("/api/prune/preview", headers=ORIGIN)
    assert resp.status_code == 200

    rows, _total = await ns.app.state.audit.query(action="prune.preview", limit=10)
    assert len(rows) == 1
    assert rows[0].target == "prune"
