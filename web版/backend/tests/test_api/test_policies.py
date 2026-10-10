"""API 集成：E08–E11 Policy display 派生字段 + excludes 归一白名单收紧（最后增量契约）。

主理人指定断言：
1. Policy display 回显：`*.tmp` 纯 glob 原样、锚定路径剥前缀，容器字段 wire 不变；
2. 越界 422：host 模式下 E12 与 E31 提交 excludes `["/sources/data"]` → 均 422
   VALIDATION_ERROR，且 E12 任务不落库。
"""

from __future__ import annotations

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.errors import ValidationError


async def _update_default_policy(client, **overrides) -> dict:
    """E10 全量替换默认策略（id=1，由 lifespan ensure_default 创建）。"""
    payload = {
        "name": "default",
        # 文档 §5.1 E09/E10 示例口径：sources 收容器形态（/host 前缀）
        "sources": ["/host/etc/nginx", "/host/var/lib"],
        # excludes 收三分类形态（展示 / 容器 / 纯 glob）
        "excludes": ["/var/backups", "*.tmp", "/host/var/log"],
        "keep_daily": 7,
        "cron": "0 3 * * *",
    }
    payload.update(overrides)
    resp = await client.put("/api/policies/1", json=payload, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_policy_display_fields_roundtrip(app_client):
    """display 派生回显：纯 glob 原样返回（不补前导 /），锚定路径剥前缀；容器字段 wire 不变。

    同时验证容器形态输入不再被二次叠加模式根（/host/var/log 输入 → 存储仍为 /host/var/log）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    body = await _update_default_policy(ns.client)

    # 容器字段（wire 兼容，存储/restic argv 口径）
    assert body["sources"] == ["/host/etc/nginx", "/host/var/lib"]
    assert body["excludes"] == ["/host/var/backups", "*.tmp", "/host/var/log"]

    # display 派生字段：锚定路径剥前缀、纯 glob 原样（否则 to_display("*.tmp") 会变 "/*.tmp"）
    assert body["display_sources"] == ["/etc/nginx", "/var/lib"]
    assert body["display_excludes"] == ["/var/backups", "*.tmp", "/var/log"]

    # GET 回读与 PUT 响应一致
    resp = await ns.client.get("/api/policies")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["excludes"] == body["excludes"]
    assert item["display_excludes"] == body["display_excludes"]
    assert item["display_sources"] == body["display_sources"]


async def test_excludes_cross_mode_422_on_e12(app_client):
    """host 模式下 E12 params.excludes 提交 /sources/data → 422 VALIDATION_ERROR，任务不落库。"""
    ns = app_client
    await finish_bootstrap(ns)

    row = await ns.app.state.db.fetch_one("SELECT COUNT(*) AS total FROM jobs")
    before = int(row["total"])

    resp = await ns.client.post(
        "/api/jobs",
        json={
            "type": "backup",
            "policy_id": 1,
            "params": {"sources": ["/etc"], "excludes": ["/sources/data"]},
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"

    row = await ns.app.state.db.fetch_one("SELECT COUNT(*) AS total FROM jobs")
    assert int(row["total"]) == before  # 任务不落库


async def test_excludes_cross_mode_422_on_e31(app_client):
    """host 模式下 E31 excludes 提交 /sources/data → 422 VALIDATION_ERROR。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.post(
        "/api/sources/dry-run",
        json={"sources": ["/etc"], "excludes": ["/sources/data"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["data"]["exclude"] == "/sources/data"


async def test_sources_display_path_roundtrip(app_client):
    """sources 归一（方案②）：展示形态提交 → wire 容器形态 + display 派生；
    glob 锚定源（/etc/*.conf）同走 to_snapshot 映射；容器形态入参保持兼容。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.put(
        "/api/policies/1",
        json={"name": "default", "sources": ["/etc/nginx", "/etc/*.conf"], "excludes": ["*.tmp"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # wire 容器形态（restic argv 口径）
    assert body["sources"] == ["/host/etc/nginx", "/host/etc/*.conf"]
    # display 派生（剥前缀，glob 锚定源同走映射）
    assert body["display_sources"] == ["/etc/nginx", "/etc/*.conf"]

    # 容器形态入参兼容：不二次叠加模式根
    resp = await ns.client.put(
        "/api/policies/1",
        json={"name": "default", "sources": ["/host/etc/nginx"], "excludes": []},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    assert resp.json()["sources"] == ["/host/etc/nginx"]
    assert resp.json()["display_sources"] == ["/etc/nginx"]


async def test_sources_cross_mode_422(app_client):
    """host 模式下 sources 提交 /sources/data 与 /sources → 均 422 VALIDATION_ERROR，
    策略不落库（E10 入口；E09 入口被 P1 单策略门禁先拦——见测试内说明）。"""
    ns = app_client
    await finish_bootstrap(ns)

    # 合法基线：先写入一份可回读的策略
    resp = await ns.client.put(
        "/api/policies/1",
        json={"name": "default", "sources": ["/etc/nginx"], "excludes": []},
        headers=ORIGIN,
    )
    assert resp.status_code == 200

    row = await ns.app.state.db.fetch_one("SELECT COUNT(*) AS total FROM policies")
    before = int(row["total"])

    for bad_sources in (["/sources/data"], ["/sources"]):
        resp = await ns.client.put(
            "/api/policies/1",
            json={"name": "default", "sources": bad_sources, "excludes": []},
            headers=ORIGIN,
        )
        assert resp.status_code == 422, bad_sources
        body = resp.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert body["data"]["source"] == bad_sources[0]

    # 策略不落库：条数不变，内容仍是基线值
    row = await ns.app.state.db.fetch_one("SELECT COUNT(*) AS total FROM policies")
    assert int(row["total"]) == before
    resp = await ns.client.get("/api/policies")
    assert resp.json()["items"][0]["sources"] == ["/host/etc/nginx"]

    # E09 入口：P1 已有默认策略时先命中 409 POLICY_WRITE_CLOSED（门禁先于 sources 归一），
    # 两者共用同一 normalize → 用单测覆盖 E09 可达时的归一行为（跨模式 422）
    from app.services.policy_service import PolicyService
    from app.settings import Settings

    service = PolicyService(repo=None, settings=Settings(restic_web_mode="host"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        service.normalize({"sources": ["/sources/data"]})

    resp = await ns.client.post(
        "/api/policies",
        json={"name": "second", "sources": ["/etc"], "excludes": []},
        headers=ORIGIN,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "POLICY_WRITE_CLOSED"


async def test_excludes_cross_mode_422_on_e10(app_client):
    """host 模式下 E10 excludes 提交 /sources/data → 422（第三处调用点同收紧）。"""
    await finish_bootstrap(app_client)
    body = await _update_default_policy(app_client.client)  # 先确认合法基线可写
    assert body["excludes"]

    resp = await app_client.client.put(
        "/api/policies/1",
        # sources 用容器形态（文档口径），让断言聚焦在 excludes 越界上
        json={"name": "default", "sources": ["/host/etc"], "excludes": ["/sources/data"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["data"]["exclude"] == "/sources/data"
