"""C4 运维端点契约：手动 copy（搬家）/ 密钥轮换 + 任务镜像校验。

验证分支：
- 422 门（源=目标、未知仓库、缺密码文件、未知/自指镜像仓）；
- 成功路径（mock restic）落地审计 + 密钥轮换重写口令文件（S3：新密码不回显）。
"""

from __future__ import annotations

from pathlib import Path

from conftest import ORIGIN, finish_bootstrap


class _FakeProc:
    def __init__(self, log_path: Path, exit_code: int = 0) -> None:
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


# ---------------------------------------------------------------- 手动 copy 422 门


async def test_copy_rejects_same_source_and_target(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/copy",
        json={"source_repo_id": "default", "target_repo_id": "default"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


async def test_copy_rejects_unknown_source(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("mir", "镜像仓", url="local:/x")
    resp = await ns.client.post(
        "/api/copy",
        json={"source_repo_id": "ghost", "target_repo_id": "mir"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


async def test_copy_rejects_unknown_target(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/copy",
        json={"source_repo_id": "default", "target_repo_id": "ghost"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------- 密钥轮换 422 门


async def test_rotate_key_rejects_unknown_repo(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos/rotate-key",
        json={"repo_id": "ghost", "new_password": "new-secret-pw"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


async def test_rotate_key_rejects_repo_without_passfile(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("nopf", "无密码仓", url="local:/x")
    resp = await ns.client.post(
        "/api/repos/rotate-key",
        json={"repo_id": "nopf", "new_password": "new-secret-pw"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------- 任务镜像校验 422 门


async def test_task_create_rejects_unknown_mirror(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos/default/backup-tasks",
        json={
            "name": "坏镜像任务",
            "sources": ["/etc"],
            "keep_daily": 7,
            "check_sample_ratio": 0.02,
            "monthly_full_day": 1,
            "mirror_repo_ids": ["ghost"],
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


async def test_task_create_rejects_self_mirror(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos/default/backup-tasks",
        json={
            "name": "自指镜像任务",
            "sources": ["/etc"],
            "keep_daily": 7,
            "check_sample_ratio": 0.02,
            "monthly_full_day": 1,
            "mirror_repo_ids": ["default"],
        },
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------- 成功路径（mock restic）


async def test_manual_copy_success_audits(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="local:/main")
    await ns.app.state.repos_repo.create("mir", "镜像仓", url="local:/x")
    mir_pw = ns.app.state.settings.passfile_path("mir")
    mir_pw.parent.mkdir(parents=True, exist_ok=True)
    mir_pw.write_text("mir-pw", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        captured["argv"] = list(argv)
        return _FakeProc(Path(log_path), 0)

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]
    resp = await ns.client.post(
        "/api/copy",
        json={"source_repo_id": "default", "target_repo_id": "mir"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "copy" in captured["argv"]

    rows, _total = await ns.app.state.audit.query(action="copy.run", limit=10)
    assert any(str(r.result) == "ok" for r in rows)


async def test_rotate_key_success_rewrites_passfile(app_client):
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="local:/main")
    ns.app.state.settings.passfile_path("default").write_text("old-pw", encoding="utf-8")

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        return _FakeProc(Path(log_path), 0)

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]
    resp = await ns.client.post(
        "/api/repos/rotate-key",
        json={"repo_id": "default", "new_password": "brand-new-pw"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    # S3：新密码只经请求体，不回显
    assert resp.json().get("new_password") is None
    # 轮换成功后口令文件同步为新密码
    assert ns.app.state.settings.passfile_path("default").read_text(encoding="utf-8") == "brand-new-pw"

    rows, _total = await ns.app.state.audit.query(action="key.rotate", limit=10)
    assert len(rows) == 1
