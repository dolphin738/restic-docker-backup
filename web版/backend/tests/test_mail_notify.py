"""批次 D C2 SMTP 邮件通知渠道（Duplicati ``SendMail`` 模块等价物）回归。

断言分五层：
- **口径**：邮件属**消息口径**（failed/skipped 超阈值恒发、success 受 on_success 开关），
  **不参与 healthchecks 心跳**（邮件当心跳会刷屏且易被判垃圾邮件）；
- **连接形态**：``tls``→SMTP_SSL 直连、``starttls``→明文连接后升级、``none``→明文；
  用户名留空时不发 AUTH（内中继场景）；
- **失败不静默**：E24 逐渠道带回原因；终态推送失败只记日志、不影响任务链路；
- **脱敏**：``notify.mail.redact=true`` 时错误摘要从正文剔除；
- **配置鲁棒**：端口/加密方式非法值回退缺省（fail-safe 方向是更安全的一侧）。

网络零触达：monkeypatch ``smtplib.SMTP`` / ``smtplib.SMTP_SSL`` 为记录型替身。
"""

from __future__ import annotations

from email import message_from_string
from email.header import decode_header, make_header
from typing import Any

import pytest

from app.db.database import Database
from app.db.repositories.config_repo import ConfigRepo
from app.services import notifier as notifier_module
from app.services.notifier import Notifier

# ---------------------------------------------------------------- 替身


class _FakeSMTP:
    """记录型 SMTP 替身（EHLO/STARTTLS/AUTH/SEND 全记录，供形态断言）。"""

    instances: list[_FakeSMTP] = []

    def __init__(self, host: str, port: int, *, timeout: float = 0, context: Any = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ssl = False
        self.ehlo_count = 0
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None
        self.sent: list[tuple[str, list[str], str]] = []
        self.quit_called = False
        self.fail_on: str | None = None
        _FakeSMTP.instances.append(self)

    def ehlo(self) -> None:
        self.ehlo_count += 1

    def starttls(self, *, context: Any = None) -> None:
        self.starttls_called = True
        if self.fail_on == "starttls":
            raise RuntimeError("STARTTLS 不受支持")

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)
        if self.fail_on == "auth":
            raise RuntimeError("535 认证失败")

    def sendmail(self, sender: str, recipients: list[str], body: str) -> None:
        if self.fail_on == "send":
            raise RuntimeError("550 收件人被拒")
        self.sent.append((sender, list(recipients), body))

    def quit(self) -> None:
        self.quit_called = True


class _FakeSMTPSSL(_FakeSMTP):
    def __init__(self, host: str, port: int, *, timeout: float = 0, context: Any = None) -> None:  # noqa: ANN001
        super().__init__(host, port, timeout=timeout, context=context)
        self.ssl = True


def _install(monkeypatch: pytest.MonkeyPatch, *, fail_on: str | None = None) -> None:
    _FakeSMTP.instances.clear()

    def make_smtp(host, port, *, timeout=0, context=None):  # noqa: ANN001, ANN202
        obj = _FakeSMTP(host, port, timeout=timeout, context=context)
        obj.fail_on = fail_on
        return obj

    def make_ssl(host, port, *, timeout=0, context=None):  # noqa: ANN001, ANN202
        obj = _FakeSMTPSSL(host, port, timeout=timeout, context=context)
        obj.fail_on = fail_on
        return obj

    monkeypatch.setattr(notifier_module.smtplib, "SMTP", make_smtp)
    monkeypatch.setattr(notifier_module.smtplib, "SMTP_SSL", make_ssl)


