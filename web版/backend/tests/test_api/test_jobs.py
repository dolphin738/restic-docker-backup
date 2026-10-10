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


async def test_e13_lists_active_only(app_client):
    """P1 最小版：仅返回 running/pending；终态任务不出现；status=success → 422。"""
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

    resp = await ns.client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [running_id]

    resp = await ns.client.get("/api/jobs", params={"status": "success"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


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


async def test_job_id_is_ulid_sorted(app_client):
    """E12 生成的任务 id 为 ULID（26 字符，可排序）。"""
    from ulid import ULID

    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post("/api/jobs", json={"type": "check"}, headers=ORIGIN)
    job_id = resp.json()["id"]
    assert len(job_id) == 26
    ULID.from_str(job_id)  # 不抛错即合法 ULID
