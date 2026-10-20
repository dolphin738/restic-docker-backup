"""API 集成：E39 系统时间/时区同步 + E07 timezone 键写入校验。

断言点：
- ``GET /api/system/time``：UTC 时刻 + 生效时区本地时间 + 偏移 + NTP 探测形态
  （网络可达性不设断言，只断言字段形态与 reason 契约）；
- ``PUT /api/config`` 的 ``timezone`` 键：合法 IANA 名落库并触发调度器重载
  （保存即生效），非法名 422 前置拒绝（不得静默回退 UTC）；
- ``POST /api/system/time/sync``：审计 ``time.sync`` 埋点 + applied/reason 透传；
- 鉴权：未登录 401（S4）。
"""

from __future__ import annotations

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.constants import NTP_SERVERS
from app.services import scheduler as scheduler_module
from app.services import time_sync as time_sync_module


async def test_get_system_time_shape(app_client):
    """时间快照字段形态：server_time/timezone/local_time/utc_offset/epoch_ms/ntp。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.put("/api/config", json={"patch": {"timezone": "Asia/Shanghai"}}, headers=ORIGIN)
    assert resp.status_code == 200

    resp = await ns.client.get("/api/system/time")
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "Asia/Shanghai"
    assert body["utc_offset"] == "+08:00"
    assert body["server_time"].endswith("Z")
    assert body["local_time"].startswith(body["server_time"][:10].replace("Z", "")) or "T" in body["local_time"]
    assert isinstance(body["epoch_ms"], int) and body["epoch_ms"] > 0
    # 生效探测链：非空列表（未配置主备时 = 内置默认链）
    assert isinstance(body["ntp_servers"], list) and body["ntp_servers"]
    # NTP 探测：可用时带 offset_ms/server；不可用时必须带 reason（禁止静默）
    ntp = body["ntp"]
    if ntp["available"]:
        assert ntp["offset_ms"] is not None and ntp["server"]
    else:
        assert ntp["reason"]


async def test_put_config_rejects_invalid_timezone(app_client):
    """非法时区名 422 前置拒绝（坏值落库会被 resolve_timezone 静默回退 UTC）。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.put("/api/config", json={"patch": {"timezone": "Not/AZone"}}, headers=ORIGIN)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

    # 整体拒绝：不产生部分写入
    assert (await ns.client.get("/api/system/time")).json()["timezone"] != "Not/AZone"


async def test_put_config_valid_timezone_reloads_scheduler(app_client, monkeypatch):
    """合法时区落库 → 调度器重载（保存即生效，不等重启）。"""
    ns = app_client
    await finish_bootstrap(ns)

    reloads: list[str] = []
    original = scheduler_module.SchedulerService.reload

    async def counting_reload(self):  # type: ignore[no-untyped-def]
        reloads.append("reload")
        return await original(self)

    monkeypatch.setattr(scheduler_module.SchedulerService, "reload", counting_reload)

    resp = await ns.client.put("/api/config", json={"patch": {"timezone": "UTC"}}, headers=ORIGIN)
    assert resp.status_code == 200
    assert reloads == ["reload"]

    body = (await ns.client.get("/api/system/time")).json()
    assert body["timezone"] == "UTC"
    assert body["utc_offset"] == "+00:00"


async def test_time_sync_endpoint_requires_session(app_client):
    """未登录 401（S4：新端点不得破鉴权）。"""
    ns = app_client
    resp = await ns.client.get("/api/system/time")
    assert resp.status_code == 401
    resp = await ns.client.post("/api/system/time/sync", headers=ORIGIN)
    assert resp.status_code == 401


async def test_time_sync_noop_outcome_and_audit(app_client, monkeypatch):
    """偏移低于阈值：applied=False + reason=clock.noop；审计 time.sync 留痕。"""
    ns = app_client
    await finish_bootstrap(ns)

    async def fake_sync(servers=None, *, force: bool = False):
        return time_sync_module.TimeSyncOutcome(
            ok=True, applied=False, offset_ms=120.0, server="pool.ntp.org", reason="clock.noop"
        )

    monkeypatch.setattr("app.routers.system.sync_time", fake_sync)

    resp = await ns.client.post("/api/system/time/sync", headers=ORIGIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "applied": False, "offset_ms": 120.0, "server": "pool.ntp.org", "reason": "clock.noop"}

    resp = await ns.client.get("/api/audit?action=time.sync")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert items[0]["action"] == "time.sync"
    # detail 为 JSON 文本原文（E26 契约：前端只展示不改写）；这里断言其内容——
    # 只含偏移/服务器/原因，零凭据面（S3）
    import json

    detail = json.loads(items[0]["detail"])
    assert detail["applied"] is False
    assert detail["offset_ms"] == 120.0
    assert detail["server"] == "pool.ntp.org"
    assert detail["reason"] == "clock.noop"