async def _make(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db = Database(tmp_path / "mail.db")
    await db.connect()
    config = ConfigRepo(db)
    return Notifier(config), config, db


async def _configure_mail(config: ConfigRepo, **overrides) -> None:
    base = {
        "notify.mail.host": "smtp.example.com",
        "notify.mail.from": "backup@example.com",
        "notify.mail.to": "ops@example.com",
        "notify.mail.username": "backup",
        "notify.mail.password": "app-password",
    }
    base.update(overrides)
    for key, value in base.items():
        if value is not None:
            await config.set(key, str(value))


def _job(summary: str = "执行失败：Boom") -> Any:
    class _Job:
        id = "job-1"
        type = "backup"

    job = _Job()
    job.summary = summary
    job.started_at = "2026-10-17T03:00:00Z"
    job.finished_at = "2026-10-17T03:01:00Z"
    return job


def _subject(smtp: _FakeSMTP) -> str:
    """取解码后的标题（MIMEText 的中文标题走 RFC 2047 base64 编码）。"""
    message = message_from_string(smtp.sent[0][2])
    return str(make_header(decode_header(str(message["Subject"]))))


def _body(smtp: _FakeSMTP) -> str:
    """取出可读正文（MIMEText 的 utf-8 正文走 base64 CTE，断言前须解码）。"""
    return message_from_string(smtp.sent[0][2]).get_payload(decode=True).decode("utf-8")


# ---------------------------------------------------------------- 口径


async def test_failed_sends_mail(tmp_path, monkeypatch) -> None:
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config)

    await notifier.on_finish(_job(), status="failed")

    assert len(_FakeSMTP.instances) == 1
    smtp = _FakeSMTP.instances[0]
    assert smtp.sent[0][0] == "backup@example.com"
    assert smtp.sent[0][1] == ["ops@example.com"]
    assert "任务失败" in _subject(smtp)  # 标题走 RFC 2047 编码，须解码后断言
    assert "执行失败" in _body(smtp)  # 正文默认不脱敏
    assert smtp.quit_called
    await db.close()


async def test_success_mail_gated_by_on_success(tmp_path, monkeypatch) -> None:
    """邮件属消息口径：success 仅在 on_success=1 时推送。"""
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config)

    await notifier.on_finish(_job("success（exit=0）files=3"), status="success")
    assert _FakeSMTP.instances == []  # 未开 on_success → 零投递

    await config.set("notify.on_success", "1")
    await notifier.on_finish(_job("success（exit=0）files=3"), status="success")
    assert len(_FakeSMTP.instances) == 1
    await db.close()


async def test_mail_not_in_healthchecks_heartbeat(tmp_path, monkeypatch) -> None:
    """healthchecks 心跳不牵连邮件：只配了 healthchecks 时邮件零投递。"""
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await config.set("notify.healthchecks_url", "https://hc.example/uuid")
    monkeypatch.setattr(notifier_module.httpx, "AsyncClient", _NoopClient)

    await notifier.on_finish(_job(), status="failed")
    assert _FakeSMTP.instances == []
    await db.close()


class _NoopClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - 吞掉 timeout 等构造参数
        return None

    async def __aenter__(self) -> _NoopClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str) -> Any:
        return _Resp()

    async def post(self, url: str, json: Any = None) -> Any:  # noqa: A002
        return _Resp()


class _Resp:
    status_code = 200

    def raise_for_status(self) -> None:
        return None


# ---------------------------------------------------------------- 连接形态


async def test_security_tls_uses_ssl_connection(tmp_path, monkeypatch) -> None:
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config, **{"notify.mail.security": "tls", "notify.mail.port": "465"})

    await notifier.on_finish(_job(), status="failed")

    smtp = _FakeSMTP.instances[0]
    assert smtp.ssl is True
    assert smtp.port == 465
    assert not smtp.starttls_called  # 隐式 TLS 不再升级
    await db.close()


async def test_security_starttls_upgrades(tmp_path, monkeypatch) -> None:
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config, **{"notify.mail.security": "starttls"})

    await notifier.on_finish(_job(), status="failed")

    smtp = _FakeSMTP.instances[0]
    assert smtp.ssl is False
    assert smtp.starttls_called
    assert smtp.ehlo_count == 2  # 升级前后各一次 EHLO
    assert smtp.login_args == ("backup", "app-password")
    await db.close()


async def test_security_none_skips_starttls_and_auth(tmp_path, monkeypatch) -> None:
    """none = 明文；用户名留空则连 AUTH 都不发（内中继/容器旁路由场景）。"""
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(
        config,
        **{"notify.mail.security": "none", "notify.mail.username": "", "notify.mail.password": ""},
    )

    await notifier.on_finish(_job(), status="failed")

    smtp = _FakeSMTP.instances[0]
    assert not smtp.starttls_called
    assert smtp.login_args is None
    await db.close()


