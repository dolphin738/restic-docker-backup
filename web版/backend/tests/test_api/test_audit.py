"""API 集成：E26 审计日志查询（R-020 批次 A）——前缀匹配/转义对抗/维度过滤/只读。

注意：``finish_bootstrap`` 自身会落 bootstrap.shown / password.changed / login.ok 等
审计行，故凡涉及 total 的断言一律用「播种前后增量」口径，不写绝对数。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

OK, DENIED = "ok", "denied"  # AuditResult 枚举字符串值


async def _audit_total(ns) -> int:
    return (await ns.app.state.audit.query(limit=1))[1]


async def _seed(ns) -> int:
    """落三条可区分的审计记录，返回播种前的总数（供增量断言）。"""
    before = await _audit_total(ns)
    audit = ns.app.state.audit
    await audit.record("login.ok", target="session", result=OK)
    await audit.record("login.failed", target="10.0.0.1", result=DENIED)
    await audit.record("restore.run", target="/restore/etc-x", result=OK)
    return before


async def test_e26_requires_session(app_client):
    resp = await app_client.client.get("/api/audit")
    assert resp.status_code == 401


async def test_e26_default_lists_all_with_actions_enum(app_client):
    """缺省全量倒序 + ``actions`` 动作名枚举（字典序去重，筛选下拉数据源）。"""
    ns = app_client
    await finish_bootstrap(ns)
    before = await _seed(ns)

    resp = await ns.client.get("/api/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == before + 3
    # 倒序：最新一条是最后播种的 restore.run
    assert body["items"][0]["action"] == "restore.run"
    assert {item["action"] for item in body["items"]} >= {"login.ok", "login.failed", "restore.run"}
    # actions 枚举：字典序去重，且包含播种的三值
    actions = body["actions"]
    assert actions == sorted(set(actions))
    assert {"login.ok", "login.failed", "restore.run"} <= set(actions)
    # 字段全集（§4.6）
    assert set(body["items"][0].keys()) == {
        "id", "ts", "action", "target", "detail", "result", "created_at",
    }


async def test_e26_action_prefix_match(app_client):
    """``action`` 前缀匹配（``restore.`` 恰命中播种的 restore.run；bootstrap 无该前缀行）。"""
    ns = app_client
    await finish_bootstrap(ns)
    before_restore = (await ns.app.state.audit.query(action_prefix="restore.", limit=1))[1]
    await ns.app.state.audit.record("restore.run", target="/restore/etc-x", result=OK)

    resp = await ns.client.get("/api/audit", params={"action": "restore."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == before_restore + 1  # bootstrap 流程不产生 restore.* 动作
    assert body["items"][0]["action"] == "restore.run"


async def test_e26_action_prefix_escapes_wildcards(app_client):
    """转义对抗（验收要点）：输入含 ``%`` 不越权——未转义时 ``log%in`` 会命中 ``login.ok``。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed(ns)

    resp = await ns.client.get("/api/audit", params={"action": "log%in"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0  # LIKE "log\%in%" 只匹配字面 "log%in…" 前缀

    resp = await ns.client.get("/api/audit", params={"action": "restore.%25"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0  # "restore.%" 字面前缀同样不命中 restore.run


async def test_e26_result_filter_whitelist(app_client):
    """``result`` 白名单过滤（denied 只含播种的 login.failed）；非法值 422。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed(ns)

    resp = await ns.client.get("/api/audit", params={"result": DENIED})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["result"] == DENIED for item in body["items"])
    assert {item["action"] for item in body["items"]} == {"login.failed"}

    resp = await ns.client.get("/api/audit", params={"result": "bogus"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e26_time_range_and_invalid_time_422(app_client):
    """``since``/``until`` 时间过滤（C8 归一）；非法时间 422。"""
    ns = app_client
    await finish_bootstrap(ns)
    before = await _seed(ns)
    total = await _audit_total(ns)

    # 上界取远 past → 零命中；下界取远 past → 全命中
    resp = await ns.client.get("/api/audit", params={"until": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    resp = await ns.client.get("/api/audit", params={"since": "2000-01-01T00:00:00Z"})
    assert resp.status_code == 200
    assert resp.json()["total"] == total

    # 带时区下界（+08:00 归一 UTC）→ 同一结果
    resp = await ns.client.get("/api/audit", params={"since": "2000-01-01T08:00:00+08:00"})
    assert resp.status_code == 200
    assert resp.json()["total"] == total
    assert before >= 0  # 增量口径锚点（bootstrap 审计行已计入 total）

    resp = await ns.client.get("/api/audit", params={"since": "not-a-time"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert resp.json()["data"]["field"] == "since"


async def test_e26_limit_upper_bound_500(app_client):
    """``limit`` 上限 500（与 E13 同口径，Q13）；501 → 422。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/audit", params={"limit": 500})
    assert resp.status_code == 200
    resp = await app_client.client.get("/api/audit", params={"limit": 501})
    assert resp.status_code == 422


async def test_e26_readonly_no_write_methods(app_client):
    """S6 只读面：E26 不提供任何写方法（PUT/POST/DELETE → 405/404）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.put("/api/audit", json={}, headers=ORIGIN)
    assert resp.status_code in (405, 404)
    resp = await ns.client.post("/api/audit", json={}, headers=ORIGIN)
    assert resp.status_code in (405, 404)
    resp = await ns.client.delete("/api/audit")
    assert resp.status_code in (403, 404, 405)  # 403 = CSRF Origin 拦截先于路由（S4 生效即合规）
