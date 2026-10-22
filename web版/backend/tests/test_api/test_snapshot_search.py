"""API 集成：P3 快照内文件搜索 ``POST /api/snapshots/{id}/files/search``。

restic 探测以 ``run_json`` 替身覆盖（预录 ``ls --json`` 全量输出），路由/过滤/游标
分页全走真实代码路径。断言点：

- **服务端过滤**：整棵快照树只跑一次 restic，命中在后端做（前端不拉全量再过滤，V-3）；
- **展示路径语义**：匹配与输出都按 ``display_path``（原路径语义），容器路径只在 ``path``；
- 大小写不敏感；
- **游标口径与 E18 同**：``next_cursor`` 可续页，末页为 ``null``，``total`` 是命中总数；
- **上限**：``limit`` 超 ``SNAPSHOT_FILES_MAX_LIMIT`` → 422；空关键词 → 422（禁全量拉取）；
- **失败不静默**：仓库未配置 / restic 非 0 退出 → 500 ``RESTIC_FAILED``。
"""

from __future__ import annotations

import json

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.services.restic import ProbeResult

SNAPSHOT_ID = "a" * 64
ROOT = "/sources/etc"
MTIME = "2026-10-01T03:00:00Z"

_LS_ENTRIES = [
    ("dir", "nginx", None),
    ("file", "nginx/nginx.conf", 512),
    ("file", "nginx/site.conf", 1024),
    ("file", "hosts", 128),
]


def _ls_lines() -> list[str]:
    """构造 ``ls --json`` NDJSON 输出（容器路径形态，含目录与文件）。"""
    return [
        json.dumps(
            {
                "name": name.rsplit("/", 1)[-1],
                "type": kind,
                "path": f"{ROOT}/{name}",
                "size": size,
                "mtime": MTIME,
                "struct_type": "node",
            }
        )
        for kind, name, size in _LS_ENTRIES
    ]


def _patch_run_json(ns, *, lines: list[str] | None = None, exit_code: int = 0) -> list[list[str]]:
    """替身 run_json：记录 argv（供「只跑一次」断言）。返回 argv 收集器。"""
    argv_log: list[list[str]] = []

    async def _run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return ProbeResult(exit_code=exit_code, lines=list(_ls_lines() if lines is None else lines), truncated=False)

    ns.app.state.restic.run_json = _run_json
    return argv_log


async def _setup_repo(ns) -> None:
    await ns.app.state.repos_repo.update("default", url="/repo")


async def _search(ns, query: str, *, limit: int | None = None, cursor: str | None = None):
    """调用搜索端点。"""
    payload: dict[str, object] = {"query": query}
    if limit is not None:
        payload["limit"] = limit
    if cursor is not None:
        payload["cursor"] = cursor
    return await ns.client.post(
        f"/api/snapshots/{SNAPSHOT_ID}/files/search", json=payload, headers=ORIGIN
    )


async def test_search_matches_display_path_case_insensitive(app_client):
    """对展示路径做大小写不敏感子串匹配；容器路径只出现在 path 字段。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns)

    resp = await _search(ns, "NGINX")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3  # 目录 + 两个文件（跨层级，非直接子项）
    assert [item["display_path"] for item in body["items"]] == [
        "/etc/nginx",
        "/etc/nginx/nginx.conf",
        "/etc/nginx/site.conf",
    ]
    assert all(item["path"].startswith("/sources/etc/") for item in body["items"])
    assert len(argv_log) == 1  # 整棵树只跑一次 restic
    assert "ls" in argv_log[0]


async def test_search_no_match_returns_empty(app_client):
    """无命中：空 items + total=0 + next_cursor=null（不是 500/422）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns)

    body = (await _search(ns, "no-such-name")).json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["next_cursor"] is None


async def test_search_cursor_pagination_same_semantics_as_e18(app_client):
    """游标分页：首页带 next_cursor，续页取余下条目，末页 next_cursor=null。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns)

    first = (await _search(ns, "nginx", limit=2)).json()
    assert [item["name"] for item in first["items"]] == ["nginx", "nginx.conf"]
    assert first["total"] == 3
    assert first["next_cursor"] is not None

    second = (await _search(ns, "nginx", limit=2, cursor=first["next_cursor"])).json()
    assert [item["name"] for item in second["items"]] == ["site.conf"]
    assert second["total"] == 3
    assert second["next_cursor"] is None


async def test_search_limit_above_max_422(app_client):
    """分页上限：超过 SNAPSHOT_FILES_MAX_LIMIT → 422（分页上限单测）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns)

    resp = await _search(ns, "nginx", limit=1001)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert argv_log == []  # 参数校验前置，restic 不跑


async def test_search_blank_query_422(app_client):
    """空/纯空白关键词 → 422（禁止退化成全量拉取，V-3）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns)

    resp = await _search(ns, "   ")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert argv_log == []


async def test_search_repo_unconfigured_500(app_client):
    """仓库地址未配置 → 500 RESTIC_FAILED（探测失败不静默）。"""
    ns = app_client
    await finish_bootstrap(ns)
    _patch_run_json(ns)

    resp = await _search(ns, "nginx")
    assert resp.status_code == 500
    assert resp.json()["code"] == "RESTIC_FAILED"


async def test_search_restic_failure_500(app_client):
    """restic 非 0 退出 → 500 RESTIC_FAILED + 可操作 reason。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns, lines=["unable to create lock"], exit_code=11)

    resp = await _search(ns, "nginx")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "RESTIC_FAILED"
    assert "锁残留" in body["data"]["reason"]


async def test_search_requires_session(app_client):
    """未登录 → 401（S4：新端点挂 require_session）。"""
    ns = app_client
    resp = await _search(ns, "nginx")
    assert resp.status_code == 401


@pytest.fixture(autouse=True)
def _clean_search_cache():
    """搜索缓存是模块级字典（B3）：用例前后各清一次，杜绝跨用例污染。"""
    from app.routers import snapshots as snapshots_router

    snapshots_router._search_cache.clear()
    yield
    snapshots_router._search_cache.clear()


async def test_search_caches_repeated_query(app_client):
    """B3（owner 2026-10-23 裁决）：同一关键词翻页/重复搜索不再重跑 restic ls。

    三层断言：① 同词两次 → 一次 ls；② 大小写不同但 casefold 相同 → 仍命中同一缓存；
    ③ 换关键词 → 重新探测（缓存按关键词维度，不是按快照一刀切）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns)

    assert (await _search(ns, "nginx")).status_code == 200
    assert (await _search(ns, "nginx")).status_code == 200
    assert len(argv_log) == 1  # 第二次走缓存

    assert (await _search(ns, "NGINX")).status_code == 200  # casefold 命中同一键
    assert len(argv_log) == 1

    assert (await _search(ns, "hosts")).status_code == 200
    assert len(argv_log) == 2  # 换词 → 重新探测
