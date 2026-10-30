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

from app.constants import AUDIT_REPO_ARCHIVE, AUDIT_REPO_DELETE, AUDIT_REPO_PURGE

_SECRET = "s3cr3t-ak-value"


@pytest.fixture(autouse=True)
def _reset_repo_status_cache():
    """E30 状态缓存是模块级全局：用例间清零，避免跨用例污染。"""
    from app.services import repo_status_cache as repos_router

    repos_router.status_cache.clear()
    yield
    repos_router.status_cache.clear()


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

    resp = await ns.client.delete("/api/repos/r2?hard=1", headers=ORIGIN)
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


async def test_delete_with_purge_disk_clears_contents_keeps_dir(app_client, tmp_path):
    """purge_disk=true：数据目录**内容**清空、目录本身保留（同址新建免手动建文件夹），
    密码文件删除且 ``<passfile_root>/<repo_id>/`` 空目录一并回收；审计记 purged_disk。

    2026-10-22 owner 反馈：整体 rmtree 掉 /home/123/qqq 后，同址新建还得先手动
    建文件夹；只删 password 不删目录，长期遗留一堆仓库 id 命名的空文件夹。
    """
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_local_repo(ns, tmp_path, "r7")
    passfile = ns.app.state.settings.passfile_path("r7")

    resp = await ns.client.delete("/api/repos/r7?purge_disk=true", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    assert (await ns.app.state.repos_repo.get("r7")) is None
    # 目录本身保留，内容清空
    assert data_dir.exists()
    assert not (data_dir / "data").exists()
    assert not (data_dir / "config").exists()
    # 密码文件删除，仓库 id 命名的空目录一并回收（不遗留孤儿空文件夹）
    assert not passfile.exists()
    assert not passfile.parent.exists()

    # 销毁分支记 repo.purge（S6 危险清单），不再记 repo.delete
    rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_PURGE)
    assert total == 1
    detail = json.loads(rows[0].detail or "{}")
    assert detail["purged_disk"] is True