async def test_multiple_recipients_parsed(tmp_path, monkeypatch) -> None:
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config, **{"notify.mail.to": "a@example.com, b@example.com;b@example.com"})

    await notifier.on_finish(_job(), status="failed")

    smtp = _FakeSMTP.instances[0]
    assert smtp.sent[0][1] == ["a@example.com", "b@example.com"]  # 去重 + 分号也认
    assert "a@example.com, b@example.com" in smtp.sent[0][2]  # To 头（MIME 头，非正文）
    await db.close()


# ---------------------------------------------------------------- 失败不静默


async def test_send_test_reports_mail_failure(tmp_path, monkeypatch) -> None:
    """E24：邮件失败必须带着原因回到 UI（禁止静默）。"""
    _install(monkeypatch, fail_on="auth")
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config)

    ok, _latency, results, reason = await notifier.send_test()

    assert ok is False
    assert reason is not None and "535" in reason
    assert [item.channel for item in results] == ["mail"]
    assert results[0].ok is False
    await db.close()


async def test_send_test_includes_mail_channel(tmp_path, monkeypatch) -> None:
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config)
    await config.set("notify.webhook.url", "https://hook.example/x")
    monkeypatch.setattr(notifier_module.httpx, "AsyncClient", _NoopClient)

    ok, _latency, results, reason = await notifier.send_test()

    assert ok is True
    assert {item.channel for item in results} == {"webhook", "mail"}
    await db.close()


async def test_finish_mail_failure_does_not_raise(tmp_path, monkeypatch) -> None:
    """通知绝不抛异常：邮件炸了也要让任务链路走完（终态已落库）。"""
    _install(monkeypatch, fail_on="send")
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config)

    await notifier.on_finish(_job(), status="failed")  # 不抛

    assert _FakeSMTP.instances[0].sent == []
    await db.close()


# ---------------------------------------------------------------- 脱敏与配置鲁棒


async def test_redact_removes_summary(tmp_path, monkeypatch) -> None:
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await _configure_mail(config, **{"notify.mail.redact": "1"})

    await notifier.on_finish(_job(), status="failed")

    body = _body(_FakeSMTP.instances[0])
    assert "执行失败" not in body  # 摘要整体剔除（不出占位符）
    assert "文件数" in body  # 非敏感摘要保留
    await db.close()


async def test_mail_disabled_without_recipients(tmp_path, monkeypatch) -> None:
    """缺收件人 → 渠道未启用 → 零连接（不产生半截配置导致的静默失败）。"""
    _install(monkeypatch)
    notifier, config, db = await _make(tmp_path, monkeypatch)
    await config.set("notify.mail.host", "smtp.example.com")
    await config.set("notify.mail.from", "backup@example.com")

    await notifier.on_finish(_job(), status="failed")
    assert _FakeSMTP.instances == []
    await db.close()


def test_mail_port_fallback_on_invalid() -> None:
    assert notifier_module._parse_mail_port(None) == 587
    assert notifier_module._parse_mail_port("abc") == 587
    assert notifier_module._parse_mail_port("99999") == 587  # 越界
    assert notifier_module._parse_mail_port("2525") == 2525


def test_mail_security_fallback_is_safer() -> None:
    """非法值一律回退到**更**安全的 starttls，绝不静默降为明文。"""
    assert notifier_module._parse_mail_security("weird") == "starttls"
    assert notifier_module._parse_mail_security(None) == "starttls"
    assert notifier_module._parse_mail_security("TLS") == "tls"  # 大小写不敏感
    assert notifier_module._parse_mail_security("none") == "none"


def test_recipients_parse_edge_cases() -> None:
    assert notifier_module._parse_recipients(None) == ()
    assert notifier_module._parse_recipients("  ") == ()
    assert notifier_module._parse_recipients("a@x.com,,b@x.com") == ("a@x.com", "b@x.com")
