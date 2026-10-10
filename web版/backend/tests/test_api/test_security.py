"""API 集成：CSRF Origin 校验、登录锁定（持久化 + 指数退避）、进程内限流。"""

from __future__ import annotations

import pytest
from conftest import NEW_PASSWORD, ORIGIN

from app.auth.service import LoginRateLimiter
from app.errors import RateLimitedError

# ---------------------------------------------------------------- CSRF（§8 第二道防线）

async def test_write_without_origin_403(app_client):
    """浏览器对写方法必带 Origin：缺失即 403 CSRF_ORIGIN（含 E01 登录本身）。"""
    resp = await app_client.client.post("/api/auth/login", json={"password": "x"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CSRF_ORIGIN"


async def test_write_cross_origin_403(app_client):
    resp = await app_client.client.post(
        "/api/auth/login",
        json={"password": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "CSRF_ORIGIN"


async def test_write_referer_fallback_accepted(app_client):
    """Origin 缺失时回退 Referer 同源校验。"""
    resp = await app_client.client.get("/api/auth/bootstrap")
    password = resp.json()["password"]
    resp = await app_client.client.post(
        "/api/auth/login",
        json={"password": password},
        headers={"Referer": "http://testserver/login"},
    )
    assert resp.status_code == 200


async def test_authenticated_write_without_origin_403(app_client):
    """已登录会话下写方法（PUT /api/config）缺 Origin 同样 403。"""
    from conftest import finish_bootstrap

    await finish_bootstrap(app_client)
    resp = await app_client.client.put("/api/config", json={"patch": {"repo.host_tag": "nas"}})
    assert resp.status_code == 403
    assert resp.json()["code"] == "CSRF_ORIGIN"


# --------------------------------------------- 登录锁定（§8：5 次失败 → 5min×2^level，上限 1h；计数持久化）

async def _fail_times(client, times: int) -> None:
    for _ in range(times):
        resp = await client.post("/api/auth/login", json={"password": "wrong-password"}, headers=ORIGIN)
        assert resp.status_code == 401


async def test_lockout_after_five_failures(app_client):
    """5 次失败 → 锁定；第 6 次（即使密码正确）→ 429 ACCOUNT_LOCKED 带 retry_after。"""
    await app_client.client.get("/api/auth/bootstrap")  # bootstrap_shown=1，防重启重置路径干扰
    await _fail_times(app_client.client, 5)

    resp = await app_client.client.post(
        "/api/auth/login", json={"password": NEW_PASSWORD}, headers=ORIGIN
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == "ACCOUNT_LOCKED"
    assert body["data"]["retry_after"] > 0

    # 锁定状态已持久化到 admin_account
    row = await app_client.app.state.db.fetch_one(
        "SELECT failed_attempts, lock_level, locked_until FROM admin_account WHERE id = 1"
    )
    assert int(row["lock_level"]) == 1
    assert row["locked_until"] is not None
    assert int(row["failed_attempts"]) == 0  # 锁定后重新计数


async def test_lockout_exponential_level_two(app_client):
    """解锁（把 locked_until 置过去）后再错 5 次 → lock_level=2，时长翻倍仍 ≤ 1h。"""
    await app_client.client.get("/api/auth/bootstrap")
    await _fail_times(app_client.client, 5)
    # 手动把锁定置为已过期（模拟等待 5min 解锁）
    await app_client.app.state.db.write(
        "UPDATE admin_account SET locked_until = '2020-01-01T00:00:00Z' WHERE id = 1"
    )
    await _fail_times(app_client.client, 5)

    resp = await app_client.client.post("/api/auth/login", json={"password": "x"}, headers=ORIGIN)
    assert resp.status_code == 429
    row = await app_client.app.state.db.fetch_one("SELECT lock_level FROM admin_account WHERE id = 1")
    assert int(row["lock_level"]) == 2


async def test_lockout_persists_across_process_restart(make_app):
    """锁定计数持久化在 DB：重启进程（全新 AuthService/限流器）后仍然锁着。"""
    ns = await make_app()
    await ns.client.get("/api/auth/bootstrap")  # bootstrap_shown=1，避免重启重置路径
    for _ in range(5):
        resp = await ns.client.post("/api/auth/login", json={"password": "wrong"}, headers=ORIGIN)
        assert resp.status_code == 401

    # 模拟进程重启：同一 DB 文件，全新应用实例
    ns2 = await make_app()
    resp = await ns2.client.post("/api/auth/login", json={"password": "wrong"}, headers=ORIGIN)
    assert resp.status_code == 429
    assert resp.json()["code"] == "ACCOUNT_LOCKED"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Bug：bootstrap 随机密码展示前重启进程，AdminAccountStore._reset_random_password "
        "(app/auth/bootstrap.py:128-140) 会把 failed_attempts/lock_level/locked_until 一并清零，"
        "违反 §8『失败计数持久化在 DB 防容器重启清零』——锁定被重启绕过"
    ),
)
async def test_lockout_survives_restart_before_bootstrap_shown(make_app):
    """未展示一次性密码时重启：锁定状态不得被清零（文档 §8 明确要求持久化）。"""
    ns = await make_app()
    for _ in range(5):
        resp = await ns.client.post("/api/auth/login", json={"password": "wrong"}, headers=ORIGIN)
        assert resp.status_code == 401

    ns2 = await make_app()  # 未 reveal bootstrap → 重启触发 _reset_random_password
    resp = await ns2.client.post("/api/auth/login", json={"password": "wrong"}, headers=ORIGIN)
    assert resp.status_code == 429  # 实际：401 PASSWORD_WRONG（锁已被清）


async def test_lock_release_allows_login(app_client):
    """锁定到期（locked_until 过去）后登录恢复，计数清零。"""
    resp = await app_client.client.get("/api/auth/bootstrap")
    bootstrap_password = resp.json()["password"]
    await _fail_times(app_client.client, 5)
    await app_client.app.state.db.write(
        "UPDATE admin_account SET locked_until = '2020-01-01T00:00:00Z' WHERE id = 1"
    )

    resp = await app_client.client.post(
        "/api/auth/login", json={"password": bootstrap_password}, headers=ORIGIN
    )
    assert resp.status_code == 200
    row = await app_client.app.state.db.fetch_one(
        "SELECT lock_level, locked_until FROM admin_account WHERE id = 1"
    )
    assert int(row["lock_level"]) == 0
    assert row["locked_until"] is None


# ---------------------------------------------------------------- 进程内限流器（单元级）

def test_rate_limiter_fixed_window():
    limiter = LoginRateLimiter(limit=3, window_seconds=60)
    limiter.acquire("1.2.3.4")
    limiter.acquire("1.2.3.4")
    limiter.acquire("1.2.3.4")
    with pytest.raises(RateLimitedError) as excinfo:
        limiter.acquire("1.2.3.4")
    assert excinfo.value.data["retry_after"] >= 1


def test_rate_limiter_window_reset():
    limiter = LoginRateLimiter(limit=2, window_seconds=60)
    limiter.acquire("ip")
    limiter.acquire("ip")
    # 模拟窗口翻新：直接改桶起点
    key_bucket = limiter._buckets["ip"]
    limiter._buckets["ip"] = (key_bucket[0] - 61, key_bucket[1])
    limiter.acquire("ip")  # 不抛错
