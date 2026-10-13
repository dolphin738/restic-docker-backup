"""API 集成：E23 通知配置读写 / E24 测试消息（R-018）+ E30 configured 字段（L3）。

断言点：
- E23 脱敏：healthchecks/webhook URL 只回 ``*_set`` 布尔，不回明文（cred.* 同口径）；
- E23 三态写入：None=不变 / ""=清除 / 非空=设置；空 patch → 422；
- E24 禁止静默：未配置任何渠道 → ok=False + 可操作原因；替身 Notifier 逐渠道结果透传；
- E30（L3）：repo.url 未配置 → ``configured=false``（UI 分色 warning 的数据源）；
- 鉴权：全部端点未登录 401。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

from app.services.notifier import ChannelResult


async def test_e23_defaults_and_roundtrip(app_client):
    """缺省值读取 → PUT 写入 → 回读（URL 只回 *_set，其余回现值）。"""
    ns = app_client
    await finish_bootstrap(ns)

    r0 = await ns.client.get("/api/notify")
    assert r0.status_code == 200
    assert r0.json()["healthchecks_url_set"] is False
    assert r0.json()["skipped_threshold"] == 100
    assert r0.json()["meow_redact"] is True
    assert r0.json()["webhook_redact"] is False

    resp = await ns.client.put(
        "/api/notify",
        json={
            "healthchecks_url": "https://hc-ping.com/secret-uuid",
            "webhook_template": "job={job_id} {status}",
            "meow_nickname": "nas",
            "on_success": True,
            "skipped_threshold": 0,
            "meow_redact": False,
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 200

    body = (await ns.client.get("/api/notify")).json()
    assert body["healthchecks_url_set"] is True
    assert "secret-uuid" not in str(body)  # 明文绝不回显
    assert body["webhook_template"] == "job={job_id} {status}"
    assert body["meow_nickname"] == "nas"
    assert body["on_success"] is True
    assert body["skipped_threshold"] == 0
    assert body["meow_redact"] is False


async def test_e23_url_three_state_semantics(app_client):
    """敏感 URL 三态：不传 = 不变；空串 = 清除；非空 = 设置。"""
    ns = app_client
    await finish_bootstrap(ns)

    await ns.client.put("/api/notify", json={"webhook_url": "https://hook.example/a"}, headers=ORIGIN)
    assert (await ns.client.get("/api/notify")).json()["webhook_url_set"] is True

    # 不传 webhook_url → 保持不变
    await ns.client.put("/api/notify", json={"meow_nickname": "x"}, headers=ORIGIN)
    assert (await ns.client.get("/api/notify")).json()["webhook_url_set"] is True

    # 空串 → 清除
    await ns.client.put("/api/notify", json={"webhook_url": ""}, headers=ORIGIN)
    assert (await ns.client.get("/api/notify")).json()["webhook_url_set"] is False


async def test_e23_empty_patch_rejected(app_client):
    """全字段 None 的空 patch → 422（与 E07 口径一致）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.put("/api/notify", json={}, headers=ORIGIN)
    assert resp.status_code == 422


async def test_e24_no_channels_returns_reason(app_client):
    """E24 未配置任何渠道：ok=False + 可操作原因（禁止静默）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post("/api/notify/test", json={}, headers=ORIGIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "未配置" in body["reason"]


async def test_e24_passthrough_channel_results(app_client):
    """E24 透传 Notifier 逐渠道结果（替身注入 app.state.notifier）。"""
    ns = app_client
    await finish_bootstrap(ns)

    class StubNotifier:
        async def send_test(self):
            return (
                False,
                42,
                [ChannelResult(channel="meow", ok=False, reason="MeoW 限流（429）")],
                "meow: MeoW 限流（429）",
            )

    ns.app.state.notifier = StubNotifier()
    resp = await ns.client.post("/api/notify/test", json={}, headers=ORIGIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["latency_ms"] == 42
    assert body["channels"][0]["channel"] == "meow"
    assert body["channels"][0]["reason"] == "MeoW 限流（429）"


async def test_e23_e24_require_session(app_client):
    """未登录 → 401。"""
    ns = app_client
    assert (await ns.client.get("/api/notify")).status_code == 401
    assert (await ns.client.post("/api/notify/test", json={}, headers=ORIGIN)).status_code == 401


async def test_e30_configured_false_without_repo_url(app_client):
    """E30（L3）：repo.url 未配置 → configured=false（「未配置」与「连接失败」分色数据源）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/repo/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["connected"] is False
