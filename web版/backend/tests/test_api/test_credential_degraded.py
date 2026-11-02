"""第 3 批 · 方案 B-full：读取侧按密钥源收窄 + 降级语义（方案 §5.1 / §8.4 / §9.4）。

一条不变量：**展示路径降级、执行路径抛错**。失效密钥源保护的键在 GET /config 上
是可判定的「失效」（has_value=False + degraded=True + 原因），绝不当成「没配过」；
而 env_sync.load_credentials（每个备份任务的必经之路）遇到同一批行**必须抛错**——
否则备份会安静地不带云凭据跑起来，最后报「认证失败」，用户查一整天 S3 密钥。
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from conftest import finish_bootstrap

from app.errors import CredentialDecryptError

# default 密钥源保护的两条凭据（密文形态入的库）
_OWNED = ("cred.default.aws_secret_access_key", "cred.default.b2_account_key")
# 另一个密钥源（rb1）的凭据：default 坏掉时它必须照常可读
_OTHER = "cred.rb1.aws_access_key_id"
_OTHER_ENV = {"AWS_ACCESS_KEY_ID": "rb1-ak"}
# 未配置过（库里有一行、值为 NULL）的键：三态里的「没配过」
_NEVER = "cred.rb1.b2_account_id"


def _passfile(ns, repo_id: str = "default") -> Path:
    """密码文件路径经 Settings 派生（不硬编码 E5 布局）。"""
    return ns.app.state.settings.passfile_path(repo_id)


async def _seed(ns) -> None:
    """两个密钥源各自就位：default 两条凭据 + rb1 一条凭据 + 一条「没配过」的行。"""
    _passfile(ns, "rb1").parent.mkdir(parents=True, exist_ok=True)
    _passfile(ns, "rb1").write_text("rb1-password", encoding="utf-8")
    repo = ns.app.state.config_repo
    for key in _OWNED:
        await repo.set(key, f"secret-for-{key}")
    await repo.set(_OTHER, "rb1-ak")
    await ns.app.state.db.write(
        "INSERT INTO config (key, value, source, updated_at) VALUES (?, NULL, 'ui', 'now')",
        (_NEVER,),
    )


async def _items(ns) -> tuple[dict[str, dict], str]:
    """GET /api/config → (按键索引的条目, 原始响应体)。"""
    resp = await ns.client.get("/api/config")
    assert resp.status_code == 200, resp.text
    return {item["key"]: item for item in resp.json()["items"]}, resp.text


async def test_get_config_degrades_only_the_failed_key_source(app_client):
    """§9.4 ①③⑤：失效密钥源降级、其他密钥源照常、口令文件回滚后免重启恢复。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed(ns)
    _passfile(ns).write_text("rotated-secret", encoding="utf-8")  # 密钥源失效

    items, body = await _items(ns)
    for key in _OWNED:
        item = items[key]
        assert item["has_value"] is False, key
        assert item["degraded"] is True, key
        assert item["degraded_reason"], key
        assert "default" in item["degraded_reason"], key
        assert "secret-for-" not in body and "enc:v2:" not in body
    # 另一个密钥源不受影响（缺口 2 的「放大器」已关）
    assert items[_OTHER]["has_value"] is True
    assert items[_OTHER]["degraded"] is False
    assert items[_OTHER]["degraded_reason"] is None
    # 三态互不混淆：没配过（NULL 行）与失效都是 has_value=False，靠 degraded 区分
    assert items[_NEVER]["has_value"] is False
    assert items[_NEVER]["degraded"] is False

    # §9.4 ⑤：口令文件恢复后无需重启（crypto 的 stat 失效缓存）即可再解密
    _passfile(ns).write_text("repo-password", encoding="utf-8")
    items, _ = await _items(ns)
    assert all(items[key]["has_value"] is True for key in _OWNED)
    assert all(items[key]["degraded"] is False for key in _OWNED)


async def test_load_credentials_raises_on_degraded_and_keeps_other_source(app_client):
    """§9.4 ②③：执行路径遇降级行必须抛错；其他仓库的凭据读取与任务链路正常。"""
    ns = app_client
    await finish_bootstrap(ns)
    await _seed(ns)
    _passfile(ns).write_text("rotated-secret", encoding="utf-8")

    from app.services.env_sync import load_credentials

    config_repo = ns.app.state.config_repo
    # 降级行不得被当空值跳过：既不返回缺凭据的 dict，也不静默
    with pytest.raises(CredentialDecryptError):
        await load_credentials(config_repo, "default")
    # 备份任务的必经入口（JobRunner._load_credentials）同样抛
    with pytest.raises(CredentialDecryptError):
        await ns.app.state.job_runner._load_credentials("default")
    # 其他仓库的凭据读取与任务链路照常（不再全站瘫痪）
    assert await load_credentials(config_repo, "rb1") == _OTHER_ENV
    assert await ns.app.state.job_runner._load_credentials("rb1") == _OTHER_ENV


async def test_startup_logs_one_aggregated_warning(make_app, monkeypatch):
    """§9.4 ④：启动期把失效密钥源汇总成**一条**告警，且不含任何键名与值（S3）。"""
    ns = await make_app()
    await finish_bootstrap(ns)
    await ns.app.state.config_repo.set(_OWNED[0], "secret-for-aws")
    _passfile(ns).write_text("rotated-secret", encoding="utf-8")

    import app.main as main_module

    recorder = mock.Mock()
    monkeypatch.setattr(main_module, "logger", recorder)
    await make_app()  # 第二次 lifespan = 重启等价（DB 单例复用，口令文件已换）

    warnings = recorder.warning.call_args_list
    assert len(warnings) == 1, warnings  # 不逐键刷屏
    rendered = str(warnings[0].args)
    assert "default" in rendered
    for leaked in ("secret-for-aws", "cred.", "aws_secret_access_key", "enc:v2:"):
        assert leaked not in rendered, leaked
