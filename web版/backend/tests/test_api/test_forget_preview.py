"""API 集成：E21 forget 预览（R-016 预览-确认流前半）。

restic 探测以 ``run_json`` 替身覆盖（预录 ``forget --dry-run --json`` NDJSON 分组输出），
路由/解析/白名单全走真实代码路径。断言点：

- **只读**：argv 必含 ``--dry-run``、必不含 ``--prune``（G3：预览绝不落裁剪）；
- **G3 服务端兜底**：无任何 keep_* 规则 → 422；
- **探测失败不静默**：restic 非 0 退出 → 500 RESTIC_FAILED，data.reason 可操作；
- **参数回显**：``params`` 与 E12 执行侧消费键同名（tags 为列表）——确认提交后
  预览与实际结果一致（R-016 验收口径）。
"""

from __future__ import annotations

import json

from conftest import ORIGIN, finish_bootstrap

from app.services.restic import ProbeResult

SNAPSHOT_A = "a" * 64
SNAPSHOT_B = "b" * 64


def _forget_group_line(keep_ids: list[str], remove_ids: list[str]) -> str:
    """构造一条 ``forget --json`` 分组 NDJSON 行（形状对齐 restic 真实输出）。"""

    def _snapshot(snapshot_id: str) -> dict:
        return {
            "time": "2026-10-01T03:00:00+08:00",
            "tree": "0" * 64,
            "paths": ["/host/etc"],
            "hostname": "nas",
            "tags": ["task-1"],
            "id": snapshot_id,
            "short_id": snapshot_id[:8],
        }

    return json.dumps(
        {
            "keep": [_snapshot(item) for item in keep_ids],
            "remove": [_snapshot(item) for item in remove_ids],
            "reasons": [{"when": "2026-10-14", "keep": {"daily": 7}}],
        }
    )


FORGET_LINES = [
    _forget_group_line([SNAPSHOT_A], [SNAPSHOT_B]),
]


def _patch_run_json(ns, *, lines: list[str], exit_code: int = 0) -> list[list[str]]:
    """替身 run_json：记录 argv 供只读断言。返回 argv 收集器。"""
    argv_log: list[list[str]] = []

    async def _run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return ProbeResult(exit_code=exit_code, lines=list(lines), truncated=False)

    ns.app.state.restic.run_json = _run_json
    return argv_log


async def _setup_repo(ns) -> None:
    """E21 前置：配置仓库地址（R-023 后 repos 表事实源）。"""
    await ns.app.state.repos_repo.update("default", url="/repo")


async def test_e21_preview_returns_remove_list(app_client):
    """预览返回将被裁剪的快照列表（paths 映射 display 形态）+ 参数回显。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=FORGET_LINES)

    resp = await ns.client.post(
        "/api/forget/preview",
        json={"tag": "task-1", "keep_daily": 7, "keep_weekly": 4},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["remove_count"] == 1
    assert body["keep_count"] == 1
    assert body["group_count"] == 1
    assert body["remove"][0]["id"] == SNAPSHOT_B
    assert body["remove"][0]["paths"] == ["/etc"]  # /host 前缀剥离（E17 同口径）
    # 参数回显与执行侧消费键同名（tags 是列表），确认提交 E12 后语义一致
    assert body["params"]["tags"] == ["task-1"]
    assert body["params"]["keep_daily"] == 7
    assert body["params"]["keep_weekly"] == 4
    assert body["params"]["group_by"] == "host,paths,tags"
    assert body["params"]["prune"] is True
    # 只读铁律：argv 必含 --dry-run、必不含 --prune；tag 经白名单传给 restic
    argv = argv_log[0]
    assert "--dry-run" in argv
    assert "--prune" not in argv
    assert argv[argv.index("--tag") + 1] == "task-1"
    assert argv[argv.index("--keep-daily") + 1] == "7"


async def test_e21_preview_without_keep_rule_422(app_client):
    """G3 兜底：无任何保留规则 → 422（不进 restic 探测）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    argv_log = _patch_run_json(ns, lines=FORGET_LINES)

    resp = await ns.client.post("/api/forget/preview", json={"tag": "task-1"}, headers=ORIGIN)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert argv_log == []  # 校验前置，restic 一次都不跑


async def test_e21_restic_failure_maps_to_500_with_reason(app_client):
    """探测失败不静默：restic 非 0 退出 → 500 RESTIC_FAILED，data.reason 可操作。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _setup_repo(ns)
    _patch_run_json(ns, lines=["unable to create lock"], exit_code=1)

    resp = await ns.client.post(
        "/api/forget/preview", json={"keep_daily": 7}, headers=ORIGIN
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "RESTIC_FAILED"
    assert "锁残留" in body["data"]["reason"]


async def test_e21_requires_session(app_client):
    """未登录 → 401（与其他业务端点同口径）。"""
    ns = app_client
    resp = await ns.client.post("/api/forget/preview", json={"keep_daily": 7}, headers=ORIGIN)
    assert resp.status_code == 401
