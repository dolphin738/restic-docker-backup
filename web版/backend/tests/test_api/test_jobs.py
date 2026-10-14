"""API 集成：E12 触发冲突 409、E13/E14 列表详情、E16 取消、E27 日志尾部。"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap


async def _make_running_job(ns, job_id: str = "job-running-01") -> str:
    """直接落一条 running 任务（绕过 runner，保证确定性冲突）。"""
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    await ns.app.state.job_repo.create(
        job_id=job_id,
        job_type="backup",
        trigger="manual",
        log_path=str(settings.jobs_dir / f"{job_id}.log"),
        status="running",
    )
    return job_id


async def test_e12_invalid_type_422(app_client):
    await finish_bootstrap(app_client)
    resp = await app_client.client.post(
        "/api/jobs", json={"type": "bogus-type"}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e12_conflict_returns_409_not_queued(app_client):
    """manual 触发遇 running/pending → 409 JOB_CONFLICT（拒绝不排队）。"""
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_running_job(ns)

    resp = await ns.client.post(
        "/api/jobs", json={"type": "backup", "policy_id": 1}, headers=ORIGIN
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "JOB_CONFLICT"
    assert body["data"]["running_job_id"] == job_id


async def test_e12_accept_returns_202(app_client):
    """无冲突时 202 受理（后台执行会因 restic 二进制缺失转 failed，不在本断言范围）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post("/api/jobs", json={"type": "check"}, headers=ORIGIN)
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"]
    assert body["type"] == "check"
    assert body["status"] == "pending"


