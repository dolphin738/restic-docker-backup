"""A8 仓库 ID 自动生成：POST /api/repos 的 id 可选 + GET /api/repos/_new_id 候选生成。

owner 2026-10-17 07:37 立项：缺省服务端生成 ``repo-<小写 ULID>``（26 位、全局唯一、
按时间有序，天然满足 REPO_ID_PATTERN 与 64 长度上限）；显式提供仍按既有规则校验。
"""

from __future__ import annotations

import re

from conftest import ORIGIN, finish_bootstrap

from app.constants import REPO_ID_MAX_LENGTH, REPO_ID_PATTERN
from app.routers.repos import _new_repo_id, _resolve_repo_id

_ID_RE = re.compile(REPO_ID_PATTERN)


# ---------------------------------------------------------------- 单元：生成器

def test_new_repo_id_format():
    """_new_repo_id 形如 repo-<26 位小写 ULID>，满足命名规则与长度上限。"""
    rid = _new_repo_id()
    assert rid.startswith("repo-")
    assert _ID_RE.match(rid), rid
    assert len(rid) <= REPO_ID_MAX_LENGTH
    # 小写 + 26 位 ULID 主体
    assert rid[5:].islower()
    assert len(rid) == 5 + 26


def test_new_repo_id_unique():
    """连续生成不重复（ULID 时间+随机，碰撞概率极低）。"""
    ids = {_new_repo_id() for _ in range(100)}
    assert len(ids) == 100


async def test_resolve_repo_id_explicit_passthrough(app_client):
    """显式提供 id → 原样返回（落库侧再校验）。"""
    ns = app_client
    await finish_bootstrap(ns)
    assert await _resolve_repo_id(ns.app.state.repos_repo, "my-repo") == "my-repo"
    assert await _resolve_repo_id(ns.app.state.repos_repo, "  spaced  ") == "spaced"


async def test_resolve_repo_id_generated_on_blank(app_client):
    """留空 → 服务端生成且不在册（exists 查重）。"""
    ns = app_client
    await finish_bootstrap(ns)
    generated = await _resolve_repo_id(ns.app.state.repos_repo, None)
    assert _ID_RE.match(generated)
    assert await ns.app.state.repos_repo.get(generated) is None


# ---------------------------------------------------------------- API：POST /api/repos 缺省生成

async def test_create_repo_without_id_generates(app_client):
    """POST /api/repos 不带 id → 200 且服务端生成 repo-<ULID> 标识并落库。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos",
        json={"name": "自动命名仓库"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rid = body["id"]
    assert _ID_RE.match(rid), rid
    assert rid.startswith("repo-")
    assert await ns.app.state.repos_repo.get(rid) is not None


async def test_create_repo_empty_id_generates(app_client):
    """POST /api/repos 带空串 id → 等同缺省，服务端生成。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos",
        json={"id": "", "name": "空串命名"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"].startswith("repo-")


async def test_create_repo_explicit_id_used(app_client):
    """POST /api/repos 带显式 id → 原样使用。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos",
        json={"id": "explicit-1", "name": "显式命名"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "explicit-1"
    assert await ns.app.state.repos_repo.get("explicit-1") is not None


async def test_create_repo_duplicate_explicit_id_rejected(app_client):
    """显式 id 撞名 → 422（既有行为保留）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("dup-1", "已存在")
    resp = await ns.client.post(
        "/api/repos",
        json={"id": "dup-1", "name": "再来一个"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


async def test_create_repo_invalid_id_pattern_rejected(app_client):
    """显式 id 含非法字符（如 `.`）→ 422（路径穿越防护不变）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos",
        json={"id": "bad.id", "name": "坏 id"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text
    assert await ns.app.state.repos_repo.get("bad.id") is None


# ---------------------------------------------------------------- API：GET /api/repos/_new_id

async def test_suggest_repo_id(app_client):
    """GET /api/repos/_new_id → 200 且返回 repo-<ULID> 候选（不落库）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/repos/_new_id", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    rid = resp.json()["id"]
    assert rid.startswith("repo-")
    assert _ID_RE.match(rid), rid
    # 候选不应已落库
    assert await ns.app.state.repos_repo.get(rid) is None


async def test_suggest_repo_id_not_captured_by_path_param(app_client):
    """/_new_id 不被 /repos/{repo_id} 路径参数捕获（声明顺序保证）。"""
    ns = app_client
    await finish_bootstrap(ns)
    # 若被 {repo_id} 捕获，会返回 404（找不到 "_new_id" 仓库）而非 200 候选
    resp = await ns.client.get("/api/repos/_new_id", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    assert "id" in resp.json()
