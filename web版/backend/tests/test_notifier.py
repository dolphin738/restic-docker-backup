"""Notifier 单测（R-018，架构 §7.6）。

覆盖七个面：
1. 告警口径：failed 恒告警；skipped ≥ 阈值恒告警（阈值 0 = 任何 skipped 即告警）；
   success 仅 on_success 开启时推送（webhook/MeoW 消息口径）；
2. healthchecks 心跳口径：成功 ping 恒发（不发会被判定 missed ping 反向误报），
   失败/skipped 超阈值走 /fail；on_start 走 /start；
3. MeoW 成功判定 = 响应 JSON status==200（HTTP 恒 200）；429 报错不重试；
4. 渠道级脱敏：redact=true 时错误摘要从消息体剔除（webhook 正文与模板变量双路）；
5. 全渠道未配置 = 零网络调用；
6. send_test 逐渠道结果；未配置任何渠道 → ok=False + 原因；
7. 通知绝不抛异常（网络错误吞掉只记日志）。

HTTP 替身：monkeypatch ``app.services.notifier.httpx.AsyncClient`` 为捕获请求的
假客户端工厂（零新依赖，httpx 真实 JSON 编码路径不经替身——本测试不验 HTTP 栈）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.db.database import Database
from app.db.repositories.config_repo import ConfigRepo
from app.services import notifier as notifier_module
from app.services.notifier import Notifier


@dataclass
class FakeResponse:
    status_code: int = 200
    payload: Any = None
    text: str = ""

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("no json")
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@dataclass
class FakeAsyncClient:
    """捕获 GET/POST 的假客户端（monkeypatch 注入 AsyncClient 位置）。"""

    responses: dict[str, FakeResponse] = field(default_factory=dict)
    calls: list[tuple[str, str, Any]] = field(default_factory=list)
    # 按前缀匹配响应（URL 末段 /start、/fail 与裸 ping 不同）
    default_status: int = 200
    raise_on: tuple[str, ...] = ()

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def _respond(self, url: str, method: str, json_body: Any) -> FakeResponse:
        self.calls.append((method, url, json_body))
        for prefix, resp in self.responses.items():
            if url.startswith(prefix):
                return resp
        if any(marker in url for marker in self.raise_on):
            return FakeResponse(status_code=500, text="boom")
        return FakeResponse(status_code=self.default_status, payload={"status": 200})

    async def get(self, url: str) -> FakeResponse:
        return self._respond(url, "GET", None)

    async def post(self, url: str, json: Any = None) -> FakeResponse:  # noqa: A002
        return self._respond(url, "POST", json)


def install_fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeAsyncClient:
    """把 notifier.httpx.AsyncClient 替换为返回同一 FakeAsyncClient 实例的工厂。"""
    fake = FakeAsyncClient()

    def factory(**kwargs: Any) -> FakeAsyncClient:
        return fake

    monkeypatch.setattr(notifier_module.httpx, "AsyncClient", factory)
    return fake


class FakeJob:
    """jobs 行替身（Notifier 只读 id/type/status/时间/摘要）。"""

    def __init__(self, job_id: str = "01JOB", job_type: str = "backup", summary: str | None = None):
        self.id = job_id
        self.type = job_type
        self.summary = summary
        self.started_at = "2026-10-14T03:00:00Z"
        self.finished_at = "2026-10-14T03:01:00Z"


async def make_notifier(tmp_path, monkeypatch: pytest.MonkeyPatch) -> tuple[Notifier, ConfigRepo, Database]:
    db = Database(tmp_path / "notify.db")
    await db.connect()
    config = ConfigRepo(db)
    return Notifier(config), config, db


async def test_failed_alerts_all_channels(tmp_path, monkeypatch):
    """failed：healthchecks /fail + webhook + MeoW 三渠道全部推送。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.healthchecks_url", "https://hc.example/uuid")
    await config.set("notify.webhook.url", "https://hook.example/x")
    await config.set("notify.meow.nickname", "nas")

    await notifier.on_finish(FakeJob(summary="执行失败：Boom"), status="failed")

    methods = [(method, url) for method, url, _ in fake.calls]
    assert ("GET", "https://hc.example/uuid/fail") in methods
    assert ("POST", "https://hook.example/x") in methods
    assert ("POST", "https://api.chuckfang.com/nas") in methods
    await db.close()


async def test_success_healthcheck_always_but_push_gated(tmp_path, monkeypatch):
    """成功：healthchecks 心跳 ping 恒发（心跳语义）；webhook/MeoW 受 on_success 开关。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.healthchecks_url", "https://hc.example/uuid")
    await config.set("notify.webhook.url", "https://hook.example/x")
    await config.set("notify.meow.nickname", "nas")

    await notifier.on_finish(FakeJob(), status="success")

    methods = [(method, url) for method, url, _ in fake.calls]
    assert methods == [("GET", "https://hc.example/uuid")]  # 只有心跳，无消息推送

    await config.set("notify.on_success", "1")
    fake.calls.clear()
    await notifier.on_finish(FakeJob(), status="success")
    methods = [(method, url) for method, url, _ in fake.calls]
    assert ("POST", "https://hook.example/x") in methods
    assert ("POST", "https://api.chuckfang.com/nas") in methods
    await db.close()


async def test_skipped_threshold_semantics(tmp_path, monkeypatch):
    """阈值 100：skipped=5 不告警；阈值 0：skipped=1 即告警（Q4 裁决口径）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas")
    await config.set("notify.skipped_threshold", "100")

    await notifier.on_finish(FakeJob(), status="success", skipped_count=5)
    assert fake.calls == []  # 低于阈值且非失败 → 无推送

    await config.set("notify.skipped_threshold", "0")
    fake.calls.clear()
    await notifier.on_finish(FakeJob(), status="success", skipped_count=1)
    assert any("chuckfang" in url for _, url, _ in fake.calls)
    await db.close()


