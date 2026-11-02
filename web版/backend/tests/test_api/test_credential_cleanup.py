"""第 2 批 · 清理侧（缺口 2）：密钥源失效后不得留下永不可解的密文。

方案 §9.3 三条各有用例，外加 D/E 两处手抄副本的兜底分支。
判据一律看 ``list_all_raw()``（存储原形）：要求**键已不存在**，而不是"侥幸能解开"。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from conftest import ORIGIN, finish_bootstrap

from app.schemas.api_schemas import RepoStatusResponse
from app.services.crypto import SENSITIVE_EXACT_KEYS, CredentialCrypto, keys_of
from app.services.retention import RetentionService
from app.utils.time import now_utc

# 该密钥源（default）保护的敏感键全集：仓库自己的凭据 + 5 个全局敏感键
_OWNED = ("cred.default.aws_secret_access_key", *SENSITIVE_EXACT_KEYS)
# 明文敏感行（加密开关关闭期间写入的存量形态）与别的仓库的凭据行：两者都必须活下来
_PLAIN = "cred.default.b2_account_id"
_OTHER = "cred.otherp.aws_secret_access_key"


def _passfile(ns, repo_id: str) -> Path:
    """容器（测试）内密码文件路径——经 Settings 派生，避免硬编码布局知识。"""
    return ns.app.state.settings.passfile_path(repo_id)


async def _raw(ns) -> dict[str, str | None]:
    """库内存储原形（不解密）——判"密文已不存在"，而不是判"能不能解开"。"""
    return {row.key: row.value for row in await ns.app.state.config_repo.list_all_raw()}


async def _plain(ns, key: str, value: str) -> None:
    """直插**明文**敏感行（不经 set：set 会按当前开关加密）。"""
    await ns.app.state.db.write(
        "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, 'ui', 'now') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, source = excluded.source",
        (key, value),
    )


async def _seed(ns) -> None:
    """旧口令下写入 1 个仓库凭据 + 5 个全局敏感键（全部为 enc:v2 旧密钥密文）。"""
    for key in _OWNED:
        await ns.app.state.config_repo.set(key, f"secret-for-{key}")
    stored = await _raw(ns)
    assert all(CredentialCrypto.is_encrypted(stored[key]) for key in _OWNED)


def _unreachable(monkeypatch) -> None:
    """让 force 的前置门禁认为仓库打不开（覆盖放行的唯一条件）。"""

    async def probe(_request, _repo_id):
        return RepoStatusResponse(connected=False, failure_kind="password", last_error="密码错误")

    from app.routers import repos_status as repos_router

    monkeypatch.setattr(repos_router, "_probe_repo_status", probe)


# ---------------------------------------------------------------- §9.3 ①②③ force 路径


async def test_force_overwrite_clears_stale_ciphertext_only(app_client, monkeypatch):
    """force 覆盖后：① 全表读取不抛；② 旧密文尽数消失；③ 明文凭据不受影响。"""
    _unreachable(monkeypatch)
    ns = app_client
    await finish_bootstrap(ns)
    _passfile(ns, "default").write_text("old-pw", encoding="utf-8")
    await _seed(ns)
    await _plain(ns, _PLAIN, "plain-b2-id")
    await _plain(ns, _OTHER, "other-repo-ak")

    resp = await ns.client.post(
        "/api/repos/default/password",
        json={"password": "brand-new-secret", "force": True},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert _passfile(ns, "default").read_text(encoding="utf-8") == "brand-new-secret"

    # ① 放大器的入口：全表逐行解密不得抛（修复前此处抛 CredentialDecryptError）
    assert isinstance(await ns.app.state.config_repo.list_all(), list)
    # ② 旧密文已从库里消失（键不存在，不是"侥幸能解"）
    stored = await _raw(ns)
    assert not (set(_OWNED) & set(stored)), set(_OWNED) & set(stored)
    # ③ 明文保留：同一仓库的明文敏感行 + 别的仓库的凭据行都还在
    assert stored[_PLAIN] == "plain-b2-id"
    assert stored[_OTHER] == "other-repo-ak"


async def test_force_write_failure_leaves_ciphertext_intact(app_client, monkeypatch):
    """写口令文件失败 → 422 且**零副作用**：清理不得跑在写之前（否则密钥还在、凭据已毁）。"""
    _unreachable(monkeypatch)
    ns = app_client
    await finish_bootstrap(ns)
    _passfile(ns, "default").write_text("old-pw", encoding="utf-8")
    await _seed(ns)

    from app.routers import repos_status as repos_router

    def _denied(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")  # EROFS

    monkeypatch.setattr(repos_router, "write_passfile", _denied)
    resp = await ns.client.post(
        "/api/repos/default/password",
        json={"password": "brand-new-secret", "force": True},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text

    stored = await _raw(ns)
    assert all(CredentialCrypto.is_encrypted(stored[key]) for key in _OWNED)
    assert _passfile(ns, "default").read_text(encoding="utf-8") == "old-pw"
    assert await ns.app.state.config_repo.get_value(_OWNED[0]) == f"secret-for-{_OWNED[0]}"


# ---------------------------------------------------------------- D · _clear_credentials


async def test_clear_credentials_covers_global_keys_for_default(app_client):
    """hard_delete / release / expire 共用的 ``_clear_credentials``：default 也清全局敏感键。

    default 当前不可达这三条路径（守卫挡着），属**预防性**加固——一旦可达路径变化，
    只清 ``cred.default.*`` 会留下 5 条永不可解的密文。此处单元级直调该步骤。
    """
    ns = app_client
    await finish_bootstrap(ns)
    _passfile(ns, "default").write_text("old-pw", encoding="utf-8")
    await _seed(ns)
    await _plain(ns, _OTHER, "other-repo-ak")

    rows = await ns.app.state.config_repo.list_all_raw()
    owned = keys_of("default", (row.key for row in rows))
    expected = {row.key for row in rows if row.key in owned and row.source != "env" and row.value}
    assert set(_OWNED) <= expected, "全局敏感键不在该密钥源范围内"

    cleared = await ns.app.state.repo_lifecycle._clear_credentials("default")

    stored = await _raw(ns)
    assert cleared == len(expected)
    assert not (expected & set(stored)), expected & set(stored)
    # 别的仓库的凭据键不归这个密钥源，一条都不许动
    assert stored[_OTHER] == "other-repo-ak"


# ---------------------------------------------------------------- E · retention 兜底分支


async def test_retention_fallback_branch_uses_keys_of(app_client):
    """``_lifecycle`` 未注入时的兜底分支（测试基座可达）同样要清到全局敏感键。"""
    ns = app_client
    await finish_bootstrap(ns)
    _passfile(ns, "default").write_text("old-pw", encoding="utf-8")
    await _seed(ns)
    await _plain(ns, _OTHER, "other-repo-ak")
    await ns.app.state.repos_repo.archive("default")  # 直接落库（绕过路由守卫，仅为构造现场）

    # 显式构造"未注入编排器"的 retention（生产装配里 main.py 会回填 _lifecycle）
    svc = RetentionService(
        ns.app.state.job_repo, ns.app.state.config_repo, ns.app.state.audit, ns.app.state.settings
    )
    svc._repos = ns.app.state.repos_repo
    assert svc._lifecycle is None

    report = await svc.cleanup_archives(now=now_utc() + timedelta(days=400))

    assert "default" in report.repos_expired
    stored = await _raw(ns)
    assert not (set(_OWNED) & set(stored)), set(_OWNED) & set(stored)
    assert stored[_OTHER] == "other-repo-ak"
