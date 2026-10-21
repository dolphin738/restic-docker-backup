"""R-023 重建仓库端点（POST /api/repos/{repo_id}/rebuild）：E41 死循环修复。

2026-10-21 前 rebuild 仅 default 仓库可用；删除仓库不触碰磁盘数据 ⇒ 同址新建会
撞上打不开的旧仓库（cat config 报「密码错误」），非默认仓库没有任何恢复路径
（「删除后换 URL 新建」同址即死循环）。本文件锁定放开后的语义：

正向：非默认仓库 + 本地 FS → 重建成功（清空旧数据内容、保留目录本身 + 新密码 init + 清旧密钥凭据）；
反向（零损失）：远程后端 422 / 运行中作业 409 / 仓库不存在 404，均不得毁数据、不落审计；
S3：审计 detail 不得出现明文密码。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.constants import AUDIT_REPO_REBUILD


class _FakeProc:
    """restic 替身进程：init 恒成功（lines 空转）。"""

    def __init__(self, _log_path: Path, exit_code: int = 0) -> None:
        self._exit_code = exit_code

    async def lines(self):  # noqa: ANN202
        return
        yield  # pragma: no cover

    async def wait(self) -> int:
        return self._exit_code

    def terminate(self) -> None:  # pragma: no cover
        pass

    def kill(self) -> None:  # pragma: no cover
        pass


@pytest.fixture(autouse=True)
def _reset_repo_status_cache():
    """E30 状态缓存是模块级全局：用例间清零，避免跨用例污染。"""
    from app.routers import repos as repos_router

    repos_router._status_cache = {}
    yield
    repos_router._status_cache = {}


def _passfile(ns, repo_id: str) -> Path:
    return ns.app.state.settings.passfile_path(repo_id)


async def _seed_repo(ns, tmp_path: Path, repo_id: str = "rb1", url: str | None = None) -> Path:
    """建一座「被旧仓库卡住」的非默认仓库：本地目录含旧数据 + 密码文件 + enc:v2 凭据。"""
    data_dir = tmp_path / f"repo-{repo_id}"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "config").write_text("old-repo-config", encoding="utf-8")
    await ns.app.state.repos_repo.create(repo_id, "重建一号", url=url or str(data_dir))
    _passfile(ns, repo_id).parent.mkdir(parents=True, exist_ok=True)
    _passfile(ns, repo_id).write_text("old-password", encoding="utf-8")
    await ns.app.state.config_repo.set(f"cred.{repo_id}.aws_secret_access_key", "sekret-1")
    raw = {r.key: r.value for r in await ns.app.state.config_repo.list_all_raw()}
    assert raw[f"cred.{repo_id}.aws_secret_access_key"].startswith("enc:v2:")
    return data_dir


async def test_rebuild_non_default_repo_succeeds(app_client, tmp_path):
    """核心修复：非默认仓库可原地重建——旧数据目录清空、新密码生效、旧密钥凭据清除。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_repo(ns, tmp_path)

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        return _FakeProc(Path(log_path), 0)

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]

    resp = await ns.client.post(
        "/api/repos/rb1/rebuild", json={"password": "brand-new-pw"}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["generated"] is False
    # S3：自填密码不回显
    assert body["password"] is None

    # 旧仓库数据被清空但目录保留（fake spawn 不会重建内容，故断言的是「清空内容」生效；
    # 保留目录本身 = 同址重建不必先手动建文件夹，2026-10-22 owner 反馈）
    assert data_dir.exists()
    assert not (data_dir / "data").exists()
    assert not (data_dir / "config").exists()
    # 密码文件已换新密码
    assert _passfile(ns, "rb1").read_text(encoding="utf-8") == "brand-new-pw"
    # 旧密钥加密的 enc:v2 凭据无法解密 → 一并清除（明文凭据不受影响）
    assert (await ns.app.state.config_repo.get("cred.rb1.aws_secret_access_key")) is None

    # 审计：repo.rebuild 一条，detail 只记 generated/cleared 计数（S3：无明文）
    rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_REBUILD)
    assert total == 1
    assert rows[0].target == "rb1"
    detail = json.loads(str(rows[0].detail))
    assert detail == {"generated": False, "cleared_credentials": 1}
    assert "brand-new-pw" not in str(rows[0].detail)


async def test_rebuild_generates_password_when_absent(app_client, tmp_path):
    """空 body → 生成强随机密码：落盘、一次性回显。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed_repo(ns, tmp_path, repo_id="rb2")

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        return _FakeProc(Path(log_path), 0)

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]

    resp = await ns.client.post("/api/repos/rb2/rebuild", json={}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    generated = resp.json()["password"]
    assert generated
    assert _passfile(ns, "rb2").read_text(encoding="utf-8") == generated


async def test_rebuild_remote_backend_rejected_zero_loss(app_client, tmp_path):
    """远程后端（s3:…）→ 422：数据/密码文件/仓库行原封不动（零损失），不落审计。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_repo(ns, tmp_path, repo_id="rb3", url="s3:s3.example.com/bucket/restic")

    resp = await ns.client.post(
        "/api/repos/rb3/rebuild", json={"password": "whatever"}, headers=ORIGIN
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"

    assert data_dir.exists()
    assert _passfile(ns, "rb3").read_text(encoding="utf-8") == "old-password"
    assert (await ns.app.state.repos_repo.get("rb3")) is not None

    _rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_REBUILD)
    assert total == 0


async def test_rebuild_rejects_active_jobs_zero_loss(app_client, tmp_path):
    """运行中的作业 → 409：清空不得发生（数据零损失），不落审计。"""
    ns = app_client
    await finish_bootstrap(ns)
    data_dir = await _seed_repo(ns, tmp_path, repo_id="rb4")
    settings = ns.app.state.settings
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    await ns.app.state.job_repo.create(
        job_id="job-running-01",
        job_type="backup",
        trigger="manual",
        log_path=str(settings.jobs_dir / "job-running-01.log"),
        repo_id="rb4",
        status="running",
    )

    resp = await ns.client.post(
        "/api/repos/rb4/rebuild", json={"password": "whatever"}, headers=ORIGIN
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "JOB_CONFLICT"

    assert data_dir.exists()
    assert _passfile(ns, "rb4").read_text(encoding="utf-8") == "old-password"
    _rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_REBUILD)
    assert total == 0


async def test_rebuild_unknown_repo_is_404(app_client):
    """合法 id 但仓库不存在 → 404 REPO_NOT_FOUND，不落审计。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.post("/api/repos/nope/rebuild", json={}, headers=ORIGIN)
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "REPO_NOT_FOUND"

    _rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_REBUILD)
    assert total == 0


async def test_rebuild_fs_root_path_rejected(app_client, tmp_path):
    """仓库 URL 解析为文件系统根 → 422 硬拒绝（清空防线），不落审计。"""
    ns = app_client
    await finish_bootstrap(ns)
    root = Path(tmp_path).anchor  # Windows: 当前盘根；POSIX: /
    await ns.app.state.repos_repo.create("rb5", "根目录仓库", url=str(root))

    resp = await ns.client.post("/api/repos/rb5/rebuild", json={}, headers=ORIGIN)
    assert resp.status_code == 422, resp.text
    assert "根目录" in resp.json()["message"]

    _rows, total = await ns.app.state.audit.query(action=AUDIT_REPO_REBUILD)
    assert total == 0
