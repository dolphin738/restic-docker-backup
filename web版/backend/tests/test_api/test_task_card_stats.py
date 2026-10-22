"""API 集成：UI 全量改造 P1 —— 任务卡片 ``last_duration_ms`` / ``snapshot_count``。

restic 探测以 ``run_json`` 替身覆盖（预录 ``snapshots --json`` 输出），路由/聚合/降级
全走真实代码路径。断言点：

- **无历史 → null**：无终态 backup（或有终态但缺时间戳）→ ``last_duration_ms=null``，
  不是 0（0 会被读成「跑过且瞬间完成」）；
- **同一次运行**：``last_duration_ms`` 与 ``last_result`` 指向同一个 job（最近终态）；
- **不可用 ≠ 0**：仓库地址未配置 / 快照列表探测失败 → ``snapshot_count=null``；
- **按 tag 分组**：一次快照列表（N 个任务只跑一次 restic）按 ``task.tag`` 计数，
  未命中的任务为 0；
- **30s 缓存**：连续两次列表只跑一次 restic；E30 状态缓存失效时连带失效。
"""

from __future__ import annotations

import json

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.services.restic import ProbeResult
from app.services.snapshot_catalog import invalidate_snapshot_catalog

SNAPSHOT_A = "a" * 64
SNAPSHOT_B = "b" * 64
SNAPSHOT_C = "c" * 64


@pytest.fixture(autouse=True)
def _clean_snapshot_catalog_cache():
    """快照目录缓存是模块级 TTL 字典：用例前后各清一次，杜绝跨用例污染。"""
    invalidate_snapshot_catalog()
    yield
    invalidate_snapshot_catalog()


def _snapshot_line(snapshot_id: str, tags: list[str]) -> str:
    """构造一条 ``snapshots --json`` NDJSON 行（字段形态对齐 fixtures/restic-json）。"""
    return json.dumps(
        {
            "time": "2026-10-01T03:00:00+08:00",
            "tree": "0" * 64,
            "paths": ["/sources/etc"],
            "hostname": "nas",
            "username": "root",
            "tags": tags,
            "id": snapshot_id,
            "short_id": snapshot_id[:8],
        }
    )


def _patch_run_json(ns, *, lines: list[str], exit_code: int = 0) -> list[list[str]]:
    """替身 run_json：记录 argv 供「只跑一次」断言。返回 argv 收集器。"""
    argv_log: list[list[str]] = []

    async def _run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return ProbeResult(exit_code=exit_code, lines=list(lines), truncated=False)

    ns.app.state.restic.run_json = _run_json
    return argv_log


