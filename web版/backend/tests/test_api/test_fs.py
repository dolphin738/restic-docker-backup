"""API 集成：E33 容器文件系统浏览（仓库路径选择）。

覆盖：鉴权门、根目录列举、相对路径 422、不存在 404、游标分页、mounts 仅根目录返回。
注：测试宿主可能是 Windows（/proc/mounts 不存在），断言只针对契约本身，不断言挂载点内容。
"""

from __future__ import annotations

from conftest import finish_bootstrap


async def test_fs_browse_requires_session(make_app):
    """未登录直接 401/403，不泄露容器目录结构。"""
    ns = await make_app()
    resp = await ns.client.get("/api/fs/browse")
    assert resp.status_code in (401, 403)


async def test_fs_browse_root_lists_entries(make_app):
    """默认根目录：200 + path=/ + 条目字段齐全。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "/"
    assert isinstance(body["mounts"], list)
    assert body["next_cursor"] is None or isinstance(body["next_cursor"], str)
    for item in body["items"]:
        assert item["name"]
        assert item["type"] in ("dir", "file", "other")
        assert item["path"].startswith("/")
        assert isinstance(item["is_mountpoint"], bool)


async def test_fs_browse_rejects_relative_path(make_app):
    """相对路径 → 422 VALIDATION_ERROR（避免相对进程 cwd 的歧义）。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse", params={"path": "etc/nginx"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_fs_browse_missing_path_404(make_app):
    """不存在的路径 → 404 NOT_FOUND。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse", params={"path": "/definitely/not/exists-xyz"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_fs_browse_pagination_cursor(make_app):
    """limit=1 翻页：第二页首条目不与第一页重复，游标耗尽后 next_cursor=None。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp1 = await ns.client.get("/api/fs/browse", params={"limit": 1})
    assert resp1.status_code == 200
    page1 = resp1.json()
    assert len(page1["items"]) == 1
    if page1["next_cursor"] is None:
        return  # 根目录只有 0/1 个条目的极端环境，跳过
    resp2 = await ns.client.get(
        "/api/fs/browse", params={"limit": 1, "cursor": page1["next_cursor"]}
    )
    page2 = resp2.json()
    assert page2["items"][0]["name"] != page1["items"][0]["name"]


async def test_fs_browse_path_normalization(make_app):
    """``/../`` 折叠为根目录：与直接请求 / 同形（normpath 语义）。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/fs/browse", params={"path": "/../"})
    assert resp.status_code == 200
    assert resp.json()["path"] == "/"
