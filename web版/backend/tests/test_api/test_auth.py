"""API 集成：认证链路 E01/E02/E03、bootstrap 一次性、E28 改密、首启门。"""

from __future__ import annotations

from conftest import NEW_PASSWORD, ORIGIN, finish_bootstrap


async def test_e04_health_no_auth(app_client):
    resp = await app_client.client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


async def test_me_requires_session(app_client):
    resp = await app_client.client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


async def test_bootstrap_password_one_time_only(app_client):
    resp = await app_client.client.get("/api/auth/bootstrap")
    assert resp.status_code == 200
    password = resp.json()["password"]
    assert len(password) >= 16

    # 第二次取 → 410 BOOTSTRAP_SHOWN
    resp2 = await app_client.client.get("/api/auth/bootstrap")
    assert resp2.status_code == 410
    assert resp2.json()["code"] == "BOOTSTRAP_SHOWN"

    # 该一次性密码可直接登录（password_hash 与 bootstrap_password_hash 同值）
    resp3 = await app_client.client.post("/api/auth/login", json={"password": password}, headers=ORIGIN)
    assert resp3.status_code == 200


async def test_bootstrap_gate_blocks_business_routes(app_client):
    """首启未改密：业务路由被压到 403 BOOTSTRAP_REQUIRED（PRD US-A1 AC2）。"""
    resp = await app_client.client.get("/api/auth/bootstrap")
    password = resp.json()["password"]
    resp = await app_client.client.post("/api/auth/login", json={"password": password}, headers=ORIGIN)
    assert resp.status_code == 200

    resp = await app_client.client.get("/api/config")
    assert resp.status_code == 403
    assert resp.json()["code"] == "BOOTSTRAP_REQUIRED"

    resp = await app_client.client.get("/api/jobs")
    assert resp.status_code == 403
    assert resp.json()["code"] == "BOOTSTRAP_REQUIRED"

    # me 端点同样被门拦下（路由内 must_change 检查）
    resp = await app_client.client.get("/api/auth/me")
    assert resp.status_code == 403
    assert resp.json()["code"] == "BOOTSTRAP_REQUIRED"


async def test_login_wrong_password_401(app_client):
    await app_client.client.get("/api/auth/bootstrap")
    resp = await app_client.client.post("/api/auth/login", json={"password": "totally-wrong"}, headers=ORIGIN)
    assert resp.status_code == 401
    assert resp.json()["code"] == "PASSWORD_WRONG"


async def test_e28_change_password_full_flow(app_client):
    """E28：首启场景免登录改密 → 旧密码失效 → 新密码登录 → must_change 解除。"""
    bootstrap_password = await finish_bootstrap(app_client)

    resp = await app_client.client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["must_change_password"] is False

    # 旧（一次性）密码已失效：password_hash 已被替换
    resp = await app_client.client.post(
        "/api/auth/login", json={"password": bootstrap_password}, headers=ORIGIN
    )
    assert resp.status_code == 401

    # 新密码可登录
    resp = await app_client.client.post("/api/auth/login", json={"password": NEW_PASSWORD}, headers=ORIGIN)
    assert resp.status_code == 200


async def test_e28_wrong_old_password_401(app_client):
    resp = await app_client.client.get("/api/auth/bootstrap")
    resp = await app_client.client.put(
        "/api/admin/password",
        json={"old_password": "wrong-old-password", "new_password": NEW_PASSWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "PASSWORD_WRONG"


async def test_e28_weak_new_password_422(app_client):
    """新密码长度足够但只有单一字符类别 → 422 VALIDATION_ERROR。"""
    resp = await app_client.client.get("/api/auth/bootstrap")
    password = resp.json()["password"]
    resp = await app_client.client.put(
        "/api/admin/password",
        json={"old_password": password, "new_password": "aaaaaaaaaa"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e28_too_short_password_422(app_client):
    resp = await app_client.client.get("/api/auth/bootstrap")
    password = resp.json()["password"]
    resp = await app_client.client.put(
        "/api/admin/password",
        json={"old_password": password, "new_password": "Sh0rt"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422


async def test_e02_logout_destroys_session(app_client):
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/auth/me")
    assert resp.status_code == 200

    resp = await app_client.client.post("/api/auth/logout", headers=ORIGIN)
    assert resp.status_code == 200
    assert resp.json()["code"] == "ok"

    # 服务端 session 已销毁：同一 cookie 再访问 → 401
    resp = await app_client.client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_login_response_sets_session_cookie(app_client):
    resp = await app_client.client.get("/api/auth/bootstrap")
    password = resp.json()["password"]
    resp = await app_client.client.post("/api/auth/login", json={"password": password}, headers=ORIGIN)
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert "rw_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie
