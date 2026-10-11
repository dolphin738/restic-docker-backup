"""API 集成：E06/E07 配置读写（env 写保护、脱敏）、E29/E30 缺配置不 5xx、E32 排除集。"""

from __future__ import annotations

import pytest
from conftest import ORIGIN, finish_bootstrap


@pytest.fixture(autouse=True)
def _reset_repo_status_cache():
    """E30 的 TTL 缓存是模块级全局：用例间必须清零，否则前一个用例的探测结果会泄漏。"""
    from app.routers import config as config_router

    config_router._status_cache = None
    yield
    config_router._status_cache = None


async def test_e06_read_config_masking(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    client = ns.client

    # 写入敏感与非敏感项
    resp = await client.put(
        "/api/config",
        json={
            "patch": {
                "repo.url": "s3:s3.amazonaws.com/bk",
                "cred.aws_secret_access_key": "TOPSECRET",
                "dump.password": "dbpass",
            }
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["passfile_status"] == "configured"  # conftest 已挂密码文件

    items = {item["key"]: item for item in body["items"]}
    assert items["repo.url"]["value"] == "s3:s3.amazonaws.com/bk"
    assert items["repo.url"]["sensitive"] is False

    # 敏感项：只回 has_value + source，不回明文
    for key in ("cred.aws_secret_access_key", "dump.password"):
        assert items[key]["sensitive"] is True
        assert items[key]["has_value"] is True
        assert items[key]["value"] is None
    assert "TOPSECRET" not in resp.text
    assert "dbpass" not in resp.text


async def test_e07_env_override_blocks_write(make_app):
    """RESTIC_REPOSITORY env → repo.url 为 source='env'：PUT → 409 ENV_OVERRIDDEN。"""
    ns = await make_app({"RESTIC_REPOSITORY": "s3:env-override-bucket"})
    await finish_bootstrap(ns)
    client = ns.client

    resp = await client.get("/api/config")
    items = {item["key"]: item for item in resp.json()["items"]}
    assert items["repo.url"]["source"] == "env"
    assert items["repo.url"]["value"] == "s3:env-override-bucket"

    resp = await client.put("/api/config", json={"patch": {"repo.url": "x"}}, headers=ORIGIN)
    assert resp.status_code == 409
    assert resp.json()["code"] == "ENV_OVERRIDDEN"


async def test_e07_env_override_all_or_nothing(make_app):
    """混合 patch（env 键 + ui 键）整体拒绝，不产生部分写入。"""
    ns = await make_app({"RESTIC_REPOSITORY": "s3:env-override-bucket"})
    await finish_bootstrap(ns)

    resp = await ns.client.put(
        "/api/config",
        json={"patch": {"repo.url": "x", "repo.host_tag": "nas"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "ENV_OVERRIDDEN"

    resp = await ns.client.get("/api/config")
    keys = {item["key"] for item in resp.json()["items"]}
    assert "repo.host_tag" not in keys  # 未被部分写入


async def test_e07_illegal_key_422(app_client):
    await finish_bootstrap(app_client)
    resp = await app_client.client.put(
        "/api/config", json={"patch": {"Bad Key!": "x"}}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e07_empty_patch_422(app_client):
    await finish_bootstrap(app_client)
    resp = await app_client.client.put("/api/config", json={"patch": {}}, headers=ORIGIN)
    assert resp.status_code == 422


async def test_e29_repo_test_without_config(app_client):
    """E29：配置缺失不抛 5xx，回 ok:false + 可操作 reason（主理人裁决口径）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.post("/api/repo/test", headers=ORIGIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"]
    assert "repo.url" in body["reason"]


async def test_e30_repo_status_without_config(app_client):
    """E30：配置缺失不抛 5xx，回 connected:false + last_error（主理人裁决口径）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/repo/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["locked"] is False
    assert body["last_error"]
    assert body["capacity"]["size"] is None


async def test_e30_repo_status_cached(make_app):
    """E30 结果走 30s TTL 缓存：第二次请求直接命中（不再探测）。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    resp1 = await ns.client.get("/api/repo/status")
    assert resp1.status_code == 200
    resp2 = await ns.client.get("/api/repo/status")
    assert resp2.status_code == 200
    assert resp2.json() == resp1.json()


async def test_e30_cache_invalidated_by_config_write(make_app):
    """E07 写入 repo.url 后 E30 缓存立即失效（真机缺陷：保存后状态卡仍是旧值）。"""
    ns = await make_app()
    await finish_bootstrap(ns)

    # 无配置时缓存的是"仓库地址未配置"
    r1 = await ns.client.get("/api/repo/status")
    assert r1.status_code == 200
    first_error = r1.json()["last_error"]
    assert "未配置" in first_error

    # 保存 repo.url → 缓存必须被清除，下一条状态重新探测（测试环境无 restic 二进制，
    # 走 run_json 的 OSError 兜底 → exit_code=-1 的结构化失败，而非"未配置"旧缓存）
    put = await ns.client.put(
        "/api/config", json={"patch": {"repo.url": "/repo"}}, headers=ORIGIN
    )
    assert put.status_code == 200

    r2 = await ns.client.get("/api/repo/status")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["connected"] is False
    assert body2["last_error"] != first_error, "E07 写入后 E30 仍返回旧缓存"


async def test_e07_readonly_key_repo_type_409(app_client):
    """E07：只读派生键 repo.type 不可直接写 → 409 READONLY_KEY（set 与 set_many 双守卫）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.put(
        "/api/config", json={"patch": {"repo.type": "s3"}}, headers=ORIGIN
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "READONLY_KEY"
    assert body["data"]["key"] == "repo.type"


async def test_e07_repo_type_derived_from_url(app_client):
    """E07 写 repo.url → repo.type 按协议前缀同源推导落库（s3:/b2:/其余→local）。"""
    await finish_bootstrap(app_client)
    client = app_client.client

    for url, expected in (
        ("s3:s3.amazonaws.com/bk", "s3"),
        ("b2:bucket", "b2"),
        ("/mnt/repo", "local"),
    ):
        resp = await client.put("/api/config", json={"patch": {"repo.url": url}}, headers=ORIGIN)
        assert resp.status_code == 200, resp.text
        resp = await client.get("/api/config")
        items = {item["key"]: item for item in resp.json()["items"]}
        assert items["repo.type"]["value"] == expected, url
        assert items["repo.type"]["source"] == "ui"

    # 清空 repo.url → repo.type 跟随删除，不残留旧值
    resp = await client.put("/api/config", json={"patch": {"repo.url": None}}, headers=ORIGIN)
    assert resp.status_code == 200
    resp = await client.get("/api/config")
    keys = {item["key"] for item in resp.json()["items"]}
    assert "repo.type" not in keys


async def test_repo_type_derived_from_env_sync(make_app):
    """env 同步路径写 repo.url → repo.type 同源推导（source=env）。"""
    ns = await make_app({"RESTIC_REPOSITORY": "b2:env-bucket"})
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/config")
    items = {item["key"]: item for item in resp.json()["items"]}
    assert items["repo.type"]["value"] == "b2"
    assert items["repo.type"]["source"] == "env"


async def test_e32_exclusions_read_only(app_client):
    """E32：内置排除集只读暴露，必须鉴权；POST 方法不允许。"""
    ns = app_client
    client = ns.client

    resp = await client.get("/api/exclusions")
    assert resp.status_code == 401

    await finish_bootstrap(ns)
    resp = await client.get("/api/exclusions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "host"
    assert body["total"] == len(body["items"])
    assert "/host/proc" in body["items"]
    assert "/host/var/lib/docker" in body["items"]
    # core 是 items 的子集（模式无关语义核心 8 条）
    assert set(body["core"]) <= set(body["items"])
    assert len(body["core"]) == 8
    # 契约新增：展示形态字段（剥前缀，前端免过 PathMapper）
    assert "/proc" in body["display_items"]
    assert "/var/lib/docker" in body["display_items"]
    assert len(body["display_items"]) == len(body["items"])
    assert set(body["display_core"]) <= set(body["display_items"])
    assert len(body["display_core"]) == 8

    # 只读：写方法 405
    resp = await client.post("/api/exclusions", json={}, headers=ORIGIN)
    assert resp.status_code == 405


async def test_e32_exclusions_mode_query(app_client):
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/exclusions", params={"mode": "sources"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "sources"
    assert "/sources/proc" in body["items"]
