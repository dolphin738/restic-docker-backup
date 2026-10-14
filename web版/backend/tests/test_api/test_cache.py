"""API 集成：E25 缓存维护（R-021 批次 A）——占用统计/30s 缓存/409 护栏/审计/S1 白名单。"""

from __future__ import annotations

from pathlib import Path

from conftest import ORIGIN, finish_bootstrap


def _cache_dir(ns) -> Path:
    return Path(ns.app.state.settings.cache_dir)


async def _make_running_job(ns, job_id: str = "job-cache-running") -> None:
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    await ns.app.state.job_repo.create(
        job_id=job_id,
        job_type="backup",
        trigger="manual",
        log_path=str(settings.jobs_dir / f"{job_id}.log"),
        status="running",
    )


async def test_cache_routes_require_session(app_client):
    resp = await app_client.client.get("/api/cache")
    assert resp.status_code == 401
    resp = await app_client.client.post("/api/cache/clear", headers=ORIGIN)
    assert resp.status_code == 401


async def test_e25_usage_reports_seed_files(app_client):
    """GET /api/cache：统计 cache_dir 内文件字节与条目数（path 回显白名单路径）。"""
    ns = app_client
    await finish_bootstrap(ns)
    cache_dir = _cache_dir(ns)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "blocks").mkdir()
    (cache_dir / "blocks" / "data.bin").write_bytes(b"x" * 1024)
    (cache_dir / "index").write_bytes(b"y" * 10)

    resp = await ns.client.get("/api/cache")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(cache_dir)
    assert body["size_bytes"] == 1024 + 10
    assert body["items_count"] == 3  # blocks/ 目录 + data.bin + index
    assert body["truncated"] is False
    assert body["measured_at"]


async def test_e25_usage_cached_within_ttl(app_client):
    """30s 结果缓存（Q14 基线）：TTL 内二次查询命中缓存（measured_at 不变）。"""
    ns = app_client
    await finish_bootstrap(ns)
    first = (await ns.client.get("/api/cache")).json()
    second = (await ns.client.get("/api/cache")).json()
    assert first["measured_at"] == second["measured_at"]


async def test_e25_usage_timeout_returns_truncated_not_5xx(app_client, monkeypatch):
    """du 超时 → truncated=True 的部分结果（Q14：不 5xx）。

    确定性手段：把 ``_measure_sync`` 替身为 100ms 慢函数 + 注入 10ms 超时——
    依赖真实盘速的 timeout=0 有竞态（空目录下线程可先于超时回调完成）。
    """
    import time as time_module

    import app.services.cache_service as cache_module
    from app.services.cache_service import CacheService

    def slow_measure(root, sink):  # noqa: ANN001, ANN202
        time_module.sleep(0.1)
        sink["done"] = 1

    monkeypatch.setattr(cache_module, "_measure_sync", slow_measure)
    ns = app_client
    await finish_bootstrap(ns)
    ns.app.state.cache_service = CacheService(ns.app.state.settings, timeout_seconds=0.01)
    resp = await ns.client.get("/api/cache")
    assert resp.status_code == 200
    assert resp.json()["truncated"] is True


async def test_e25_clear_conflicts_with_active_job(app_client):
    """409 护栏（Q15 基线）：存在 running 任务 → JOB_CONFLICT，data 带 running_job_id。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _make_running_job(ns)

    resp = await ns.client.post("/api/cache/clear", headers=ORIGIN)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "JOB_CONFLICT"
    assert body["data"]["running_job_id"] == "job-cache-running"


async def test_e25_clear_conflicts_with_pending_job(app_client):
    """QA 补测（批次 B）：pending 同属活跃态 → 同样 409（restic 进程可能即将持缓存）。"""
    ns = app_client
    await finish_bootstrap(ns)
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    await ns.app.state.job_repo.create(
        job_id="job-cache-pending",
        job_type="backup",
        trigger="manual",
        log_path=str(settings.jobs_dir / "job-cache-pending.log"),
        status="pending",
    )

    resp = await ns.client.post("/api/cache/clear", headers=ORIGIN)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "JOB_CONFLICT"
    assert body["data"]["running_job_id"] == "job-cache-pending"


async def test_e25_clear_empties_dir_records_audit(app_client):
    """清空：cache_dir 内容被删、审计 ``cache.clear`` 首次落库、占用缓存立即失效。"""
    ns = app_client
    await finish_bootstrap(ns)
    cache_dir = _cache_dir(ns)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "nested").mkdir()
    (cache_dir / "nested" / "blob").write_bytes(b"z" * 8)

    resp = await ns.client.post("/api/cache/clear", headers=ORIGIN)
    assert resp.status_code == 200

    assert list(cache_dir.iterdir()) == []  # 目录本身保留，内容清空
    rows, total = await ns.app.state.audit.query(action="cache.clear")
    assert total == 1
    assert rows[0].result == "ok"
    assert rows[0].target == str(cache_dir)

    # 占用缓存已失效：再次查询反映清空后的 0 字节（非 TTL 内旧值）
    usage = (await ns.client.get("/api/cache")).json()
    assert usage["size_bytes"] == 0


async def test_e25_clear_only_touches_cache_dir(app_client):
    """S1 白名单：clear 绝不越出 cache_dir（外部文件原样保留）。"""
    ns = app_client
    await finish_bootstrap(ns)
    outside = Path(ns.app.state.settings.data_dir) / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    _cache_dir(ns).mkdir(parents=True, exist_ok=True)
    (_cache_dir(ns) / "cached.bin").write_bytes(b"c")

    resp = await ns.client.post("/api/cache/clear", headers=ORIGIN)
    assert resp.status_code == 200
    assert outside.read_text(encoding="utf-8") == "keep me"
