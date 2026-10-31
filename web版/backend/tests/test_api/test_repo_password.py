"""R-023 仓库密码端点（POST /api/repos/{repo_id}/password）。

密码既是 restic 仓库密钥，也是 R-025 凭据加密的密钥源（HKDF info 混入 repo_id），
故端点**只在密码文件不存在时允许写入**，且明文仅生成场景一次性回显。

反向断言：审计 detail 不得出现明文（红线 S3）；穿越 id 不得拼进路径（红线 S2 精神）。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.constants import REPO_PASSWORD_LENGTH
from app.schemas.api_schemas import RepoStatusResponse


@pytest.fixture(autouse=True)
def _reset_repo_status_cache():
    """E30 的 TTL 缓存是模块级全局：用例间必须清零，否则前一个用例的探测结果会泄漏。"""
    from app.services import repo_status_cache as repos_router

    repos_router.status_cache.clear()
    yield
    repos_router.status_cache.clear()


def _passfile(ns, repo_id: str) -> Path:
    """容器（测试）内密码文件路径——经 Settings 派生，避免硬编码重复布局知识。"""
    return ns.app.state.settings.passfile_path(repo_id)


async def test_generate_writes_passfile_and_echoes_once(app_client):
    """空 body → 生成强随机密码：落盘 0600，明文一次性回显且与文件内容一致。

    生成长度取 ``REPO_PASSWORD_LENGTH``——与前端「生成强随机密码」同口径
    （此前后端 token_urlsafe(32)≈43 位 vs 前端 20 位，界面上两个位数）。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r2", "二号仓库")

    resp = await ns.client.post("/api/repos/r2/password", json={}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated"] is True
    assert len(body["password"]) == REPO_PASSWORD_LENGTH
    # 四类字符齐备（与前端 generateStrongPassword 的生成口径一致：每类至少一个）
    assert any(c.islower() for c in body["password"])
    assert any(c.isupper() for c in body["password"])
    assert any(c.isdigit() for c in body["password"])
    assert any(not c.isalnum() for c in body["password"])

    path = _passfile(ns, "r2")
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == body["password"]
    # 0600 只在 POSIX 生效：Windows 的 chmod 仅能置只读位，st_mode 恒为 0666。
    # 真实部署是 Linux 容器，故此处按平台跳过而非放宽断言。
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_self_supplied_password_is_not_echoed(app_client):
    """自填密码：落盘精确一致，响应不回显（用户已知）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r3", "三号仓库")

    resp = await ns.client.post(
        "/api/repos/r3/password", json={"password": "my-own-secret"}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated"] is False
    assert body["password"] is None

    assert _passfile(ns, "r3").read_text(encoding="utf-8") == "my-own-secret"


async def test_existing_passfile_is_rejected(app_client):
    """已存在密码文件 → 422（覆盖会让仓库不可读 + 已加密凭据失效），且内容不变。"""
    ns = app_client
    await finish_bootstrap(ns)
    before = _passfile(ns, "default").read_text(encoding="utf-8")  # conftest 预置

    resp = await ns.client.post("/api/repos/default/password", json={}, headers=ORIGIN)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert _passfile(ns, "default").read_text(encoding="utf-8") == before


async def test_unknown_repo_is_404(app_client):
    """合法 id 但仓库不存在 → 404 REPO_NOT_FOUND。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post("/api/repos/nope/password", json={}, headers=ORIGIN)
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "REPO_NOT_FOUND"


async def test_traversal_repo_id_is_rejected(app_client):
    """穿越 id → 4xx，且不得在 PASSFILE_ROOT 之外写出任何文件。"""
    ns = app_client
    await finish_bootstrap(ns)
    outside = Path(ns.app.state.settings.passfile_root).parent / "escaped-password"
    for bad in ("..", "a/b", ".hidden", "%2e%2e"):
        resp = await ns.client.post(f"/api/repos/{bad}/password", json={}, headers=ORIGIN)
        assert resp.status_code in (404, 422), f"{bad} -> {resp.status_code} {resp.text}"
    assert not outside.exists()


async def test_create_repo_rejects_traversal_id(app_client):
    """建仓侧同源校验：非法 id 不得入库（否则后续路径派生即成漏洞）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post("/api/repos", json={"id": "../evil", "name": "恶意"}, headers=ORIGIN)
    assert resp.status_code == 422, resp.text
    assert await ns.app.state.repos_repo.get("../evil") is None


async def test_audit_never_records_plaintext(app_client):
    """红线 S3：审计只记动作与 generated，明文不得出现在 detail。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r4", "四号仓库")

    resp = await ns.client.post("/api/repos/r4/password", json={}, headers=ORIGIN)
    secret = resp.json()["password"]

    rows, _total = await ns.app.state.audit.query(action="repo.password_set", limit=20)
    assert rows, "未留审计痕迹"
    for row in rows:
        assert secret not in str(row.detail)
        # detail 落库为 JSON 文本（AuditRepo 只存文本，不存结构化列）
        assert json.loads(row.detail) == {"generated": True, "forced": False}


async def test_passfile_status_flips_to_configured(app_client):
    """写入后仓库列表的 passfile_status 由 missing 变 configured。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r5", "五号仓库")

    listed = await ns.client.get("/api/repos")
    assert {r["id"]: r["passfile_status"] for r in listed.json()}["r5"] == "missing"

    resp = await ns.client.post("/api/repos/r5/password", json={}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text

    listed = await ns.client.get("/api/repos")
    assert {r["id"]: r["passfile_status"] for r in listed.json()}["r5"] == "configured"


async def test_force_overwrite_allowed_when_repo_unreachable(app_client, monkeypatch):
    """密码文件已存在但仓库打不开（密码错误）→ force=true 允许覆盖。

    自救通道：default 仓库禁删、旧密码又无从找回时，界面此前没有任何出路。
    放行前必须先探测一次——只有仓库**当前打不开**才接受覆盖。
    """
    from app.routers import repos_status as repos_router

    async def unreachable(_request, _repo_id):
        return RepoStatusResponse(
            connected=False,
            failure_kind="password",
            last_error="仓库密码错误（请检查仓库密码文件挂载）",
        )

    monkeypatch.setattr(repos_router, "_probe_repo_status", unreachable)

    ns = app_client
    await finish_bootstrap(ns)
    before = _passfile(ns, "default").read_text(encoding="utf-8")
    assert before != "brand-new-secret"

    resp = await ns.client.post(
        "/api/repos/default/password", json={"password": "brand-new-secret", "force": True}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    assert _passfile(ns, "default").read_text(encoding="utf-8") == "brand-new-secret"

    # 覆盖是危险动作：审计必须留下 forced 标记（明文仍不得入审计）
    rows, _total = await ns.app.state.audit.query(action="repo.password_set", limit=20)
    assert any(json.loads(r.detail).get("forced") is True for r in rows)
    assert not any("brand-new-secret" in str(r.detail) for r in rows)


async def test_force_still_rejected_when_repo_connected(app_client, monkeypatch):
    """仓库此刻能正常打开（密码正确）→ 即使 force 也拒绝：换密码必须走密钥轮换。"""
    from app.routers import repos_status as repos_router

    async def reachable(_request, _repo_id):
        return RepoStatusResponse(connected=True)

    monkeypatch.setattr(repos_router, "_probe_repo_status", reachable)

    ns = app_client
    await finish_bootstrap(ns)
    before = _passfile(ns, "default").read_text(encoding="utf-8")

    resp = await ns.client.post(
        "/api/repos/default/password", json={"password": "would-destroy-repo", "force": True}, headers=ORIGIN
    )
    assert resp.status_code == 422, resp.text
    assert _passfile(ns, "default").read_text(encoding="utf-8") == before


async def test_unwritable_directory_reports_actionable_error(app_client, monkeypatch):
    """目录不可写（只读挂载/权限不足）→ 422 且带容器路径，不静默、不 500。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r6", "六号仓库")

    real_mkdir = Path.mkdir

    def denied_mkdir(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")  # EROFS

    monkeypatch.setattr(Path, "mkdir", denied_mkdir)
    try:
        resp = await ns.client.post("/api/repos/r6/password", json={}, headers=ORIGIN)
    finally:
        monkeypatch.setattr(Path, "mkdir", real_mkdir)

    assert resp.status_code == 422, resp.text
    assert "r6" in resp.json()["message"]
