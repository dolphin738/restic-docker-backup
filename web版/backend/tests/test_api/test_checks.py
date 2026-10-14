"""API 集成：E22 check 结果视图（R-017 批次 B）——类型门禁、解析视图、read_data 形态。"""

from __future__ import annotations

from pathlib import Path

from conftest import ORIGIN, finish_bootstrap

FIXTURES = Path(__file__).parent.parent / "fixtures" / "restic-json"


def _write_log(ns, job_id: str, fixture: str) -> str:
    """把 check fixture 写成任务日志（E22 的解析数据源），返回 log_path。"""
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.jobs_dir / f"{job_id}.log"
    log_path.write_text((FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8")
    return str(log_path)


async def _insert_check_job(ns, job_id: str, *, log_path: str, status: str = "failed") -> None:
    await ns.app.state.job_repo.create(
        job_id=job_id,
        job_type="check",
        trigger="scheduled",
        log_path=log_path,
        status=status,
    )


async def test_e22_requires_session(app_client):
    resp = await app_client.client.get("/api/checks/some-job")
    assert resp.status_code == 401


async def test_e22_missing_job_404(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/checks/no-such-job")
    assert resp.status_code == 404


async def test_e22_non_check_job_422(app_client):
    """类型门禁：非 check 任务 422 VALIDATION_ERROR（§2.4）。"""
    ns = app_client
    await finish_bootstrap(ns)
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    await ns.app.state.job_repo.create(
        job_id="job-backup-1",
        job_type="backup",
        trigger="manual",
        log_path=str(settings.jobs_dir / "job-backup-1.log"),
        status="success",
    )
    resp = await ns.client.get("/api/checks/job-backup-1")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_e22_index_damaged_view(app_client):
    """索引损坏形态：4 条 error 原文逐字保留 + summary 指标（suggest_repair_index）。"""
    ns = app_client
    await finish_bootstrap(ns)
    job_id = "job-check-idx"
    log_path = _write_log(ns, job_id, "check-errors-index.ndjson")
    await _insert_check_job(ns, job_id, log_path=log_path)

    resp = await ns.client.get(f"/api/checks/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    view = body["check_view"]
    assert view["error_count"] == 4
    assert view["num_errors"] == 2  # summary 原值（源码计数口径）
    assert view["broken_packs"] == []
    assert view["suggest_repair_index"] is True
    assert view["suggest_prune"] is False
    assert view["read_data"] is False  # 无提交参数内存 → 回落 False
    # message 逐字保留（tree 头行带换行、index 提示块以前置换行开头）
    assert view["errors"][0]["message"].endswith(":\n")
    assert view["errors"][3]["message"].startswith("\nThe repository index is damaged")
    # JobOut 全字段并存
    assert body["id"] == job_id
    assert body["type"] == "check"
    assert body["log_path"] == log_path


async def test_e22_read_data_damaged_view(app_client):
    """--read-data 失败形态：broken_packs 保序（修复动作数据源）。"""
    ns = app_client
    await finish_bootstrap(ns)
    job_id = "job-check-pack"
    _write_log(ns, job_id, "check-read-data-damaged.ndjson")
    await _insert_check_job(ns, job_id, log_path=str(ns.app.state.settings.jobs_dir / f"{job_id}.log"))

    view = (await ns.client.get(f"/api/checks/{job_id}")).json()["check_view"]
    assert view["num_errors"] == 2
    assert len(view["broken_packs"]) == 2
    assert all(len(pack) == 64 for pack in view["broken_packs"])


async def test_e22_clean_view(app_client):
    """健康形态：零错误 + 空列表（null 归一）+ 空产物日志路径容错。"""
    ns = app_client
    await finish_bootstrap(ns)
    job_id = "job-check-clean"
    log_path = _write_log(ns, job_id, "check-clean.ndjson")
    await _insert_check_job(ns, job_id, log_path=log_path, status="success")

    view = (await ns.client.get(f"/api/checks/{job_id}")).json()["check_view"]
    assert view["error_count"] == 0
    assert view["num_errors"] == 0
    assert view["broken_packs"] == []

    # 日志缺失（如调度冲突的 cancelled 任务）→ 零值视图，不报错
    absent = str(ns.app.state.settings.jobs_dir / "absent.log")
    await _insert_check_job(ns, "job-check-no-log", log_path=absent, status="cancelled")
    view = (await ns.client.get("/api/checks/job-check-no-log")).json()["check_view"]
    assert view["error_count"] == 0 and view["read_data"] is False


async def test_e22_read_data_from_submitted_params(app_client):
    """read_data 取提交参数内存：经 E12 提交 read_data=True 的 check → 视图 read_data=True。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/jobs", json={"type": "check", "params": {"read_data": True}}, headers=ORIGIN
    )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    view = (await ns.client.get(f"/api/checks/{job_id}")).json()["check_view"]
    assert view["read_data"] is True
