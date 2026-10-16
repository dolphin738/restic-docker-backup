"""R-024 Prometheus 指标与备份成功率报表（P3-B）——单元 + 仓储聚合 + API 链路。

锁死的不变量：
① 令牌只以 SHA-256 硬哈希入库（E07 写入口径），原文零落盘；
② E37 鉴权（owner 裁决 1C）：Bearer 令牌或 session 任一通过，都不过 → 401；
   首启未改密两种路径都压（403 BOOTSTRAP_REQUIRED）；
③ pull 模型：指标抓取时从 jobs 表现算（无进程内计数器，重启不漂移）；
④ 报表按**配置时区**分桶、零运行日补齐；窗口下界=本地零点（非 UTC 零点，
   防正偏移时区漏首日 8 小时——本文件的回归护栏）；
⑤ 成功率口径：分母只计终态 backup，interrupted/cancelled 单列不混入。
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from conftest import ORIGIN, finish_bootstrap

from app.db.database import Database
from app.db.repositories.job_repo import JobRepo
from app.services.metrics import (
    build_backup_success_report,
    collect_metrics,
    hash_metrics_token,
    render_prometheus,
    verify_metrics_token,
)
from app.utils.time import now_utc, utc_iso

TOKEN = "metrics-scrape-token-7f3a"


# ---------------------------------------------------------------- 令牌单元


def test_token_hash_is_one_way_and_stable() -> None:
    """哈希确定性 + 非可逆形态（hex 64 位；原文不出现在哈希里）。"""
    digest = hash_metrics_token(TOKEN)
    assert len(digest) == 64
    assert TOKEN not in digest
    assert hash_metrics_token(f"  {TOKEN}  ") == digest  # 首尾空白归一


def test_token_verify_semantics() -> None:
    """正确令牌过、错误令牌拒、未设令牌恒拒。"""
    digest = hash_metrics_token(TOKEN)
    assert verify_metrics_token(TOKEN, digest) is True
    assert verify_metrics_token("wrong-token", digest) is False
    assert verify_metrics_token(TOKEN, None) is False
    assert verify_metrics_token(TOKEN, "") is False


# ---------------------------------------------------------------- 仓储聚合 + 渲染


async def _seed_backup(db: Database, job_id: str, *, status: str, finished: bool = True) -> None:
    repo = JobRepo(db)
    await repo.create(job_id=job_id, job_type="backup", trigger="manual", log_path=f"/tmp/{job_id}.log")
    await repo.finalize(job_id, status, exit_code=0 if status == "success" else 1)


async def test_collect_and_render(tmp_path) -> None:
    """聚合 + 渲染：状态计数、ratio、sentinel(-1)、process_start（加密状态指标已移除，审计 2026-10-16 nit-2）。"""
    db = Database(tmp_path / "m.db")
    await db.connect()
    await _seed_backup(db, "job-ok1", status="success")
    await _seed_backup(db, "job-ok2", status="success")
    await _seed_backup(db, "job-bad", status="failed")

    snapshot = await collect_metrics(JobRepo(db))
    assert snapshot.backup_jobs_by_status == {"success": 2, "failed": 1}
    assert snapshot.backup_success_ratio == pytest.approx(2 / 3)
    assert snapshot.jobs_active == 0

    text = render_prometheus(snapshot)
    assert 'restic_web_backup_jobs_total{status="success"} 2' in text
    assert 'restic_web_backup_jobs_total{status="failed"} 1' in text
    assert 'restic_web_backup_jobs_total{status="interrupted"} 0' in text
    assert "restic_web_backup_success_ratio 0.6666666666666666" in text
    assert "restic_web_process_start_time_seconds" in text
    assert text.count("# TYPE restic_web_backup_jobs_total gauge") == 1
    assert text.endswith("\n")

    # 无历史：sentinel -1（Prometheus 侧可配 < 0 告警），不抛错
    empty_db = Database(tmp_path / "empty.db")
    await empty_db.connect()
    empty_text = render_prometheus(await collect_metrics(JobRepo(empty_db)))
    assert "restic_web_backup_success_ratio -1" in empty_text
    assert "restic_web_backup_last_run_timestamp_seconds -1" in empty_text
    await empty_db.close()
    await db.close()


# ---------------------------------------------------------------- 报表


async def test_report_buckets_and_timezone(tmp_path) -> None:
    """分桶：零运行日补齐 + 按配置时区归日（同一 UTC 时刻两种时区落两天）。"""
    db = Database(tmp_path / "r.db")
    await db.connect()
    repo = JobRepo(db)
    await _seed_backup(db, "job-a", status="success")
    await _seed_backup(db, "job-b", status="failed")
    # 固定 created_at：UTC 16:30 —— Asia/Shanghai 为次日 00:30、UTC 为当日
    stamp = utc_iso(now_utc().replace(hour=16, minute=30, second=0, microsecond=0) - timedelta(days=1))
    for job_id in ("job-a", "job-b"):
        await db.write("UPDATE jobs SET created_at = ? WHERE id = ?", (stamp, job_id))

    sh = await build_backup_success_report(repo, days=7, timezone="Asia/Shanghai")
    utc = await build_backup_success_report(repo, days=7, timezone="UTC")

    sh_day = next(b for b in sh.buckets if b.total > 0)
    utc_day = next(b for b in utc.buckets if b.total > 0)
    assert sh_day.date != utc_day.date  # 时区敏感：同刻归入不同本地日
    assert sh_day.total == 2 and sh_day.success == 1 and sh_day.failed == 1
    assert sh_day.ratio == pytest.approx(0.5)
    assert sh.total == 2 and sh.ratio == pytest.approx(0.5)
    assert len(sh.buckets) == 7  # 零运行日补齐
    assert sum(1 for b in sh.buckets if b.total == 0) == 6
    assert sh.from_date <= sh_day.date <= sh.to_date
    await db.close()


async def test_report_local_midnight_boundary(tmp_path) -> None:
    """窗口下界=本地零点：UTC 0x:30 的作业在东八区应落在首日桶内（不漏 8 小时）。"""
    db = Database(tmp_path / "rb.db")
    await db.connect()
    repo = JobRepo(db)
    await _seed_backup(db, "job-edge", status="success")
    # 昨天 UTC 17:30 = 上海次日 01:30：若 since 误用 UTC 零点，该行被漏掉
    stamp = utc_iso(now_utc().replace(hour=17, minute=30, second=0, microsecond=0) - timedelta(days=1))
    await db.write("UPDATE jobs SET created_at = ? WHERE id = 'job-edge'", (stamp,))

    report = await build_backup_success_report(repo, days=2, timezone="Asia/Shanghai")
    hit = [b for b in report.buckets if b.total > 0]
    assert len(hit) == 1 and hit[0].date == report.to_date and hit[0].success == 1
    await db.close()


# ---------------------------------------------------------------- API 链路（E37/E38）


async def _anon_client(ns) -> httpx.AsyncClient:
    """无 cookie 的匿名客户端（同一 app；验证「无凭证」路径不能被会话 cookie 沾光）。"""
    transport = httpx.ASGITransport(app=ns.app, client=("10.0.0.1", 12345))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_e37_requires_token_or_session(app_client) -> None:
    """无凭证 → 401；错误 Bearer → 401（回落 session 也没有）。"""
    ns = app_client
    await finish_bootstrap(ns)
    anon = await _anon_client(ns)
    try:
        resp = await anon.get("/api/metrics")
        assert resp.status_code == 401
        resp = await anon.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 401  # 令牌未设置
    finally:
        await anon.aclose()


async def test_e37_bearer_token_end_to_end(app_client) -> None:
    """E07 设令牌（入库即哈希）→ Bearer 抓取 200 + 文本格式；原文零落库。"""
    ns = app_client
    await finish_bootstrap(ns)
    client = ns.client

    resp = await client.put(
        "/api/config", json={"patch": {"metrics.token": TOKEN}}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    item = next(i for i in (await client.get("/api/config")).json()["items"] if i["key"] == "metrics.token")
    assert item["has_value"] is True and item["value"] is None  # 敏感键不回明文
    # 库内只有硬哈希（读出经 R-025 透明解密；原文零落盘）
    stored = await ns.app.state.config_repo.get_value("metrics.token")
    assert stored == hash_metrics_token(TOKEN)
    assert TOKEN not in stored

    resp = await client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "restic_web_backup_jobs_total" in resp.text

    # 错误令牌 + 有 session → 200（裁决 1C「任一通过」的回落语义）；
    # 错误令牌 + 无 session → 401（匿名客户端验证）
    resp = await client.get("/api/metrics", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 200
    anon = await _anon_client(ns)
    try:
        resp = await anon.get("/api/metrics", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401
    finally:
        await anon.aclose()

    # 清除令牌后：Bearer 失效，本 client 回落 session 仍 200；
    # 匿名客户端既无令牌也无 session → 401
    resp = await client.put("/api/config", json={"patch": {"metrics.token": ""}}, headers=ORIGIN)
    assert resp.status_code == 200
    resp = await client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    anon = await _anon_client(ns)
    try:
        resp = await anon.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 401
    finally:
        await anon.aclose()


async def test_e37_session_path(app_client) -> None:
    """浏览器 session（cookie）同样可拉指标（裁决 1C 的任一通过）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/metrics")
    assert resp.status_code == 200
    assert "restic_web_info" in resp.text


