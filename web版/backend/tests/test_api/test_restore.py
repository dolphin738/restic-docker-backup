"""API 集成：E19 恢复到时间戳子目录 + B1 文件级恢复 paths 归一（R-027，增量设计 §2.2/§3.1）。

S1 校验点①（路由层）：paths 逐条 ``to_snapshot`` + ``PathMapper.validate``（模式根
白名单），越界 400 ``PATH_OUT_OF_PREFIX``（data 带 path）；归一后经 ``params.include``
入 JobRunner；``restore.run`` 审计 detail 增 ``include_count``（S6：不新增动作名）。
S7 不变：目标仍为服务端计算的时间戳子目录。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

SNAPSHOT_ID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"


def _patch_submit(ns) -> list[dict]:
    """捕获 runner.submit 的 kwargs（params 透传断言）；返回调用收集器。"""
    calls: list[dict] = []
    original = ns.app.state.job_runner.submit

    async def _submit(job_type, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"job_type": job_type, **kwargs})
        return await original(job_type, **kwargs)

    ns.app.state.job_runner.submit = _submit
    return calls


async def test_e19_without_paths_restores_whole_snapshot(app_client):
    """缺省 paths = 整快照恢复（兼容既有行为；params 不带 include）。"""
    ns = app_client
    await finish_bootstrap(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore", json={"snapshot_id": SNAPSHOT_ID, "target": "/restore"}, headers=ORIGIN
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"]
    assert body["restore_target"].startswith("/restore/")

    assert len(calls) == 1
    assert calls[0]["job_type"] == "restore"
    assert "include" not in calls[0]["params"]


async def test_e19_paths_normalized_to_container_form(app_client):
    """展示形态 paths → 容器形态 params.include（去重保序）；审计带 include_count。"""
    ns = app_client
    await finish_bootstrap(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={
            "snapshot_id": SNAPSHOT_ID,
            "target": "/restore",
            "paths": ["/etc", "/home/u/x", "/etc"],
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 202

    assert calls[0]["params"]["include"] == ["/sources/etc", "/sources/home/u/x"]

    import json

    rows, total = await ns.app.state.audit.query(action="restore.run")
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["include_count"] == 2


async def test_e19_paths_out_of_prefix_returns_400(app_client):
    """越界 paths → 400 PATH_OUT_OF_PREFIX（data 带 path；同步回前端，不打进 failed）。"""
    ns = app_client
    await finish_bootstrap(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": "/restore", "paths": ["/etc\x00x"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "PATH_OUT_OF_PREFIX"
    assert body["data"]["path"] == "/etc\x00x"
    assert calls == []  # 未提交任何任务


async def test_e19_empty_paths_treated_as_whole_snapshot(app_client):
    """空数组 paths = 整快照（与缺省同义）。"""
    ns = app_client
    await finish_bootstrap(ns)
    calls = _patch_submit(ns)

    resp = await ns.client.post(
        "/api/restore",
        json={"snapshot_id": SNAPSHOT_ID, "target": "/restore", "paths": []},
        headers=ORIGIN,
    )
    assert resp.status_code == 202
    assert "include" not in calls[0]["params"]


async def test_e19_target_out_of_whitelist_403(app_client):
    """target 越界 → 403 RESTORE_FORBIDDEN（S7：仅 /restore 白名单）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/restore", json={"snapshot_id": SNAPSHOT_ID, "target": "/sources"}, headers=ORIGIN
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "RESTORE_FORBIDDEN"


async def test_e19_requires_session(app_client):
    resp = await app_client.client.post(
        "/api/restore", json={"snapshot_id": SNAPSHOT_ID, "target": "/restore"}, headers=ORIGIN
    )
    assert resp.status_code == 401
