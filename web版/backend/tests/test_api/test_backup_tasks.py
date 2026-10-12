"""API 集成：E34 列表/详情、E35 增改删、E36 dry-run（选项② R-026，增量设计 §2.1）。

覆盖任务书验收：404 REPO_NOT_FOUND（repo 作用域）、409 TASK_RUNNING（运行中拒绝删除）、
删除后 jobs.task_id 置空而 jobs 行保留（外键 ON DELETE SET NULL）、dry_run_pending
门（E36 预览成功清除/源变更重置）、tag 服务端生成只读、审计三动作埋点。
E36 的 restic 子进程以预录输出替身覆盖（与 test_sources.py 同款手段，路由/映射/
白名单全走真实代码路径）。
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import ORIGIN, finish_bootstrap

# ---------------------------------------------------------------- 测试替身（E36 dry-run）

_DRY_RUN_LINES = [
    '{"message_type":"status","percent_done":0.0,"total_files":2,"files_done":0,"total_bytes":512,"bytes_done":0}',
    '{"message_type":"verbose_status","action":"modified","item":"/host/etc/nginx/nginx.conf",'
    '"duration":0,"data_size":512,"size":512,"metadata_time":0,"metadata_size":100}',
]


class _FakeDryRunProcess:
    """ResticProcess 替身：按预录 JSON 行产出 dry-run 输出并 tee 到日志文件。"""

    def __init__(self, lines: list[str], log_path: Path, exit_code: int = 0) -> None:
        self._lines = lines
        self._log_path = log_path
        self._exit_code = exit_code

    async def lines(self):  # noqa: ANN201
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("ab") as sink:
            for line in self._lines:
                sink.write((line + "\n").encode("utf-8"))
                sink.flush()
                yield line

    async def wait(self) -> int:
        return self._exit_code

    def terminate(self) -> None:  # pragma: no cover - 本测试不触达
        pass

    def kill(self) -> None:  # pragma: no cover
        pass


def _patch_restic_spawn(ns, lines: list[str], exit_code: int = 0) -> list[list[str]]:
    """替身 spawn：记录 argv 供白名单断言。返回 argv 收集器。"""
    argv_log: list[list[str]] = []

    async def _spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        argv_log.append(list(argv))
        return _FakeDryRunProcess(lines, Path(log_path), exit_code)

    ns.app.state.restic.spawn = _spawn  # 实例级替身（app.state 可变）
    return argv_log


async def _create_task(ns, **overrides) -> dict:
    """经 API 创建任务并返回响应体（缺省最小合法负载）。"""
    payload = {"name": "每日全量", "sources": ["/etc"]}
    payload.update(overrides)
    resp = await ns.client.post("/api/repos/default/backup-tasks", json=payload, headers=ORIGIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _insert_job(ns, job_id: str, status: str, *, task_id: int | None = None) -> None:
    """直接落一条任务行（绕过 runner，保证确定性）。"""
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


# ---------------------------------------------------------------- repo 作用域与鉴权（E6/S4）

async def test_repo_scope_non_default_returns_404(app_client):
    """repo_id 仅接受 default；其余 404 REPO_NOT_FOUND（data 带 repo_id）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/repos/other/backup-tasks")
    assert resp.status_code == 404
    assert resp.json()["code"] == "REPO_NOT_FOUND"
    assert resp.json()["data"]["repo_id"] == "other"

    resp = await app_client.client.post(
        "/api/repos/other/backup-tasks",
        json={"name": "x", "sources": ["/etc"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "REPO_NOT_FOUND"
    assert resp.json()["data"]["repo_id"] == "other"


async def test_backup_tasks_routes_require_session(app_client):
    """新端点挂 require_session + require_bootstrap_done（S4）。"""
    resp = await app_client.client.get("/api/repos/default/backup-tasks")
    assert resp.status_code == 401


# ---------------------------------------------------------------- E35 创建

async def test_e35_create_returns_201_with_server_generated_tag(app_client):
    """创建：tag 服务端生成 task-<id>、dry_run_pending=1、display 派生正确。"""
    ns = app_client
    await finish_bootstrap(ns)
    body = await _create_task(ns, cron="0 3 * * *")

    assert body["id"] >= 1
    assert body["name"] == "每日全量"
    assert body["repo_id"] == "default"
    assert body["tag"] == f"task-{body['id']}"  # 自动生成只读（共享知识 §9.1）
    assert body["enabled"] is True
    assert body["sources"] == ["/host/etc"]  # 容器形态入库
    assert body["display_sources"] == ["/etc"]  # display 派生出 API
    assert body["dry_run_pending"] is True  # 创建即置 1（R-026-08）
    assert body["legacy_policy_id"] is None  # greenfield 恒 None
    assert body["cron"] == "0 3 * * *"

    audit = ns.app.state.audit
    rows, total = await audit.query(action="task.create")
    assert total == 1
    assert rows[0].target == str(body["id"])


async def test_e35_create_rejects_empty_name_422(app_client):
    """name 空 → 422 VALIDATION_ERROR（任务域必填，策略域 DEFAULT_NAME 不适用）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.post(
        "/api/repos/default/backup-tasks", json={"name": "", "sources": ["/etc"]}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e35_create_rejects_bad_cron_and_source_422(app_client):
    """cron 字段数非法 / 备份源越界 → 422（与策略页同规则，共享校验委托）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.post(
        "/api/repos/default/backup-tasks",
        json={"name": "x", "sources": ["/etc"], "cron": "0 3 *"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422

    resp = await app_client.client.post(
        "/api/repos/default/backup-tasks", json={"name": "x", "sources": []}, headers=ORIGIN
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------- E34 列表 / E34' 详情

async def test_e34_list_cards_derive_never_run_and_next_fire(app_client):
    """列表：派生状态 never_run、dry_run_pending、next_run_at（cron 推导）、分页壳。"""
    ns = app_client
    await finish_bootstrap(ns)
    first = await _create_task(ns, cron="0 3 * * *")
    second = await _create_task(ns, name="仅手动")  # 无 cron → 仅手动

    resp = await ns.client.get("/api/repos/default/backup-tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0

    card = next(item for item in body["items"] if item["id"] == first["id"])
    assert card["tag"] == f"task-{first['id']}"
    assert card["status"] == "never_run"  # 无 backup 历史（§1.2 派生）
    assert card["last_result"] is None
    assert card["running_job_id"] is None
    assert card["dry_run_pending"] is True
    assert card["prune_due"] is False  # 从未成功：无可清理对象
    assert isinstance(card["next_run_at"], str)  # 调度器 cron 推导

    manual = next(item for item in body["items"] if item["id"] == second["id"])
    assert manual["next_run_at"] is None  # 无 cron 且无全局缺省 → 仅手动


async def test_e34_card_reflects_running_job_and_last_result(app_client):
    """卡片聚合：活跃 job → running_job_id/status；最近 backup 终态 → last_result。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await _create_task(ns, cron="0 3 * * *")
    await _insert_job(ns, "job-done-01", "success", task_id=task["id"])
    await _insert_job(ns, "job-run-01", "running", task_id=task["id"])

    resp = await ns.client.get("/api/repos/default/backup-tasks")
    card = resp.json()["items"][0]
    assert card["status"] == "running"  # 活跃 job 优先于终态
    assert card["running_job_id"] == "job-run-01"
    assert card["last_result"] == "success"  # 最近一次 backup 终态


async def test_e34_detail_returns_full_fields(app_client):
    """E34' 详情/编辑预填：全字段 + display 派生 + tag 只读。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns, cron="0 3 * * *", keep_daily=7)

    resp = await ns.client.get(f"/api/repos/default/backup-tasks/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["keep_daily"] == 7
    assert body["sources"] == ["/host/etc"]
    assert body["display_sources"] == ["/etc"]
    assert body["dry_run_pending"] is True


async def test_e34_detail_missing_task_404(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/repos/default/backup-tasks/999")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- E35' 更新

async def test_e35_update_resets_dry_run_pending_on_source_change(app_client):
    """US-026-02 AC2：E36 清除后，源集合变更 → dry_run_pending 重新置 1；未变更则保持。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns)

    # E36 预览成功 → 清除首跑门
    _patch_restic_spawn(ns, _DRY_RUN_LINES, exit_code=0)
    resp = await ns.client.post(
        f"/api/repos/default/backup-tasks/{created['id']}/dry-run", json={}, headers=ORIGIN
    )
    assert resp.status_code == 200
    assert resp.json()["error"] is None

    detail = (
        await ns.client.get(f"/api/repos/default/backup-tasks/{created['id']}")
    ).json()
    assert detail["dry_run_pending"] is False

    # 同源更新：不清不置（保持现值）
    resp = await ns.client.put(
        f"/api/repos/default/backup-tasks/{created['id']}",
        json={"name": "改名不变源", "sources": ["/etc"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "改名不变源"
    assert resp.json()["dry_run_pending"] is False

    # 源集合变更：重新置 1
    resp = await ns.client.put(
        f"/api/repos/default/backup-tasks/{created['id']}",
        json={"name": "改名不变源", "sources": ["/etc", "/home"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    assert resp.json()["dry_run_pending"] is True
    assert resp.json()["sources"] == ["/host/etc", "/host/home"]

    rows, total = await ns.app.state.audit.query(action="task.update")
    assert total == 2


async def test_e35_update_never_changes_tag(app_client):
    """tag 永不被写（共享知识 §9.1：防 forget --tag 误删他任务快照）。

    API 契约层即不接收 tag 字段（TaskUpsertRequest 无此字段，extra=forbid → 422）；
    更新后 tag 恒为创建时的 ``task-<id>``。
    """
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns)

    resp = await ns.client.put(
        f"/api/repos/default/backup-tasks/{created['id']}",
        json={"name": "新名", "sources": ["/etc"], "tag": "hacked-tag"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422  # 未知字段 tag 被拒（API 不接收）

    resp = await ns.client.put(
        f"/api/repos/default/backup-tasks/{created['id']}",
        json={"name": "新名", "sources": ["/etc"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    assert resp.json()["tag"] == f"task-{created['id']}"


async def test_e35_update_missing_task_404(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.put(
        "/api/repos/default/backup-tasks/999",
        json={"name": "x", "sources": ["/etc"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- E35'' 删除

async def test_e35c_delete_unbinds_jobs_but_keeps_rows(app_client):
    """US-026-03 AC1：删除任务 → jobs.task_id 置空（外键 SET NULL），jobs 行保留。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns)
    await _insert_job(ns, "job-hist-01", "success", task_id=created["id"])

    resp = await ns.client.delete(f"/api/repos/default/backup-tasks/{created['id']}", headers=ORIGIN)
    assert resp.status_code == 200
    assert resp.json()["code"] == "ok"

    job = await ns.app.state.job_repo.get("job-hist-01")
    assert job is not None  # 行保留
    assert job.task_id is None  # 脱离关联
    assert (await ns.app.state.task_repo.get(created["id"])) is None

    rows, total = await ns.app.state.audit.query(action="task.delete")
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["name"] == "每日全量"
    assert detail["tag"] == f"task-{created['id']}"


async def test_e35c_delete_running_task_returns_409(app_client):
    """US-026-03 AC5：活跃 job 存在 → 409 TASK_RUNNING（data 带 running_job_id/task_name）。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns, name="在跑任务")
    await _insert_job(ns, "job-block-01", "running", task_id=created["id"])

    resp = await ns.client.delete(f"/api/repos/default/backup-tasks/{created['id']}", headers=ORIGIN)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "TASK_RUNNING"
    assert body["data"]["running_job_id"] == "job-block-01"
    assert body["data"]["task_name"] == "在跑任务"

    # 任务仍在（删除被拒）
    assert (await ns.app.state.task_repo.get(created["id"])) is not None


async def test_e35c_delete_missing_task_404(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.delete("/api/repos/default/backup-tasks/999", headers=ORIGIN)
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------- E36 dry-run

async def test_e36_dry_run_success_clears_pending(app_client):
    """E36 预览成功（无 error）→ dry_run_pending 清 0（R-026-08 服务端凭证）。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns)
    argv_log = _patch_restic_spawn(ns, _DRY_RUN_LINES, exit_code=0)

    resp = await ns.client.post(
        f"/api/repos/default/backup-tasks/{created['id']}/dry-run", json={}, headers=ORIGIN
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["truncated"] is False
    assert len(body["items"]) == 1
    assert body["items"][0]["display_path"] == "/etc/nginx/nginx.conf"

    # 缺省 body 取任务存量 sources；--dry-run 走 ResticCmd 白名单（S2）
    assert "--dry-run" in argv_log[0]
    assert "/host/etc" in argv_log[0]

    detail = (await ns.client.get(f"/api/repos/default/backup-tasks/{created['id']}")).json()
    assert detail["dry_run_pending"] is False


async def test_e36_dry_run_restic_failure_keeps_pending(app_client):
    """restic 非 0 → 200 + error 字段（禁止静默）；dry_run_pending 保持。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns)
    _patch_restic_spawn(ns, ['{"message_type":"error","error":"repository not initialized"}'], exit_code=1)

    resp = await ns.client.post(
        f"/api/repos/default/backup-tasks/{created['id']}/dry-run", json={}, headers=ORIGIN
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]
    assert body["items"] == []

    detail = (await ns.client.get(f"/api/repos/default/backup-tasks/{created['id']}")).json()
    assert detail["dry_run_pending"] is True


async def test_e36_dry_run_body_overrides_task_sources(app_client):
    """请求体覆盖存量 sources（支撑「源集合变更后重新预览」）。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns)
    argv_log = _patch_restic_spawn(ns, _DRY_RUN_LINES, exit_code=0)

    resp = await ns.client.post(
        f"/api/repos/default/backup-tasks/{created['id']}/dry-run",
        json={"sources": ["/home"], "excludes": []},
        headers=ORIGIN,
    )
    assert resp.status_code == 200
    assert "/host/home" in argv_log[0]
    assert "/host/etc" not in argv_log[0]


async def test_e36_dry_run_missing_task_404(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos/default/backup-tasks/999/dry-run", json={}, headers=ORIGIN
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_e36_dry_run_out_of_prefix_sources_400(app_client):
    """越界 sources → 400 PATH_OUT_OF_PREFIX（S1，与 E31 同口径）。"""
    ns = app_client
    await finish_bootstrap(ns)
    created = await _create_task(ns)
    _patch_restic_spawn(ns, _DRY_RUN_LINES, exit_code=0)

    resp = await ns.client.post(
        f"/api/repos/default/backup-tasks/{created['id']}/dry-run",
        json={"sources": ["/etc\x00x"]},
        headers=ORIGIN,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "PATH_OUT_OF_PREFIX"
