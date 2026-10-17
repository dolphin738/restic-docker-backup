"""API 集成：E17 快照列表 ``?tag=`` 服务端过滤（X2 owner 裁决，任务标签联动 R-026）。

restic 探测以 ``run_json`` 替身覆盖（预录 ``snapshots --json`` 输出），路由/过滤/分页
全走真实代码路径。断言点：
- 过滤语义为**包含**（快照 tags 数组含该 tag 即保留，任务 tag 形如 ``task-<id>``）；
- 不传参行为与既有契约完全一致；
- restic argv 不带 ``--tag``（S2 白名单不扩面，Python 侧过滤）。
"""

from __future__ import annotations

import json

from conftest import finish_bootstrap

from app.services.restic import ProbeResult


def _snapshot_line(snapshot_id: str, tags: list[str], time: str) -> str:
    """构造一条 ``snapshots --json`` NDJSON 行（字段形态对齐 fixtures/restic-json）。"""
    return json.dumps(
        {
            "time": time,
            "tree": "0" * 64,
            "paths": ["/sources/etc"],
            "hostname": "nas",
            "username": "root",
            "tags": tags,
            "id": snapshot_id,
            "short_id": snapshot_id[:8],
        }
    )


SNAPSHOT_A = "a" * 64
SNAPSHOT_B = "b" * 64

SNAPSHOTS_LINES = [
    _snapshot_line(SNAPSHOT_A, ["task-1"], "2026-10-01T03:00:00+08:00"),
    _snapshot_line(SNAPSHOT_B, ["task-2", "auto"], "2026-10-02T03:00:00+08:00"),
]


def _patch_run_json(ns, lines: list[str]) -> list[list[str]]:
    """替身 run_json：记录 argv 供白名单断言。返回 argv 收集器。"""
    argv_log: list[list[str]] = []

    async def _run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return ProbeResult(exit_code=0, lines=list(lines), truncated=False)

    ns.app.state.restic.run_json = _run_json
    return argv_log


async def _setup_repo(ns) -> list[list[str]]:
    """配置默认仓库 url（E17 前置，P3-C 起 url 落在 repos 表）并挂 run_json 替身。"""
    await ns.app.state.repos_repo.update("default", url="/repo")
    return _patch_run_json(ns, SNAPSHOTS_LINES)


async def test_e17_without_tag_returns_all(app_client):
    """不传 tag：行为与既有契约完全一致（全部快照、按时间倒序）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)

    resp = await ns.client.get("/api/snapshots")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [SNAPSHOT_B, SNAPSHOT_A]  # 最新在前


async def test_e17_tag_filter_keeps_snapshots_containing_tag(app_client):
    """``?tag=task-1`` → 只回 tags 数组包含该 tag 的快照。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)

    resp = await ns.client.get("/api/snapshots", params={"tag": "task-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == SNAPSHOT_A
    assert body["items"][0]["tags"] == ["task-1"]


async def test_e17_tag_filter_no_match_returns_empty(app_client):
    """无匹配 tag → 空列表 + total=0（分页壳完整，非 404）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)

    resp = await ns.client.get("/api/snapshots", params={"tag": "task-9"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_e17_tag_filter_never_passes_tag_to_restic(app_client):
    """过滤在 Python 侧做：restic argv 不出现 --tag（S2 白名单不扩面）。"""
    ns = app_client
    await finish_bootstrap(ns)
    argv_log = await _setup_repo(ns)

    await ns.client.get("/api/snapshots", params={"tag": "task-1"})
    assert len(argv_log) == 1
    assert "--tag" not in argv_log[0]


async def test_e17_requires_session(app_client):
    resp = await app_client.client.get("/api/snapshots")
    assert resp.status_code == 401
