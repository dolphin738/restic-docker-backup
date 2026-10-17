"""API 集成：批次 D C2 SMTP 邮件渠道（E23 配置读写 / E24 测试消息）。

断言点：
- E23 读写往返：端口/加密方式/收件人落库并回显；**密码只回 ``mail_password_set``**（S3）；
- 非法值 422：端口越界（1~65535）、加密方式不在白名单（Literal 收口，不靠运行期回退）；
- E24：邮件渠道出现在逐渠道结果里（enabled 时）。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap

_MAIL = {
    "mail_host": "smtp.example.com",
    "mail_port": 587,
    "mail_username": "backup",
    "mail_password": "app-password",
    "mail_from": "backup@example.com",
    "mail_to": "ops@example.com, sre@example.com",
    "mail_security": "starttls",
}


async def _put(app_client, **overrides) -> object:
    payload = dict(_MAIL)
    payload.update(overrides)
    return await app_client.client.put("/api/notify", json=payload, headers=ORIGIN)


async def test_put_and_get_mail_roundtrip(app_client) -> None:
    await finish_bootstrap(app_client)
    resp = await _put(app_client)
    assert resp.status_code == 200, resp.text

    resp = await app_client.client.get("/api/notify")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mail_host"] == "smtp.example.com"
    assert body["mail_port"] == 587
    assert body["mail_username"] == "backup"
    assert body["mail_from"] == "backup@example.com"
    assert body["mail_to"] == "ops@example.com, sre@example.com"
    assert body["mail_security"] == "starttls"
    assert body["mail_redact"] is False


async def test_password_only_exposes_has_value(app_client) -> None:
    """S3：密码不得出现在任何 API 响应里（只回 mail_password_set）。"""
    await finish_bootstrap(app_client)
    await _put(app_client)
    body = (await app_client.client.get("/api/notify")).json()
    assert body["mail_password_set"] is True
    assert "app-password" not in str(body)
    assert "mail_password" not in body  # 明文字段名都不出现


async def test_clear_password_unsets_flag(app_client) -> None:
    """三态语义：空串 = 清除（与 healthchecks/webhook URL 同口径）。"""
    await finish_bootstrap(app_client)
    await _put(app_client)
    resp = await app_client.client.put(
        "/api/notify", json={"mail_password": ""}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    body = (await app_client.client.get("/api/notify")).json()
    assert body["mail_password_set"] is False


async def test_invalid_port_is_422(app_client) -> None:
    await finish_bootstrap(app_client)
    for bad in (0, 65536, -1):
        resp = await _put(app_client, mail_port=bad)
        assert resp.status_code == 422, resp.text


async def test_invalid_security_is_422(app_client) -> None:
    """加密方式非法 → 直接 422（不静默回退，管理员必须看到自己填错了）。"""
    await finish_bootstrap(app_client)
    resp = await _put(app_client, mail_security="weird")
    assert resp.status_code == 422, resp.text


async def test_mail_password_roundtrip(app_client) -> None:
    """密码落库后可读回（库内密文形态由 test_credential_crypto 单独锁定）。"""
    await finish_bootstrap(app_client)
    await _put(app_client)
    config = app_client.app.state.config_repo
    assert (await config.get_value("notify.mail.password")) == "app-password"


async def test_notify_update_is_audited(app_client) -> None:
    """S6：notify.update 审计留存（且 detail 不含密码）。"""
    await finish_bootstrap(app_client)
    await _put(app_client)
    rows = await app_client.app.state.db.fetch_all(
        "SELECT detail FROM audit_logs WHERE action = 'notify.update' ORDER BY id DESC LIMIT 1"
    )
    assert rows
    assert "mail" in str(rows[0]["detail"])
    assert "app-password" not in str(rows[0]["detail"])


async def test_unauthenticated_put_is_401(app_client) -> None:
    await finish_bootstrap(app_client)
    app_client.client.cookies.clear()
    resp = await app_client.client.put("/api/notify", json=_MAIL, headers=ORIGIN)
    assert resp.status_code == 401