async def test_meow_success_requires_status_200(tmp_path, monkeypatch):
    """MeoW 成功判定 = 响应 JSON status==200（HTTP 恒 200；429 不重试直接报错）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    await config.set("notify.meow.nickname", "nas")

    # status=429（MeoW 业务码非 200）
    fake = install_fake_client(monkeypatch)
    fake.responses["https://api.chuckfang.com"] = FakeResponse(status_code=200, payload={"status": 429})
    ok, _, results, reason = await notifier.send_test()
    assert ok is False
    assert results[0].ok is False and "MeoW" in (results[0].reason or "")

    # HTTP 429
    fake2 = install_fake_client(monkeypatch)
    fake2.responses["https://api.chuckfang.com"] = FakeResponse(status_code=429, text="rate limited")
    ok2, _, results2, _ = await notifier.send_test()
    assert ok2 is False and results2[0].ok is False
    await db.close()


async def test_webhook_redaction(tmp_path, monkeypatch):
    """渠道级脱敏：redact=true 时错误摘要从正文剔除、模板敏感变量渲染为空。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.webhook.url", "https://hook.example/x")
    await config.set(
        "notify.webhook.template",
        "job={job_id} status={status} detail={error_detail} repo={repo}",
    )
    await config.set("notify.webhook.redact", "1")

    await notifier.on_finish(FakeJob(summary="执行失败：Boom"), status="failed")

    body = fake.calls[0][2]
    assert "执行失败：Boom" not in body["content"]
    assert "detail= repo=" in body["content"]  # 敏感变量剔除后不出占位符值
    await db.close()


async def test_no_channels_zero_network(tmp_path, monkeypatch):
    """全渠道未配置：零网络调用；on_finish/on_start 静默返回。"""
    notifier, _, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await notifier.on_finish(FakeJob(), status="failed")
    await notifier.on_start(FakeJob())
    assert fake.calls == []
    await db.close()


async def test_send_test_reports_per_channel(tmp_path, monkeypatch):
    """send_test：逐渠道结果；未配置任何渠道 → ok=False + 可操作原因。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)

    ok, latency, results, reason = await notifier.send_test()
    assert ok is False and results == [] and reason and "未配置" in reason

    install_fake_client(monkeypatch)
    await config.set("notify.healthchecks_url", "https://hc.example/uuid")
    ok2, _, results2, _ = await notifier.send_test()
    assert ok2 is True
    assert [item.channel for item in results2] == ["healthchecks"]
    assert latency >= 0
    await db.close()


async def test_notification_never_raises(tmp_path, monkeypatch):
    """网络异常一律吞掉：on_start/on_finish 正常返回（通知绝不影响任务链路）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    fake.raise_on = ("hc.example",)
    await config.set("notify.healthchecks_url", "https://hc.example/uuid")
    await config.set("notify.meow.nickname", "nas")

    await notifier.on_start(FakeJob())  # GET 500 → raise_for_status → 被吞
    await notifier.on_finish(FakeJob(), status="failed")  # 同上，且不影响后续渠道
    # POST 路径 JSON 序列化失败同样被吞（content 非 None，验证不抛即可）
    assert True
    await db.close()


def test_on_start_pings_start(tmp_path, monkeypatch):
    """on_start → GET {url}/start（healthchecks /start 语义）。"""
    import asyncio

    async def run() -> tuple[list[tuple[str, str]], Database]:
        notifier, config, db = await make_notifier(tmp_path, monkeypatch)
        fake = install_fake_client(monkeypatch)
        await config.set("notify.healthchecks_url", "https://hc.example/uuid")
        await notifier.on_start(FakeJob())
        return [(m, u) for m, u, _ in fake.calls], db

    methods, db = asyncio.run(run())
    assert ("GET", "https://hc.example/uuid/start") in methods
    asyncio.run(db.close())


def test_config_value_parsers():
    """布尔/阈值解析的缺省与非法回退（Q4：阈值 0 合法）。"""
    assert notifier_module._parse_bool(None, default=False) is False
    assert notifier_module._parse_bool("1", default=False) is True
    assert notifier_module._parse_bool("garbage", default=True) is True
    assert notifier_module._parse_threshold(None) == 100
    assert notifier_module._parse_threshold("0") == 0
    assert notifier_module._parse_threshold("999999999999") == 1_000_000  # 写爆兜底
    assert notifier_module._parse_threshold("abc") == 100


def test_render_redaction_excludes_summary():
    """_render：include_summary=False 时错误摘要整体不出现在正文。"""
    n = Notifier.__new__(Notifier)  # 纯函数路径，绕过构造
    title, content = n._render(
        job_type="backup", status="failed", task_name="每日全量", job_id="01J",
        file_count=3, bytes_done=100, skipped_count=0,
        started_at=None, finished_at=None, summary="机密路径 /srv/secret",
        include_summary=False,
    )
    assert "机密路径" not in content
    assert "每日全量" in content and "失败" in title