async def test_e37_bootstrap_gate_with_token(make_app) -> None:
    """首启未改密：持有效令牌也 403（S4/S8 全站压制不因新路由破防）。"""
    ns = await make_app()
    # 未经 finish_bootstrap：must_change=1。直接经仓储设令牌（绕过首启门）
    from app.services.metrics import hash_metrics_token as _hash

    await ns.app.state.config_repo.set("metrics.token", _hash(TOKEN))
    resp = await ns.client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "BOOTSTRAP_REQUIRED"


async def test_e38_report_session_and_validation(app_client) -> None:
    """E38：session 可拉；days 越界 422；分桶按配置时区。"""
    ns = app_client
    await finish_bootstrap(ns)
    client = ns.client

    resp = await client.get("/api/reports/backup-success?days=7")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["days"] == 7 and len(body["buckets"]) == 7
    assert body["total"] == 0 and body["ratio"] is None
    assert body["buckets"][-1]["date"] == body["to_date"]

    for bad in (0, 91, -1):
        resp = await client.get(f"/api/reports/backup-success?days={bad}")
        assert resp.status_code == 422, bad
        assert resp.json()["code"] == "VALIDATION_ERROR"

    # 无 session → 401（报表是普通 session 端点；匿名客户端无 cookie）
    anon = await _anon_client(ns)
    try:
        resp = await anon.get("/api/reports/backup-success", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 401
    finally:
        await anon.aclose()


async def test_e38_report_with_data(app_client) -> None:
    """E38 有数据：终态 backup 进桶，成功率可见。"""
    ns = app_client
    await finish_bootstrap(ns)
    db = ns.app.state.db
    await _seed_backup(db, "job-r1", status="success")
    await _seed_backup(db, "job-r2", status="failed")

    resp = await ns.client.get("/api/reports/backup-success?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2 and body["success"] == 1 and body["failed"] == 1
    assert body["ratio"] == pytest.approx(0.5)
    today_bucket = body["buckets"][-1]
    assert today_bucket["total"] == 2
