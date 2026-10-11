"""SPA 静态托管测试（单容器方案：8000 端口同时提供 API 与页面）。

回归背景：镜像此前**未打包前端产物且后端无静态路由**，浏览器打开 `/` 直接收到
``{"code":"NOT_FOUND","message":"Not Found"}``——页面完全不可达。
本文件锁定三条边界：页面可达、SPA 深链回落、`/api` 错误仍是统一 JSON。
"""

from __future__ import annotations

import pytest

INDEX_HTML = "<!doctype html><html><body>restic-web-spa</body></html>\n"


def _make_webroot(tmp_path) -> str:
    """造一个最小 webroot：index.html + assets/app.js（模拟 vite 产物布局）。"""
    webroot = tmp_path / "webroot"
    (webroot / "assets").mkdir(parents=True)
    (webroot / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (webroot / "assets" / "app.js").write_text("console.log('spa')\n", encoding="utf-8")
    return str(webroot)


@pytest.fixture()
async def spa_client(make_app, tmp_path):
    """指向临时 webroot 的应用实例（STATIC_DIR 环境变量驱动）。"""
    return await make_app({"STATIC_DIR": _make_webroot(tmp_path)})


async def test_root_serves_index_html(spa_client):
    resp = await spa_client.client.get("/")
    assert resp.status_code == 200
    assert "restic-web-spa" in resp.text


async def test_deep_route_falls_back_to_index(spa_client):
    """前端路由深链（/snapshots/xxx）必须回落 index.html，否则刷新即 404。"""
    resp = await spa_client.client.get("/snapshots/abc123")
    assert resp.status_code == 200
    assert "restic-web-spa" in resp.text


async def test_static_asset_is_served(spa_client):
    resp = await spa_client.client.get("/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


async def test_missing_asset_keeps_404(spa_client):
    """带扩展名的缺失资源不回落 HTML，避免掩盖前端产物缺失问题。"""
    resp = await spa_client.client.get("/assets/missing.js")
    assert resp.status_code == 404


async def test_api_route_is_not_shadowed(spa_client):
    resp = await spa_client.client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_unknown_api_path_stays_json_404(spa_client):
    """API 客户端必须拿到统一 JSON 错误，不能回落成 HTML 页面。"""
    resp = await spa_client.client.get("/api/not-exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_head_on_spa_route_succeeds(spa_client):
    resp = await spa_client.client.head("/restore")
    assert resp.status_code == 200


async def test_without_webroot_root_is_json_404(app_client):
    """无静态目录（本地后端开发态）：`/` 仍是统一 JSON 404，页面由 Vite 提供。"""
    resp = await app_client.client.get("/")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"
