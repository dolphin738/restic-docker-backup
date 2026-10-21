"""R-023 删除仓库端点（`DELETE /api/repos/{repo_id}`）：E41 v2 语义 + ``repo.delete`` 审计。

正向：删除成功写审计（S6 危险操作清单第 15 项），detail 只含仓库名与两个清理计数。
反向断言（两类）：
- **不得为「删空气」留痕**：404（仓库不存在）/ 409（仍有任务或活跃作业）/ 422（default 禁删）
  三条拒绝路径删除未发生，审计表里不得出现 ``repo.delete``；
- **不得回显凭据（S3）**：detail 只落计数，写入的凭据明文绝不进审计。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.constants import AUDIT_REPO_DELETE

_SECRET = "s3cr3t-ak-value"


@pytest.fixture(autouse=True)
def _reset_repo_status_cache():
    """E30 状态缓存是模块级全局：用例间清零，避免跨用例污染。"""
    from app.routers import repos as repos_router

    repos_router._status_cache = {}
    yield
    repos_router._status_cache = {}


async def _insert_terminal_job(ns, job_id: str, repo_id: str) -> str:
    """落一条终态作业行（走真实 JobRepo，绕过 runner）并返回其日志文件路径。"""
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.jobs_dir / f"{job_id}.log"
    log_path.write_text("pre-recorded output\n", encoding="utf-8")
    await ns.app.state.job_repo.create(
        job_id=job_id,
        job_type="init",
        trigger="manual",
        log_path=str(log_path),
        repo_id=repo_id,
        status="success",
    )
    return str(log_path)


async def _audit_count(ns) -> int:
    """``repo.delete`` 审计条数。"""
    _, total = await ns.app.state.audit.query(action=AUDIT_REPO_DELETE)
    return total


async def test_delete_repo_writes_audit_with_counts(app_client):
    """删除成功 → 审计 ``repo.delete`` 一条：target=repo_id，detail 含 name 与两个清理计数。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r2", "二号仓库")
    log_path = await _insert_terminal_job(ns, "job-init-01", "r2")
    await ns.app.state.config_repo.set("cred.r2.ak", _SECRET)

    resp = await ns.client.delete("/api/repos/r2", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == "ok"

    # 级联效果（E41 v2）：终态作业行 + 日志文件 + 凭据键随仓库一并清除
    assert (await ns.app.state.repos_repo.get("r2")) is None
    assert (await ns.app.state.job_repo.get("job-init-01")) is None
    assert not (ns.app.state.settings.jobs_dir / "job-init-01.log").exists()
    assert (await ns.app.state.config_repo.get("cred.r2.ak")) is None

    rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_DELETE)
    assert total == 1
    row = rows[0]
    assert row.target == "r2"
    assert row.result == "ok"
    detail = json.loads(row.detail or "{}")
    assert detail["name"] == "二号仓库"
    assert detail["removed_logs"] == 1
    assert detail["cleared_credentials"] == 1
    # S3 反向断言：凭据明文不进审计
    assert _SECRET not in (row.detail or "")
    assert log_path not in (row.detail or "")


async def test_delete_missing_repo_returns_404_without_audit(app_client):
    """仓库不存在 → 404 ``REPO_NOT_FOUND``，且不落审计（拒绝为无效 target 留痕）。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.delete("/api/repos/nope", headers=ORIGIN)
    assert resp.status_code == 404
    assert resp.json()["code"] == "REPO_NOT_FOUND"

    assert await _audit_count(ns) == 0


async def test_delete_repo_with_task_returns_409_without_audit(app_client):
    """仓库下仍有备份任务 → 409 ``REPO_NOT_EMPTY``，仓库保留且不落审计。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r3", "有任务仓库")
    await ns.app.state.task_repo.create({"name": "占用任务", "sources": ["/sources/etc"], "repo_id": "r3"})

    resp = await ns.client.delete("/api/repos/r3", headers=ORIGIN)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "REPO_NOT_EMPTY"
    assert body["data"]["tasks"] == 1

    assert (await ns.app.state.repos_repo.get("r3")) is not None
    assert await _audit_count(ns) == 0


