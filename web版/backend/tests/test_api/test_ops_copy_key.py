"""C4 运维端点契约：手动 copy（搬家）/ 密钥轮换 + 任务镜像校验。

验证分支：
- 422 门（源=目标、未知仓库、缺密码文件、未知/自指镜像仓）；
- 成功路径（mock restic）落地审计 + 密钥轮换重写口令文件（S3：新密码不回显）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.errors import CredentialDecryptError
from app.services.restic import ProbeResult


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


def _snap_line(snapshot_id: str, tags: list[str] | None = None) -> str:
    """构造一行 ``restic snapshots --json`` 记录（解析器只强依赖 ``id``）。"""
    return json.dumps(
        {
            "id": snapshot_id,
            "time": "2026-10-29T12:00:00+08:00",
            "hostname": "host-a",
            "paths": ["/etc"],
            "tags": tags or [],
        }
    )


def _install_copy_probes(ns, *, source_lines, target_runs):
    """按 ``RESTIC_REPOSITORY`` 路由 snapshots 探测；目标仓按调用顺序消费 ``target_runs``。

    预检顺序固定：源（1 次）→ 目标（1 次）→ copy → 复核目标（1 次）。目标仓用队列
    表达「复制前 / 复制后」两个快照集合，正是 ``copied`` 的核算依据。
    """
    pending = list(target_runs)

    async def _fake_run_json(argv, *, env, timeout_seconds, max_lines=200_000):  # noqa: ANN001, ANN202
        del argv, timeout_seconds, max_lines
        if (env or {}).get("RESTIC_REPOSITORY", "") == "local:/main":
            return ProbeResult(exit_code=0, lines=list(source_lines))
        return pending.pop(0) if pending else ProbeResult(exit_code=0, lines=[])

    ns.app.state.restic.run_json = _fake_run_json  # type: ignore[assignment]


async def _prepare_copy_pair(ns) -> None:
    """两仓库 + 目标口令文件（copy 目标须 passfile 就绪，否则前端本就禁选）。"""
    await ns.app.state.repos_repo.update("default", url="local:/main")
    await ns.app.state.repos_repo.create("mir", "镜像仓", url="local:/x")
    mir_pw = ns.app.state.settings.passfile_path("mir")
    mir_pw.parent.mkdir(parents=True, exist_ok=True)
    mir_pw.write_text("mir-pw", encoding="utf-8")


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
    await _prepare_copy_pair(ns)
    captured: dict[str, list[str]] = {}
    source_id = "a" * 64
    copied_id = "b" * 64
    _install_copy_probes(
        ns,
        source_lines=[_snap_line(source_id)],
        target_runs=[
            ProbeResult(exit_code=0, lines=[]),  # 复制前：目标仓空
            ProbeResult(exit_code=0, lines=[_snap_line(copied_id)]),  # 复制后：多了一条
        ],
    )

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
    # 真实增量核算：exit 0 之外还要给出「新增了几条」与目标仓当前总数
    assert body["copied"] == 1
    assert body["target_total"] == 1
    assert body["message"] == ""

    rows, _total = await ns.app.state.audit.query(action="copy.run", limit=10)
    assert any(str(r.result) == "ok" for r in rows)
    detail = json.loads(str(rows[0].detail))
    assert detail.get("copied") == 1
    assert detail.get("target_total") == 1


async def test_copy_rejects_same_store(app_client):
    """源/目标快照 ID 重叠 ⇒ 同一仓库：422 前移，而不是 copy 一次静默 exit 0。

    这是「提示复制完成但目标仓库看不到」最隐蔽的成因：两个仓库记录地址指向同一
    存储时，restic 判定快照已存在直接跳过，退出码仍为 0（0.19.1 实测）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await _prepare_copy_pair(ns)
    same = _snap_line("c" * 64)
    _install_copy_probes(
        ns,
        source_lines=[same],
        target_runs=[ProbeResult(exit_code=0, lines=[same])],
    )

    resp = await ns.client.post(
        "/api/copy",
        json={"source_repo_id": "default", "target_repo_id": "mir"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text
    assert "同一仓库" in resp.text


async def test_copy_rejects_empty_source(app_client):
    """源仓库没有（匹配的）快照 → 422，不跑 copy（它会 exit 0 且零输出）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _prepare_copy_pair(ns)
    _install_copy_probes(
        ns,
        source_lines=[],
        target_runs=[ProbeResult(exit_code=0, lines=[])],
    )
    ran: list[bool] = []

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        ran.append(True)
        return _FakeProc(Path(log_path), 0)

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]
    resp = await ns.client.post(
        "/api/copy",
        json={"source_repo_id": "default", "target_repo_id": "mir"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text
    assert "没有可复制的快照" in resp.text
    assert ran == []  # 预检拦下后绝不 spawn


async def test_copy_rejects_unavailable_target(app_client):
    """目标仓读不出来（不存在/口令错/未初始化）→ 422 前移，别等 copy 才失败。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _prepare_copy_pair(ns)
    _install_copy_probes(
        ns,
        source_lines=[_snap_line("d" * 64)],
        target_runs=[
            ProbeResult(exit_code=10, lines=["Fatal: repository does not exist"]),
        ],
    )
    resp = await ns.client.post(
        "/api/copy",
        json={"source_repo_id": "default", "target_repo_id": "mir"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text
    assert "目标仓库不可用" in resp.text


async def test_manual_copy_reports_zero_copied(app_client):
    """exit 0 但目标仓快照数没变 ⇒ copied=0 + 明确 message（不得报「复制完成」）。

    形态：目标仓已包含全部待复制快照（跨仓副本的 ID 与源不同，故预检不会判同仓）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await _prepare_copy_pair(ns)
    existing = _snap_line("e" * 64)
    _install_copy_probes(
        ns,
        source_lines=[_snap_line("f" * 64)],
        target_runs=[
            ProbeResult(exit_code=0, lines=[existing]),
            ProbeResult(exit_code=0, lines=[existing]),  # 复核：没有新增
        ],
    )

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
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
    assert body["copied"] == 0
    assert body["target_total"] == 1
    assert "未新增" in body["message"]
    # 前端据 copied=0 显示 warning；后端 message 同口径，避免两处语义漂移
    rows, _total = await ns.app.state.audit.query(action="copy.run", limit=10)
    detail = json.loads(str(rows[0].detail))
    assert detail.get("copied") == 0


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


async def test_rotate_default_reencrypts_credentials(app_client):
    """审计 2026-10-18 S-1：rotate default 仓库后，存量凭据必须用**新密钥**重加密。

    缺口原状：rotate 成功重写口令文件 → R-025 密钥（密码文件派生）随之切换 →
    旧密钥加密的存量 enc:v2 凭据全部解不开，后续任务 500。修复后：rotate 在
    重写口令文件前用旧密钥解出明文快照，重写后经 set() 写侧用新密钥写回。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="local:/main")
    ns.app.state.settings.passfile_path("default").write_text("old-pw", encoding="utf-8")

    # 旧密钥下经 E07 录入一条凭据（落库应为 enc:v2 旧密钥密文）
    resp = await ns.client.put(
        "/api/config",
        json={"patch": {"cred.default.aws_secret_access_key": "sekret-1"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    crypto = ns.app.state.encryption.crypto
    raw_before = {r.key: r.value for r in await ns.app.state.config_repo.list_all_raw()}
    cipher_before = raw_before["cred.default.aws_secret_access_key"]
    assert cipher_before.startswith("enc:v2:")
    assert crypto.decrypt(cipher_before) == "sekret-1"

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        return _FakeProc(Path(log_path), 0)

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]
    resp = await ns.client.post(
        "/api/repos/rotate-key",
        json={"repo_id": "default", "new_password": "brand-new-pw"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text

    # 口令文件已换新密码（⇒ 密钥已切换）
    assert ns.app.state.settings.passfile_path("default").read_text(encoding="utf-8") == "brand-new-pw"

    # 落库形态：仍是 enc:v2，但**密文已变**（新密钥重加密；nonce 随机 ⇒ 必不同）
    raw_after = {r.key: r.value for r in await ns.app.state.config_repo.list_all_raw()}
    cipher_after = raw_after["cred.default.aws_secret_access_key"]
    assert cipher_after.startswith("enc:v2:")
    assert cipher_after != cipher_before

    # 新密钥可解、明文保持；旧密文用新密钥解不开（隔离证据：不是原样遗留）
    assert crypto.decrypt(cipher_after) == "sekret-1"
    with pytest.raises(CredentialDecryptError):
        crypto.decrypt(cipher_before)  # 旧密文已不可用新密钥解开（确属重加密而非原样遗留）

    # 读侧透明：E30/config 读回明文不变
    resp = await ns.client.get("/api/config")
    assert resp.status_code == 200, resp.text
    items = {i["key"]: i for i in resp.json()["items"]}
    cred = items["cred.default.aws_secret_access_key"]
    assert cred["has_value"] is True

    # 审计 detail 带重加密计数（零明文）
    rows, _total = await ns.app.state.audit.query(action="key.rotate", limit=10)
    detail = json.loads(str(rows[0].detail))
    assert detail.get("creds_reencrypted") == 1
