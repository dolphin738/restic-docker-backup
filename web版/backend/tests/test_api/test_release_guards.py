"""第 0 批（D5+D6）：`release` 端点三守卫。

原缺口（2026-11-02 实测）：`POST /api/archive/repos/{repo_id}/release` 只有
「仓库存在」+「confirm_id 匹配」+「无活跃作业」三条校验，**缺** `ensure_deletable`
（同文件 DELETE 路由有）、**缺**「必须已归档」。后果：

- 可直接删掉 `default` 行 → 接通「墓碑到期 → 口令文件被判孤儿回收 →
  全局敏感键永久解不开」的时序炸弹（正常 UI 操作即可触发，无需越权）；
- 未归档的在用仓库也能释放，语义与「释放**归档**仓库 ID」不符；
- 有备份任务时抛未捕获的 `sqlite3.IntegrityError` → 落到 500 兜底。

本文件锁死修复后的三条断言。D6 裁决 = (c) 入口硬断言（default 永不可 release）。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap


async def _archived(ns, repo_id: str) -> None:
    """建一个已归档、无任务的仓库（release 的合法对象）。"""
    repos = ns.app.state.repos_repo
    await repos.create(repo_id, "归档仓库")
    path = ns.app.state.settings.passfile_path(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("archived-password", encoding="utf-8")
    await repos.archive(repo_id)


async def test_release_default_rejected(app_client):
    """D6=(c)：default 永不可 release —— 入口硬断言，行必须还在。

    这是时序炸弹的上弦点：一旦放行，365 天后墓碑到期会让 default 的口令文件
    被判孤儿并回收，全局敏感键（dump.password / notify.* / metrics.token）随之
    永久解不开，且那时已无恢复路径。
    """
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.post(
        "/api/archive/repos/default/release",
        json={"confirm_id": "default"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"
    # 行必须还在
    assert await ns.app.state.repos_repo.exists("default") is True


async def test_release_rejects_non_archived_repo(app_client):
    """未归档的在用仓库不可 release：语义是「释放**归档**仓库的 ID」。

    修复前实测返回 200 且行被删 —— 等于绕过 DELETE 路由的全部守卫直接删仓库。
    """
    ns = app_client
    await finish_bootstrap(ns)
    repos = ns.app.state.repos_repo
    await repos.create("live1", "在用仓库")
    assert (await repos.get("live1")).deleted_at is None

    resp = await ns.client.post(
        "/api/archive/repos/live1/release", json={"confirm_id": "live1"}, headers=ORIGIN
    )
    assert resp.status_code == 422, resp.text
    assert await repos.exists("live1") is True


async def test_release_with_task_returns_409_not_500(app_client):
    """有备份任务时必须给 409 REPO_NOT_EMPTY，不得落到 500。

    修复前：`drop_row` 撞 `FOREIGN KEY constraint failed` → `sqlite3.IntegrityError`
    未被捕获 → `main.py` 兜底 → 500「服务器内部错误」，用户无从判断原因。
    """
    ns = app_client
    await finish_bootstrap(ns)
    repos = ns.app.state.repos_repo
    await _archived(ns, "art1")

    # 归档后仍能建任务（backup_tasks 只查 exists，不查 deleted_at）——构造占用态
    resp = await ns.client.post(
        "/api/repos/art1/backup-tasks",
        json={"name": "t1", "sources": ["/etc"], "keep_last": 3},
        headers=ORIGIN,
    )
    assert resp.status_code == 201, resp.text

    resp = await ns.client.post(
        "/api/archive/repos/art1/release", json={"confirm_id": "art1"}, headers=ORIGIN
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "REPO_NOT_EMPTY"
    assert await repos.exists("art1") is True


async def test_release_archived_repo_still_works(app_client):
    """正向不回归：已归档、无任务、非 default 的仓库 release 仍 200，行删、墓碑留。"""
    ns = app_client
    await finish_bootstrap(ns)
    repos = ns.app.state.repos_repo
    await _archived(ns, "ok1")

    resp = await ns.client.post(
        "/api/archive/repos/ok1/release", json={"confirm_id": "ok1"}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    assert await repos.exists("ok1") is False
    tombs = [t for t in await repos.list_tombstones() if t["repo_id"] == "ok1"]
    assert len(tombs) == 1 and tombs[0]["released_at"] is not None
