"""API 集成：A3 快照打/去标签（POST /api/snapshots/tag）。

restic 探测以 ``run_json`` 替身覆盖（预录 ``tag`` 的 changed/summary JSON 输出），
路由/解析/白名单/审计全走真实代码路径。断言点：

- **成功**：返回 changed_count + 逐条新旧 id 映射；argv 含 --add/--remove 且快照 id 在末尾；
- **业务校验前置**：add/remove 皆空 → 422（不进 restic）；
- **高危拦截**：无 snapshot_ids 且无 tag_filter/host → 422（避免改全部快照）；
- **快照 id 格式**：非法十六进制 → 422；
- **过滤维度**：tag_filter + host → argv 含 --tag/--host；
- **探测失败不静默**：restic 非 0 退出 → 500 RESTIC_FAILED，data.reason 可操作；
- **鉴权**：未登录 → 401。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

SNAPSHOT_A = "a" * 64
SNAPSHOT_B = "b" * 64

OLD_ID = "c" * 64
NEW_ID = "d" * 64


def _tag_lines() -> list[str]:
    """预录 restic tag 的 changed + summary 输出（对齐 cmd_tag.go 真实形态）。"""
    return [
        '{"message_type":"changed","old_snapshot_id":"'
        + OLD_ID
        + '","new_snapshot_id":"'
        + NEW_ID
        + '"}',
        '{"message_type":"summary","changed_snapshots":1}',
    ]


def _patch_run_json(ns, *, lines: list[str], exit_code: int = 0) -> list[list[str]]:
    from app.services.restic import ProbeResult

    argv_log: list[list[str]] = []

    async def _run_json_real(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return ProbeResult(exit_code=exit_code, lines=list(lines), truncated=False)

    ns.app.state.restic.run_json = _run_json_real
    return argv_log


async def _setup_repo(ns) -> None:
    await ns.app.state.repos_repo.update("default", url="/repo")


async def test_tag_success_returns_changed_mapping(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=_tag_lines())

    resp = await ns.client.post(
        "/api/snapshots/tag",
        json={"snapshot_ids": [SNAPSHOT_A], "add": ["prod"], "remove": ["legacy"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed_count"] == 1
    assert body["changed"][0]["old_snapshot_id"] == OLD_ID
    assert body["changed"][0]["new_snapshot_id"] == NEW_ID
    # argv 含 --add/--remove + 快照 id 在末尾
    argv = argv_log[0]
    assert "--add" in argv and argv[argv.index("--add") + 1] == "prod"
    assert "--remove" in argv and argv[argv.index("--remove") + 1] == "legacy"
    assert argv[-1] == SNAPSHOT_A


async def test_tag_empty_add_remove_422(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=_tag_lines())

    resp = await ns.client.post(
        "/api/snapshots/tag", json={"snapshot_ids": [SNAPSHOT_A]}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert argv_log == []  # 校验前置，restic 不跑


async def test_tag_no_target_422(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=_tag_lines())

    # 既无 snapshot_ids 也无 tag_filter/host → 高危拦截 422
    resp = await ns.client.post(
        "/api/snapshots/tag", json={"add": ["prod"]}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert argv_log == []


async def test_tag_invalid_snapshot_id_422(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=_tag_lines())

    resp = await ns.client.post(
        "/api/snapshots/tag",
        json={"snapshot_ids": ["NOT-HEX"], "add": ["prod"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert argv_log == []


async def test_tag_filter_and_host_emit_selection_flags(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=_tag_lines())

    resp = await ns.client.post(
        "/api/snapshots/tag",
        json={"tag_filter": ["task-1"], "host": "nas", "add": ["prod"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    argv = argv_log[0]
    assert "--tag" in argv and argv[argv.index("--tag") + 1] == "task-1"
    assert "--host" in argv and argv[argv.index("--host") + 1] == "nas"


async def test_tag_restic_failure_maps_to_500(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns, lines=["unable to create lock"], exit_code=11)

    resp = await ns.client.post(
        "/api/snapshots/tag",
        json={"snapshot_ids": [SNAPSHOT_A], "add": ["prod"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "RESTIC_FAILED"
    assert "锁残留" in body["data"]["reason"]


async def test_tag_requires_session(app_client):
    ns = app_client
    resp = await ns.client.post(
        "/api/snapshots/tag",
        json={"snapshot_ids": [SNAPSHOT_A], "add": ["prod"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 401