async def test_delete_default_repo_returns_422_without_audit(app_client):
    """default 仓库禁删 → 422，仓库保留且不落审计。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.delete("/api/repos/default", headers=ORIGIN)
    assert resp.status_code == 422

    assert (await ns.app.state.repos_repo.get("default")) is not None
    assert await _audit_count(ns) == 0


# ---------------------------------------------------------------- purge_disk（磁盘清理）


async def _seed_local_repo(ns, tmp_path, repo_id: str) -> Path:
    """建一座本地 FS 仓库：磁盘数据目录 + 密码文件（模拟「删库后卡住同址新建」现场）。"""
    data_dir = tmp_path / f"repo-{repo_id}"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "config").write_text("old-repo-config", encoding="utf-8")
    await ns.app.state.repos_repo.create(repo_id, "本地仓库", url=str(data_dir))
    passfile = ns.app.state.settings.passfile_path(repo_id)
    passfile.parent.mkdir(parents=True, exist_ok=True)
    passfile.write_text("old-password", encoding="utf-8")
    return data_dir


async def test_delete_with_purge_disk_cleans_data_and_passfile(app_client, tmp_path):
    """purge_disk=true：数据目录与密码文件随删除一并清空（同址可干净新建），审计记 purged_disk。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_local_repo(ns, tmp_path, "r7")
    passfile = ns.app.state.settings.passfile_path("r7")

    resp = await ns.client.delete("/api/repos/r7?purge_disk=true", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    assert (await ns.app.state.repos_repo.get("r7")) is None
    assert not data_dir.exists()
    assert not passfile.exists()

    rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_DELETE)
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["purged_disk"] is True


async def test_delete_purge_disk_rejected_for_remote_backend_zero_loss(app_client, tmp_path):
    """远程后端勾选 purge → 422 且**零损失**：仓库行、密码文件、本地旧目录都不得动。"""
    ns = app_client
    await finish_bootstrap(ns)
    # 故意给一座「URL 是远程、磁盘却有旧数据」的仓库：422 时磁盘旧数据同样不许碰
    data_dir = await _seed_local_repo(ns, tmp_path, "r8")
    await ns.app.state.repos_repo.update("r8", url="s3:s3.example.com/bucket/restic")
    passfile = ns.app.state.settings.passfile_path("r8")

    resp = await ns.client.delete("/api/repos/r8?purge_disk=true", headers=ORIGIN)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"

    assert (await ns.app.state.repos_repo.get("r8")) is not None
    assert passfile.read_text(encoding="utf-8") == "old-password"
    assert data_dir.exists()
    assert await _audit_count(ns) == 0


async def test_delete_purge_disk_on_default_rejected_zero_loss(app_client, tmp_path):
    """default 禁删 + purge → 422：校验必须先于一切副作用，「拒绝删除却已毁数据」不可接受。"""
    ns = app_client
    await finish_bootstrap(ns)
    # default 预置 url 为 None；给一个本地目录作为「若误清空就是事故」的标靶
    data_dir = tmp_path / "repo-default"
    data_dir.mkdir()
    (data_dir / "keep").write_text("x", encoding="utf-8")
    await ns.app.state.repos_repo.update("default", url=str(data_dir))

    resp = await ns.client.delete("/api/repos/default?purge_disk=true", headers=ORIGIN)
    assert resp.status_code == 422, resp.text

    assert (await ns.app.state.repos_repo.get("default")) is not None
    assert data_dir.exists()
    assert await _audit_count(ns) == 0


async def test_delete_without_purge_keeps_disk_data(app_client, tmp_path):
    """默认（不勾选）：磁盘数据与密码文件保留——重新添加同 id 仓库仍可复用。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_local_repo(ns, tmp_path, "r9")
    passfile = ns.app.state.settings.passfile_path("r9")

    resp = await ns.client.delete("/api/repos/r9", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    assert (await ns.app.state.repos_repo.get("r9")) is None
    assert data_dir.exists()
    assert passfile.read_text(encoding="utf-8") == "old-password"

    rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_DELETE)
    detail = json.loads(rows[0].detail or "{}")
    assert detail["purged_disk"] is False
