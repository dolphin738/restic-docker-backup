"""批次② exclude_spec API 契约：持久化往返 + 校验（glob / negate∧icase → 422）。"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

_SPEC = {
    "quick": {
        "extensions": ["log"],
        "nameContains": "cache",
        "largerThan": "100M",
        "ifPresent": [".nobackup"],
        "caches": True,
    },
    "rules": [
        {"pattern": "*.TMP", "negate": False, "icase": True},
        {"pattern": "keep.log", "negate": True, "icase": False},
    ],
}


async def _create(ns, **extra):
    payload = {"name": "带过滤", "sources": ["/etc"], **extra}
    return await ns.client.post("/api/repos/default/backup-tasks", json=payload, headers=ORIGIN)


async def test_exclude_spec_roundtrip(app_client):
    """create 带 exclude_spec → 原样回传，excludes 置空（spec 为唯一事实源）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await _create(ns, exclude_spec=_SPEC)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["exclude_spec"] == _SPEC
    assert body["excludes"] == []

    detail = (await ns.client.get(f"/api/repos/default/backup-tasks/{body['id']}")).json()
    assert detail["exclude_spec"] == _SPEC


async def test_exclude_spec_invalid_glob_422(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    spec = {"rules": [{"pattern": "[abc", "negate": False, "icase": False}]}
    resp = await _create(ns, exclude_spec=spec)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_exclude_spec_negate_icase_422(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    spec = {"rules": [{"pattern": "*.log", "negate": True, "icase": True}]}
    resp = await _create(ns, exclude_spec=spec)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_exclude_spec_bad_size_422(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    spec = {"quick": {"largerThan": "100MB"}}
    resp = await _create(ns, exclude_spec=spec)
    assert resp.status_code == 422