async def test_time_sync_propagates_permission_denied(app_client, monkeypatch):
    """容器缺 CAP_SYS_TIME：ok=False + reason 透传，绝不假报成功。"""
    ns = app_client
    await finish_bootstrap(ns)

    async def fake_sync(servers=None, *, force: bool = False):
        return time_sync_module.TimeSyncOutcome(
            ok=False, applied=False, offset_ms=5000.0, server="pool.ntp.org", reason="clock.permission_denied"
        )

    monkeypatch.setattr("app.routers.system.sync_time", fake_sync)
    resp = await ns.client.post("/api/system/time/sync", headers=ORIGIN)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["reason"] == "clock.permission_denied"


async def test_get_system_time_uses_ntp_probe(app_client, monkeypatch):
    """GET /time 的 NTP 段落透传探测结果（含失败 reason）。"""
    ns = app_client
    await finish_bootstrap(ns)

    async def fake_probe(servers=None, *, force: bool = False):
        return time_sync_module.NtpProbeResult(
            available=False, server=None, offset_ms=None, rtt_ms=None, reason="ntp.timeout"
        )

    monkeypatch.setattr("app.routers.system.probe_ntp", fake_probe)
    resp = await ns.client.get("/api/system/time")
    assert resp.status_code == 200
    assert resp.json()["ntp"] == {
        "available": False,
        "server": None,
        "offset_ms": None,
        "rtt_ms": None,
        "reason": "ntp.timeout",
    }


@pytest.mark.parametrize("tz_name", ["Asia/Tokyo", "Europe/London", "America/Sao_Paulo"])
async def test_get_system_time_offset_variants(app_client, tz_name):
    """不同时区落地不同 utc_offset（时区设置真正作用于展示层）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.put("/api/config", json={"patch": {"timezone": tz_name}}, headers=ORIGIN)
    assert resp.status_code == 200
    body = (await ns.client.get("/api/system/time")).json()
    assert body["timezone"] == tz_name
    assert body["utc_offset"] in {"+09:00", "+01:00", "+00:00", "-03:00"}


# ---------------------------------------------------------------- NTP 主备服务器（E39）


async def test_put_config_ntp_primary_backup_and_effective_chain(app_client):
    """主备地址落库 → GET /time 的 ntp_servers 按「主 → 备」透传生效链。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.put(
        "/api/config",
        json={"patch": {"time_sync.ntp_primary": "ntp.example.com", "time_sync.ntp_backup": "ntp2.example.com:123"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 200

    body = (await ns.client.get("/api/system/time")).json()
    assert body["ntp_servers"] == ["ntp.example.com", "ntp2.example.com:123"]

    # 只留备用：主键清除后备用成为链首（回落默认链的语义只对「皆空」生效）
    resp = await ns.client.put(
        "/api/config", json={"patch": {"time_sync.ntp_primary": ""}}, headers=ORIGIN
    )
    assert resp.status_code == 200
    body = (await ns.client.get("/api/system/time")).json()
    assert body["ntp_servers"] == ["ntp2.example.com:123"]

    # 皆空 → 回落内置默认链
    resp = await ns.client.put(
        "/api/config", json={"patch": {"time_sync.ntp_backup": ""}}, headers=ORIGIN
    )
    assert resp.status_code == 200
    body = (await ns.client.get("/api/system/time")).json()
    assert body["ntp_servers"] == list(NTP_SERVERS)


@pytest.mark.parametrize(
    "bad_server",
    [
        "http://ntp.example.com",
        "ntp.example.com:0",
        "ntp.example.com:99999",
        "ntp.example.com:abc",
        "ntp..example.com",
        "ntp example.com",
        "ntp.example.com/path",
    ],
)
async def test_put_config_rejects_invalid_ntp_server(app_client, bad_server):
    """坏地址 422 前置拒绝（落库只在探测时才炸 = 「保存成功但同步失败」）。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.put(
        "/api/config",
        json={"patch": {"time_sync.ntp_primary": bad_server, "time_sync.ntp_backup": "ntp2.example.com"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

    # 整体拒绝：不产生部分写入（备用键也不得落库）
    body = (await ns.client.get("/api/system/time")).json()
    assert bad_server not in body["ntp_servers"]
    assert "ntp2.example.com" not in body["ntp_servers"]


async def test_time_sync_uses_configured_chain(app_client, monkeypatch):
    """POST /time/sync 按主备配置解析探测链并透传给同步服务（含 force）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.client.put(
        "/api/config",
        json={"patch": {"time_sync.ntp_primary": "ntp.example.com", "time_sync.ntp_backup": "ntp2.example.com"}},
        headers=ORIGIN,
    )

    seen: dict[str, object] = {}

    async def fake_sync(servers=None, *, force: bool = False):
        seen["servers"] = list(servers) if servers else None
        seen["force"] = force
        return time_sync_module.TimeSyncOutcome(
            ok=True, applied=True, offset_ms=5000.0, server="ntp.example.com", reason=None
        )

    monkeypatch.setattr("app.routers.system.sync_time", fake_sync)
    resp = await ns.client.post("/api/system/time/sync", headers=ORIGIN)
    assert resp.status_code == 200
    assert seen == {"servers": ["ntp.example.com", "ntp2.example.com"], "force": True}