async def test_delete_purge_disk_reclaims_empty_passfile_dir_without_password(app_client, tmp_path):
    """从未设置密码的仓库：purge 仍回收空的 ``<passfile_root>/<repo_id>/`` 目录。

    现场：运维预置/旧版遗留的空目录 + 无 password 文件——删除仓库时一并回收。
    """
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = tmp_path / "repo-r11"
    data_dir.mkdir()
    await ns.app.state.repos_repo.create("r11", "无密码仓库", url=str(data_dir))
    passfile = ns.app.state.settings.passfile_path("r11")
    passfile.parent.mkdir(parents=True)  # 目录在、密码文件不在

    resp = await ns.client.delete("/api/repos/r11?purge_disk=true", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    assert not passfile.exists()
    assert not passfile.parent.exists()


async def test_delete_purge_disk_keeps_foreign_files_in_passfile_dir(app_client, tmp_path):
    """密码目录里有用户手动放入的文件 → 不回收该目录（rmdir 仅空目录成功）。

    应用只拥有 ``<passfile_root>/<repo_id>/password`` 一个文件；目录里的其他
    文件不属应用管控范围，删仓库不得顺手清掉。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_local_repo(ns, tmp_path, "r10")
    passfile = ns.app.state.settings.passfile_path("r10")
    (passfile.parent / "keep-me").write_text("x", encoding="utf-8")

    resp = await ns.client.delete("/api/repos/r10?purge_disk=true", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    assert not passfile.exists()
    assert (passfile.parent / "keep-me").read_text(encoding="utf-8") == "x"


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

    resp = await ns.client.delete("/api/repos/r9?hard=1", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    assert (await ns.app.state.repos_repo.get("r9")) is None
    assert data_dir.exists()
    assert passfile.read_text(encoding="utf-8") == "old-password"

    rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_DELETE)
    detail = json.loads(rows[0].detail or "{}")
    assert detail["purged_disk"] is False


# ---------------------------------------------------------------- 镜像引用保护（删除保护方案 P1，裁决 5）

MIRROR_ERROR = "REPO_IN_USE_AS_MIRROR"


async def _create_task(ns, repo_id: str, name: str, mirror_repo_ids: list[str]) -> None:
    payload = {"name": name, "sources": ["/sources/data"], "mirror_repo_ids": mirror_repo_ids}
    resp = await ns.client.post(f"/api/repos/{repo_id}/backup-tasks", json=payload, headers=ORIGIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_delete_repo_blocked_when_used_as_mirror(app_client):
    """第三门：仓库仍被别仓任务当镜像目的地 → 409，删除未发生、无审计。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("mirror-target", "镜像目的地仓", url="/data/mirror")
    await _create_task(ns, "default", "主任务", ["mirror-target"])

    resp = await ns.client.delete("/api/repos/mirror-target", headers=ORIGIN)
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == MIRROR_ERROR
    # 阻断不留痕：审计表不得出现 repo.delete
    assert await _audit_count(ns) == 0
    # 仓库仍在
    assert await ns.app.state.repos_repo.get("mirror-target") is not None


async def test_delete_repo_allowed_when_no_mirror_reference(app_client):
    """未被任何任务当镜像目的地 → 正常删除（不回归既有语义）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("plain-target", "普通仓", url="/data/plain")
    await ns.app.state.repos_repo.create("other-repo", "其他仓", url="/data/other")
    await _create_task(ns, "default", "主任务", ["other-repo"])

    resp = await ns.client.delete("/api/repos/plain-target?hard=1", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    assert await _audit_count(ns) == 1
    assert await ns.app.state.repos_repo.get("plain-target") is None


# ---------------------------------------------------------------- 占用明细（删除门动态统计，裁决 6）


async def test_repo_usage_lists_mirror_refs_and_snapshot_total(app_client, monkeypatch):
    """占用明细：镜像引用清单 + 快照**总条数**（含无 tag 快照，故取 tally.total）。"""
    from app.services.snapshot_catalog import SnapshotTally

    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("used-repo", "被引用仓", url="/data/used")
    await _create_task(ns, "default", "主任务", ["used-repo"])

    class _StubCatalog:
        async def snapshot_tally(self, **_kwargs):
            # counts 求和 = 3，但 total = 5（2 条手工快照无 task tag）→ 必须回 5
            return SnapshotTally(counts={"task-1": 2, "task-2": 1}, total=5)

    monkeypatch.setattr(ns.app.state, "snapshot_catalog", _StubCatalog(), raising=False)

    resp = await ns.client.get("/api/repos/used-repo/usage", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repo_id"] == "used-repo"
    assert [ref["name"] for ref in body["mirror_refs"]] == ["主任务"]
    assert body["snapshot_total"] == 5


async def test_repo_usage_unknown_repo_404(app_client):
    """仓库不存在 → 404（与删除门同口径，不静默回空明细）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/repos/nope/usage", headers=ORIGIN)
    assert resp.status_code == 404, resp.text


async def test_repo_usage_snapshot_total_null_when_unavailable(app_client, monkeypatch):
    """探测不可用 → ``snapshot_total`` 为 null（**不可当 0**，否则用户误判「从没备过」）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("dark-repo", "不可达仓", url="/data/dark")

    class _StubCatalog:
        async def snapshot_tally(self, **_kwargs):
            return None

    monkeypatch.setattr(ns.app.state, "snapshot_catalog", _StubCatalog(), raising=False)

    resp = await ns.client.get("/api/repos/dark-repo/usage", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    assert resp.json()["snapshot_total"] is None


# ---------------------------------------------------------------- 归档语义（删除保护方案 P2，裁决 D8）

async def test_delete_default_archives_repo(app_client, tmp_path):
    """默认 DELETE = **归档**：行还在（deleted_at 非空）、凭据与密码文件不动、数据不动。

    「记录会过期，数据永不自动删」的第一条：这步唯一后果是列表不再显示它。
    """
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_local_repo(ns, tmp_path, "ra")
    await ns.app.state.config_repo.set("cred.ra.ak", _SECRET)
    passfile = ns.app.state.settings.passfile_path("ra")

    resp = await ns.client.delete("/api/repos/ra", headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    repo = await ns.app.state.repos_repo.get("ra")
    assert repo is not None and repo.deleted_at is not None
    # 可恢复性全在：凭据 + 密码文件 + 磁盘数据
    cred = await ns.app.state.config_repo.get("cred.ra.ak")
    assert cred is not None and cred.value == _SECRET
    assert passfile.read_text(encoding="utf-8") == "old-password"
    assert data_dir.exists() and (data_dir / "config").exists()
    # 列表不再返回它（归档页才有）
    listed = [r.id for r in await ns.app.state.repos_repo.list_all()]
    assert "ra" not in listed
    assert [r.id for r in await ns.app.state.repos_repo.list_archived()] == ["ra"]
    # 审计：repo.archive（非危险动作），且不产生 repo.delete
    rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_ARCHIVE)
    assert total == 1 and rows[0].target == "ra"
    assert await _audit_count(ns) == 0


async def test_restore_archived_repo_reactivates(app_client, tmp_path):
    """恢复归档：deleted_at 置空 → 重新出现在列表；凭据仍在（无需重建）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_local_repo(ns, tmp_path, "rb")
    await ns.client.delete("/api/repos/rb", headers=ORIGIN)

    resp = await ns.client.post("/api/archive/repos/rb/restore", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    repo = await ns.app.state.repos_repo.get("rb")
    assert repo is not None and repo.deleted_at is None
    assert "rb" in [r.id for r in await ns.app.state.repos_repo.list_all()]


async def test_release_id_requires_confirm_and_leaves_tombstone(app_client, tmp_path):
    """释放 ID：必须原样输入仓库 id；成功后 id 可复用、留不含凭据的墓碑、**不删磁盘数据**。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_local_repo(ns, tmp_path, "rc")
    await ns.app.state.config_repo.set("cred.rc.ak", _SECRET)
    await ns.client.delete("/api/repos/rc", headers=ORIGIN)

    bad = await ns.client.post("/api/archive/repos/rc/release", json={"confirm_id": "wrong"}, headers=ORIGIN)
    assert bad.status_code == 422, bad.text

    ok = await ns.client.post("/api/archive/repos/rc/release", json={"confirm_id": "rc"}, headers=ORIGIN)
    assert ok.status_code == 200, ok.text

    assert (await ns.app.state.repos_repo.get("rc")) is None
    # id 可复用（repos 行已删）
    await ns.app.state.repos_repo.create("rc", "新仓库")
    # 墓碑留存，且不含凭据；磁盘数据未动
    tombs = await ns.app.state.repos_repo.list_tombstones()
    row = next(t for t in tombs if t["repo_id"] == "rc")
    assert row["released_at"] is not None
    assert "password" not in row and "cred" not in json.dumps(row, ensure_ascii=False)
    assert data_dir.exists()


async def test_archive_list_returns_repos_and_tombstones(app_client, tmp_path):
    """归档页数据源：可恢复的归档仓库 + 墓碑（含 data_destroyed 标记）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_local_repo(ns, tmp_path, "rd")
    await ns.client.delete("/api/repos/rd", headers=ORIGIN)
    await _seed_local_repo(ns, tmp_path, "re")
    await ns.client.delete("/api/repos/re?purge_disk=true", headers=ORIGIN)

    resp = await ns.client.get("/api/archive/repos", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    archived = {item["repo_id"]: item for item in body["repos"]}
    assert "rd" in archived and archived["rd"]["archived_at"]
    tombs = {item["repo_id"]: item for item in body["tombstones"]}
    assert tombs["re"]["data_destroyed"] is True
    assert "rd" not in tombs