async def test_e14_job_detail_and_404(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_running_job(ns)

    resp = await ns.client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id
    assert body["status"] == "running"
    assert body["type"] == "backup"
    assert body["trigger"] == "manual"
    assert body["last_event_id"] == 0
    assert body["log_path"].endswith(f"{job_id}.log")

    resp = await ns.client.get("/api/jobs/no-such-job")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_e13_explicit_active_filter(app_client):
    """显式 ``status=running,pending`` 仅返回进行中（前端 ``listActiveJobs`` 的既有语义）。"""
    ns = app_client
    await finish_bootstrap(ns)
    running_id = await _make_running_job(ns)
    await ns.app.state.job_repo.create(
        job_id="job-done",
        job_type="check",
        trigger="manual",
        log_path="/tmp/job-done.log",
        status="success",
    )

    resp = await ns.client.get("/api/jobs", params={"status": "running,pending"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [running_id]

    resp = await ns.client.get("/api/jobs", params={"status": "success"})
    assert resp.status_code == 200  # P2 起全状态合法（原 P1 门禁 422 已放开）


async def test_e13_default_returns_full_history(app_client):
    """缺省（不传 status）＝全量历史（P2 语义；前端活跃列表已显式传参，无隐式依赖）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _make_running_job(ns)
    await ns.app.state.job_repo.create(
        job_id="job-done",
        job_type="check",
        trigger="manual",
        log_path="/tmp/job-done.log",
        status="success",
    )

    resp = await ns.client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2  # running + success（终态不再被门禁挡掉）
    assert {item["status"] for item in body["items"]} == {"running", "success"}


async def test_e13_status_whitelist_still_enforced(app_client):
    """非法状态值仍 422（白名单放开至全状态，不是取消校验）。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/jobs", params={"status": "success,bogus"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["data"]["illegal"] == ["bogus"]


async def test_e13_type_filter(app_client):
    """``type`` 维度（Q13）：按任务类型筛选，白名单外 422。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _make_running_job(ns)  # backup
    await ns.app.state.job_repo.create(
        job_id="job-check-1",
        job_type="check",
        trigger="manual",
        log_path="/tmp/job-check-1.log",
        status="success",
    )

    resp = await ns.client.get("/api/jobs", params={"type": "check"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["type"] == "check"

    resp = await ns.client.get("/api/jobs", params={"type": "bogus"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e13_started_range_filter_normalized_to_utc(app_client):
    """``started_from``/``started_to`` 时间范围（Q13）：无时区按 UTC，带时区归一（C8）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.job_repo.create(
        job_id="job-early",
        job_type="backup",
        trigger="manual",
        log_path="/tmp/job-early.log",
        status="success",
    )
    await ns.app.state.job_repo.update_status(
        "job-early", "success", started_at="2026-10-01T00:00:00Z"
    )
    await ns.app.state.job_repo.create(
        job_id="job-late",
        job_type="backup",
        trigger="manual",
        log_path="/tmp/job-late.log",
        status="success",
    )
    await ns.app.state.job_repo.update_status(
        "job-late", "success", started_at="2026-10-10T00:00:00Z"
    )

    # 下界：只命中 job-late（无时区按 UTC）
    resp = await ns.client.get("/api/jobs", params={"started_from": "2026-10-05T00:00:00"})
    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["items"]] == ["job-late"]

    # 带时区下界（+08:00 等价 2026-10-05T00:00Z）→ 同一结果（C8 归一）
    resp = await ns.client.get(
        "/api/jobs", params={"started_from": "2026-10-05T08:00:00+08:00"}
    )
    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["items"]] == ["job-late"]

    # 上界：只命中 job-early
    resp = await ns.client.get("/api/jobs", params={"started_to": "2026-10-05T00:00:00Z"})
    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["items"]] == ["job-early"]

    # 非法时间 → 422
    resp = await ns.client.get("/api/jobs", params={"started_from": "not-a-time"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e13_limit_upper_bound_500(app_client):
    """``limit`` 上限对齐 E26 的 500（Q13 基线）；501 → 422。"""
    await finish_bootstrap(app_client)
    resp = await app_client.client.get("/api/jobs", params={"limit": 500})
    assert resp.status_code == 200
    resp = await app_client.client.get("/api/jobs", params={"limit": 501})
    assert resp.status_code == 422


async def test_e16_cancel_running_then_409_again(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_running_job(ns)

    resp = await ns.client.post(f"/api/jobs/{job_id}/cancel", headers=ORIGIN)
    assert resp.status_code == 202

    resp = await ns.client.get(f"/api/jobs/{job_id}")
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["exit_code"] == -1

    # 已终态：再次取消 → 409 NOT_CANCELLABLE
    resp = await ns.client.post(f"/api/jobs/{job_id}/cancel", headers=ORIGIN)
    assert resp.status_code == 409
    assert resp.json()["code"] == "NOT_CANCELLABLE"


async def test_e16_cancel_missing_job_404(app_client):
    await finish_bootstrap(app_client)
    resp = await app_client.client.post("/api/jobs/no-such-job/cancel", headers=ORIGIN)
    assert resp.status_code == 404


async def test_e27_log_tail(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_running_job(ns)
    log_path = ns.app.state.settings.jobs_dir / f"{job_id}.log"
    log_path.write_bytes(b"line-1\nline-2\nline-3\n")

    resp = await ns.client.get(f"/api/jobs/{job_id}/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "line-1\nline-2\nline-3\n"
    assert body["truncated"] is False

    resp = await ns.client.get(f"/api/jobs/{job_id}/log", params={"tail_bytes": 7})
    body = resp.json()
    assert body["content"] == "line-3\n"[0:7] or body["content"].endswith("line-3\n")
    assert body["truncated"] is True


async def test_e27_log_missing_file_empty(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    job_id = await _make_running_job(ns)
    resp = await ns.client.get(f"/api/jobs/{job_id}/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == ""
    assert body["tail_bytes"] == 0


async def test_jobs_routes_require_session(app_client):
    resp = await app_client.client.get("/api/jobs")
    assert resp.status_code == 401
    resp = await app_client.client.post("/api/jobs", json={"type": "check"}, headers=ORIGIN)
    assert resp.status_code == 401


async def test_e12_unknown_task_id_returns_422_not_500(app_client):
    """非法 ``task_id`` → 422 VALIDATION_ERROR（Q1：不得落到 500）。

    缺陷背景：``task_id`` 原先只校验与 ``policy_id`` 互斥，不存在性校验；传入不存在
    的 id 时 ``JobRunner`` 落库撞 ``jobs.task_id`` 外键抛 ``sqlite3.IntegrityError``，
    未捕获即 HTTP 500。参数类错误统一走 422，``data.task_id`` 便于前端定位。
    """
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/jobs", json={"type": "backup", "task_id": 999999}, headers=ORIGIN
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["data"]["task_id"] == 999999


async def test_e12_known_task_id_still_accepted(app_client):
    """对照组：存在的 ``task_id`` 照常 202 受理（新校验不得误伤合法请求）。"""
    ns = app_client
    await finish_bootstrap(ns)
    task = await ns.app.state.task_repo.create(
        {"name": "合法任务", "enabled": 1, "sources": ["/host/srv"], "excludes": []}
    )
    resp = await ns.client.post(
        "/api/jobs", json={"type": "backup", "task_id": task.id}, headers=ORIGIN
    )
    assert resp.status_code == 202


async def test_e12_task_id_and_policy_id_mutex_still_422(app_client):
    """互斥校验优先：``task_id`` + ``policy_id`` 同传仍是既有的 422 互斥语义。

    新加的存在性校验不得抢在互斥校验之前改变语义（互斥是入参组合错误，与 id 是否存在无关）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    task = await ns.app.state.task_repo.create(
        {"name": "互斥任务", "enabled": 1, "sources": ["/host/srv"], "excludes": []}
    )
    resp = await ns.client.post(
        "/api/jobs", json={"type": "backup", "task_id": task.id, "policy_id": 1}, headers=ORIGIN
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "互斥" in body["message"]
    assert body["data"]["task_id"] == task.id
    assert body["data"]["policy_id"] == 1


async def test_e12_unknown_policy_id_returns_422_not_500(app_client):
    """非法 ``policy_id`` → 422 VALIDATION_ERROR（与 task_id 侧同口径，不得 500）。

    同 Q1：``policy_id`` 原先也无存在性校验，不存在时落库撞 ``jobs.policy_id`` 外键
    （``0001_init.sql:71``）抛 ``sqlite3.IntegrityError`` → 500。两条路径语义同为
    「引用了不存在的实体」，错误语义必须一致（否则同一支 API 一会 422 一会 500）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/jobs", json={"type": "backup", "policy_id": 999999}, headers=ORIGIN
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["data"]["policy_id"] == 999999


async def test_e12_known_policy_id_still_accepted(app_client):
    """对照组：存在的 ``policy_id``（缺省策略 id=1）照常 202 受理。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/jobs", json={"type": "backup", "policy_id": 1}, headers=ORIGIN
    )
    assert resp.status_code == 202


async def test_e12_manual_backup_books_last_run_at(app_client):
    """手动「立即运行」backup → 记账 ``last_run_at``（与调度触发路径同口径）。

    缺陷背景：``last_run_at`` 原先只在调度器 ``_fire_backup`` 写入，只跑过手动备份的
    任务卡片显示「上次运行：无」，与跑过调度备份的任务「上次运行：[成功]」并存。
    """
    ns = app_client
    await finish_bootstrap(ns)
    task = await ns.app.state.task_repo.create(
        {"name": "手动记账", "enabled": 1, "sources": ["/host/srv"], "excludes": []}
    )
    assert (await ns.app.state.task_repo.get(task.id)).last_run_at is None

    resp = await ns.client.post(
        "/api/jobs", json={"type": "backup", "task_id": task.id}, headers=ORIGIN
    )
    assert resp.status_code == 202
    assert (await ns.app.state.task_repo.get(task.id)).last_run_at is not None


async def test_e12_manual_restore_does_not_book_last_run_at(app_client):
    """手动 restore **不**记账：``last_run_at`` 语义是「上次备份时间」，不得被污染。

    若 restore/check/forget/unlock/init/dump 也写，该字段会退化成「上次任何作业时间」，
    E34 卡片的「上次运行」与 prune 到期判定口径随之失真。
    """
    ns = app_client
    await finish_bootstrap(ns)
    task = await ns.app.state.task_repo.create(
        {"name": "恢复不记账", "enabled": 1, "sources": ["/host/srv"], "excludes": []}
    )

    resp = await ns.client.post(
        "/api/jobs",
        json={"type": "restore", "task_id": task.id, "snapshot_id": "snap-0001"},
        headers=ORIGIN,
    )
    assert resp.status_code == 202
    assert (await ns.app.state.task_repo.get(task.id)).last_run_at is None


async def test_job_id_is_ulid_sorted(app_client):
    """E12 生成的任务 id 为 ULID（26 字符，可排序）。"""
    from ulid import ULID

    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post("/api/jobs", json={"type": "check"}, headers=ORIGIN)
    job_id = resp.json()["id"]
    assert len(job_id) == 26
    ULID.from_str(job_id)  # 不抛错即合法 ULID
