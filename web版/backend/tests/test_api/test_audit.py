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


async def test_e26_action_multi_prefix_union(app_client):
    """逗号分隔多前缀取**并集**（归档页「操作日志」按动作清单查；单个前缀覆盖不全）。

    ``repo.id_release`` 与 ``rclone.config_restore`` 不共享任何单一前缀，逐个查要发
    N 个请求且翻页失效 → 服务端取并集。断言用**增量**口径（bootstrap 有 repo.* 行）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    audit = ns.app.state.audit

    counts = {}
    for action in ("repo.purge", "repo.id_release", "rclone.config_restore"):
        counts[action] = (await audit.query(action_prefix=action, limit=1))[1]
    # 并集基线：三个前缀各自总数之和（动作名互斥，无重叠）
    union_before = sum(counts.values())

    await audit.record("repo.purge", target="r1", result=OK)
    await audit.record("repo.id_release", target="r2", result=OK)
    await audit.record("rclone.config_restore", target="remote-a", result=OK)
    # 噪声行：前缀清单之外的动作，不得被并集结果收进来
    await audit.record("cache.clear", target="noise", result=OK)

    multi = "repo.purge,repo.id_release,rclone.config_restore"
    resp = await ns.client.get("/api/audit", params={"action": multi, "limit": 500})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == union_before + 3

    actions = [item["action"] for item in body["items"]]
    assert {"repo.purge", "repo.id_release", "rclone.config_restore"} <= set(actions)
    assert "cache.clear" not in actions


async def test_e26_action_multi_prefix_still_escapes_wildcards(app_client):
    """多前缀不松转义对抗：含 ``%`` 的分片仍按字面匹配，不越权。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed(ns)

    resp = await ns.client.get("/api/audit", params={"action": "log%in,restore."})
    assert resp.status_code == 200
    body = resp.json()
    # restore. 前缀命中；log%in 被转义为字面，不命中 login.ok
    assert {item["action"] for item in body["items"]} == {"restore.run"}


async def test_e26_action_multi_prefix_ignores_empty_segments(app_client):
    """空分片（``,,`` / 尾随逗号）被丢弃，不退化成「无过滤全量返回」。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed(ns)

    resp = await ns.client.get(
        "/api/audit", params={"action": ",,restore.,", "limit": 500}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {item["action"] for item in body["items"]} == {"restore.run"}


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


async def test_e26_offset_beyond_total_returns_empty_page(app_client):
    """QA 补测（批次 B）：offset 越过总数 → 200 + 空 items（不 5xx、total 不变）。"""
    ns = app_client
    await finish_bootstrap(ns)
    total = await _audit_total(ns)
    assert total > 0  # bootstrap 审计行已存在，越界偏移才有意义

    resp = await ns.client.get("/api/audit", params={"offset": total + 100_000})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == total
    assert body["offset"] == total + 100_000


async def test_e26_action_prefix_escapes_underscore_and_backslash(app_client):
    """QA 补测（批次 B）：``_`` 与 ``\\`` 同属 LIKE 通配/转义字符，必须转义防越权。

    - ``log_in``（未转义时 ``_`` 通配单字符会命中 ``login.ok``）→ 0 命中；
    - ``login.ok\\``（尾部反斜杠字面量）→ 0 命中（转义后仍按字面前缀匹配）；
    - 对照组：字面前缀 ``login.ok`` 正常命中。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await _seed(ns)

    resp = await ns.client.get("/api/audit", params={"action": "log_in"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0  # ``_`` 是字面下划线，不匹配 ``login.ok``

    resp = await ns.client.get("/api/audit", params={"action": "login.ok\\"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0  # 尾部反斜杠不改变匹配语义、也不得 500

    resp = await ns.client.get("/api/audit", params={"action": "login.ok"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1  # 对照组：合法前缀正常命中


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