async def _create_task(ns, **overrides) -> dict:
    """经 API 创建任务并返回响应体（缺省最小合法负载）。"""
    payload = {"name": "每日全量", "sources": ["/etc"]}
    payload.update(overrides)
    resp = await ns.client.post("/api/repos/default/backup-tasks", json=payload, headers=ORIGIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _insert_terminal_backup(
    ns,
    job_id: str,
    status: str,
    *,
    task_id: int,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    """落一条**终态** backup 任务行（绕过 runner，保证确定性）。"""
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    await ns.app.state.job_repo.create(
        job_id=job_id,
        job_type="backup",
        trigger="manual",
        log_path=str(settings.jobs_dir / f"{job_id}.log"),
        status=status,
        task_id=task_id,
    )
    await ns.app.state.job_repo.update_status(
        job_id, status, started_at=started_at, finished_at=finished_at
    )


async def _list_cards(ns) -> list[dict]:
    """取任务卡片列表（按 id 升序，便于按创建顺序定位）。"""
    resp = await ns.client.get("/api/repos/default/backup-tasks")
    assert resp.status_code == 200, resp.text
    return sorted(resp.json()["items"], key=lambda item: item["id"])


# ---------------------------------------------------------------- last_duration_ms


async def test_last_duration_ms_null_without_backup_history(app_client):
    """无终态 backup 历史 → null（不是 0）。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns)

    card = (await _list_cards(ns))[0]
    assert card["id"] == task["id"]
    assert card["last_result"] is None
    assert card["last_duration_ms"] is None


async def test_last_duration_ms_null_when_timestamps_missing(app_client):
    """终态 backup 缺 started/finished（老数据）→ null，不用更旧的补齐。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns)
    await _insert_terminal_backup(ns, "job-no-ts-01", "success", task_id=task["id"])

    card = (await _list_cards(ns))[0]
    assert card["last_result"] == "success"
    assert card["last_duration_ms"] is None


async def test_last_duration_ms_matches_latest_terminal_backup(app_client):
    """耗时取**最近一次**终态 backup（与 last_result 同一次运行）。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns)
    # 同秒创建 → ORDER BY created_at DESC, id DESC 由 id 决胜（"job-02-new" > "job-01-old"）
    await _insert_terminal_backup(
        ns,
        "job-01-old",
        "success",
        task_id=task["id"],
        started_at="2026-10-01T00:00:00Z",
        finished_at="2026-10-01T00:10:00Z",  # 600_000 ms（不应被采用）
    )
    await _insert_terminal_backup(
        ns,
        "job-02-new",
        "failed",
        task_id=task["id"],
        started_at="2026-10-02T00:00:00Z",
        finished_at="2026-10-02T00:00:30Z",  # 30_000 ms
    )

    card = (await _list_cards(ns))[0]
    assert card["last_result"] == "failed"  # 与耗时同一条 job
    assert card["last_duration_ms"] == 30_000


async def test_last_duration_ms_per_task_not_shared(app_client):
    """耗时按任务聚合：另一个任务无历史 → 它自己为 null。"""
    ns = app_client
    await finish_bootstrap(ns)
    first = await _create_task(ns)
    second = await _create_task(ns, name="仅手动")
    await _insert_terminal_backup(
        ns,
        "job-only-01",
        "success",
        task_id=first["id"],
        started_at="2026-10-03T00:00:00Z",
        finished_at="2026-10-03T00:00:05Z",
    )

    cards = await _list_cards(ns)
    assert cards[0]["id"] == first["id"]
    assert cards[0]["last_duration_ms"] == 5_000
    assert cards[1]["id"] == second["id"]
    assert cards[1]["last_duration_ms"] is None


# ---------------------------------------------------------------- snapshot_count


async def test_snapshot_count_null_when_repo_unconfigured(app_client):
    """仓库地址未配置 → null 且不跑 restic（不可用 ≠ 0）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _create_task(ns)
    argv_log = _patch_run_json(ns, lines=[_snapshot_line(SNAPSHOT_A, ["task-1"])])

    card = (await _list_cards(ns))[0]
    assert card["snapshot_count"] is None
    assert argv_log == []  # 未配置 → 直接降级，不探测


async def test_snapshot_count_null_when_probe_fails(app_client):
    """快照列表探测失败（restic 非 0 退出）→ null，列表接口仍 200。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    task = await _create_task(ns)
    _patch_run_json(ns, lines=["unable to create lock"], exit_code=1)

    resp = await ns.client.get("/api/repos/default/backup-tasks")
    assert resp.status_code == 200
    card = next(item for item in resp.json()["items"] if item["id"] == task["id"])
    assert card["snapshot_count"] is None


async def test_snapshot_count_groups_by_task_tag(app_client):
    """一次快照列表按 task.tag 分组：命中计数正确、未命中为 0。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    first = await _create_task(ns)
    second = await _create_task(ns, name="带 auto 标签")
    third = await _create_task(ns, name="无快照")

    lines = [
        _snapshot_line(SNAPSHOT_A, [first["tag"]]),
        _snapshot_line(SNAPSHOT_B, [second["tag"], "auto"]),
        _snapshot_line(SNAPSHOT_C, [second["tag"]]),
    ]
    argv_log = _patch_run_json(ns, lines=lines)

    cards = await _list_cards(ns)
    assert cards[0]["id"] == first["id"]
    assert cards[1]["id"] == second["id"]
    assert cards[2]["id"] == third["id"]
    assert cards[0]["snapshot_count"] == 1  # first：1 条
    assert cards[1]["snapshot_count"] == 2  # second：2 条（多 tag 各计一次）
    assert cards[2]["snapshot_count"] == 0  # third：命中列表但无该 tag → 0（不是 null）
    assert len(argv_log) == 1  # 三个任务只跑一次 restic（禁止退化为 N 次）
    assert "snapshots" in argv_log[0]


async def test_snapshot_count_reuses_ttl_cache(app_client):
    """30s 内连续两次列表只跑一次 restic（援 E30 缓存先例）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    task = await _create_task(ns)
    argv_log = _patch_run_json(ns, lines=[_snapshot_line(SNAPSHOT_A, [task["tag"]])])

    assert (await _list_cards(ns))[0]["snapshot_count"] == 1
    assert (await _list_cards(ns))[0]["snapshot_count"] == 1
    assert len(argv_log) == 1


async def test_snapshot_count_refreshes_after_status_cache_invalidation(app_client):
    """E30 状态缓存失效（任务终态/配置写入）连带清快照目录缓存 → 重新探测。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    task = await _create_task(ns)
    argv_log = _patch_run_json(ns, lines=[_snapshot_line(SNAPSHOT_A, [task["tag"]])])

    assert (await _list_cards(ns))[0]["snapshot_count"] == 1
    from app.routers.repos import invalidate_repo_status_cache

    invalidate_repo_status_cache()
    assert (await _list_cards(ns))[0]["snapshot_count"] == 1
    assert len(argv_log) == 2  # 失效后重新取一次


async def test_snapshot_count_skips_probe_when_no_tasks(app_client):
    """B4（owner 2026-10-23 裁决）：任务列表为空时**不做**快照探测。

    动机：P1 把 ``snapshot_count`` 挂在 E34 列表上，但空任务列表仍然会白跑一次
    ``restic snapshots``（首版是 20s 超时，属可感知的无效等待）。短路后空列表零子进程。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    argv_log = _patch_run_json(ns, lines=[_snapshot_line(SNAPSHOT_A, ["task-1"])])

    assert await _list_cards(ns) == []
    assert argv_log == []  # 空列表：一次探测都不发


async def test_snapshot_count_failure_backs_off(app_client):
    """B4：探测失败后进入指数退避——第二次请求不再吃一次超时（argv 不变）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="/repo")
    await _create_task(ns)
    argv_log = _patch_run_json(ns, lines=["unable to create lock"], exit_code=1)

    assert (await _list_cards(ns))[0]["snapshot_count"] is None
    assert len(argv_log) == 1
    # 失败结论按退避 TTL 缓存（首次失败 2× base = 60s）→ 立即再查不会重新探测
    assert (await _list_cards(ns))[0]["snapshot_count"] is None
    assert len(argv_log) == 1
