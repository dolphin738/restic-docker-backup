"""API 集成：批次 D C1 任务级前后钩子（E35 创建 / E35' 更新 / E34' 详情）。

契约冻结点：``pre_cmd`` / ``post_cmd`` 两个字段进 E35 upsert 与 E34' 详情，
空串 = 未配置；坏命令（引号不闭合 / 超长 / 含换行）在写入侧 422，不留到任务提交时。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

_BASE = {"name": "钩子任务", "sources": ["/etc"]}


async def _create(app_client, **overrides) -> dict:
    payload = dict(_BASE)
    payload.update(overrides)
    resp = await app_client.client.post(
        "/api/repos/default/backup-tasks", json=payload, headers=ORIGIN
    )
    return resp, (resp.json() if resp.status_code != 201 else resp.json())


async def test_create_task_with_hooks_returns_them(app_client) -> None:
    await finish_bootstrap(app_client)
    resp, body = await _create(
        app_client, pre_cmd="/opt/hooks/pre.sh --daily", post_cmd="/opt/hooks/post.sh"
    )
    assert resp.status_code == 201, resp.text
    assert body["pre_cmd"] == "/opt/hooks/pre.sh --daily"
    assert body["post_cmd"] == "/opt/hooks/post.sh"


async def test_create_task_without_hooks_defaults_empty(app_client) -> None:
    """不传钩子字段 → 空串（与 cron 的 null 语义区分：空串 = 明确未配置）。"""
    await finish_bootstrap(app_client)
    resp, body = await _create(app_client)
    assert resp.status_code == 201, resp.text
    assert body["pre_cmd"] == ""
    assert body["post_cmd"] == ""


async def test_create_task_rejects_illegal_hook(app_client) -> None:
    """写侧尽早失败（Q4 静默防线）：三类坏命令全部 422，不落库。"""
    await finish_bootstrap(app_client)
    for bad in ('/opt/hooks/pre.sh "oops', "x" * 2000, "pre.sh\nrm -rf /"):
        resp = await app_client.client.post(
            "/api/repos/default/backup-tasks",
            json={**_BASE, "pre_cmd": bad},
            headers=ORIGIN,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_update_task_hooks_roundtrip(app_client) -> None:
    await finish_bootstrap(app_client)
    _resp, created = await _create(app_client)
    task_id = created["id"]

    resp = await app_client.client.put(
        f"/api/repos/default/backup-tasks/{task_id}",
        json={**_BASE, "pre_cmd": "/opt/hooks/freeze.sh", "post_cmd": "/opt/hooks/thaw.sh"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pre_cmd"] == "/opt/hooks/freeze.sh"
    assert resp.json()["post_cmd"] == "/opt/hooks/thaw.sh"

    # 全量语义：更新时不给钩子字段 → 清空（与其余字段同口径）
    resp = await app_client.client.put(
        f"/api/repos/default/backup-tasks/{task_id}", json=_BASE, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pre_cmd"] == ""
    assert resp.json()["post_cmd"] == ""


async def test_task_detail_exposes_hooks(app_client) -> None:
    await finish_bootstrap(app_client)
    _resp, created = await _create(app_client, pre_cmd="/opt/hooks/pre.sh")
    resp = await app_client.client.get(f"/api/repos/default/backup-tasks/{created['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["pre_cmd"] == "/opt/hooks/pre.sh"
    assert resp.json()["post_cmd"] == ""


async def test_unauthenticated_create_is_401(app_client) -> None:
    """钩子字段不引入鉴权旁路（S4）：无 session 的写一律 401 UNAUTHORIZED。"""
    await finish_bootstrap(app_client)
    # Origin 照给（否则先撞 CSRF 403），只清 session cookie → 落到鉴权 401
    app_client.client.cookies.clear()
    resp = await app_client.client.post(
        "/api/repos/default/backup-tasks", json=_BASE, headers=ORIGIN
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"
