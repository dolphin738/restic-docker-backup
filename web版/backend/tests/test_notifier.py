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

from app.constants import MEOW_MAX_WAIT_SECONDS
from app.db.database import Database
from app.db.repositories.config_repo import ConfigRepo
from app.services import notifier as notifier_module
from app.services.notifier import Notifier
from app.utils.time import now_utc


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
    """捕获 GET/POST 的假客户端（monkeypatch 注入 AsyncClient 位置）。

    ``params`` 单独记一条流水（MeoW 的 msgType/htmlHeight 走查询参数，与 Body 分离断言）。
    """

    responses: dict[str, FakeResponse] = field(default_factory=dict)
    calls: list[tuple[str, str, Any]] = field(default_factory=list)
    params: list[dict[str, str] | None] = field(default_factory=list)
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

    async def post(self, url: str, json: Any = None, params: dict[str, str] | None = None) -> FakeResponse:  # noqa: A002
        self.params.append(params)
        return self._respond(url, "POST", json)


def install_fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeAsyncClient:
    """把 notifier.httpx.AsyncClient 替换为返回同一 FakeAsyncClient 实例的工厂。"""
    fake = FakeAsyncClient()

    def factory(**kwargs: Any) -> FakeAsyncClient:
        return fake

    monkeypatch.setattr(notifier_module.httpx, "AsyncClient", factory)
    return fake


class FakeJob:
    """jobs 行替身（Notifier 只读 id/type/status/时间/摘要/repo_id）。"""

    def __init__(
        self,
        job_id: str = "01JOB",
        job_type: str = "backup",
        summary: str | None = None,
        repo_id: str | None = None,
    ):
        self.id = job_id
        self.type = job_type
        self.summary = summary
        self.repo_id = repo_id
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


async def test_meow_payload_uses_msg_field(tmp_path, monkeypatch):
    """契约红线：MeoW JSON 必填字段是 ``msg``（写成 content → 400「消息内容不能为空」）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas")

    ok, _, results, _ = await notifier.send_test()

    assert ok is True and results[0].channel == "meow"
    body = fake.calls[0][2]
    assert "content" not in body                       # 反向断言：错字段名不得复活
    assert body["msg"] and body["msg"].startswith("这是一条测试消息")
    assert body["title"] == "restic-web 通知测试"
    assert fake.calls[0][1] == "https://api.chuckfang.com/nas"
    assert fake.params[0] == {"msgType": "text"}       # 纯文本态不夹带 htmlHeight
    await db.close()


async def test_meow_html_style_goes_query_params(tmp_path, monkeypatch):
    """msgType/htmlHeight 是查询参数（API §3.2），不属于 JSON Body。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas")
    await config.set("notify.meow.msg_type", "html")
    await config.set("notify.meow.html_height", "300")

    await notifier.send_test()

    assert fake.params[0] == {"msgType": "html", "htmlHeight": "300"}
    assert set(fake.calls[0][2]) == {"title", "msg"}   # Body 只这两键
    await db.close()


async def test_meow_style_invalid_falls_back(tmp_path, monkeypatch):
    """非法 msg_type / 越界 html_height 回退缺省（text / 200），不把脏值送出去。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas")
    await config.set("notify.meow.msg_type", "markdown")   # 非法
    await config.set("notify.meow.html_height", "99999")   # 越界

    await notifier.send_test()

    assert fake.params[0] == {"msgType": "text"}
    assert notifier_module._parse_html_height("99999") == 200
    await db.close()


async def test_meow_nickname_url_encoded(tmp_path, monkeypatch):
    """昵称 URL 编码（中文/空格；斜杠也不得破坏路径结构）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas 01/中文")

    await notifier.send_test()

    url = fake.calls[0][1]
    assert url.startswith("https://api.chuckfang.com/nas%2001%2F")
    assert "/中文" not in url                          # 中文不得裸奔（API 要求 URL 编码）
    await db.close()


async def test_meow_error_surfaces_status_and_server_msg(tmp_path, monkeypatch):
    """失败原因带上业务状态码与服务端原文（400/404 可自助定位，禁止只回「推送失败」）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    await config.set("notify.meow.nickname", "nas")
    fake = install_fake_client(monkeypatch)
    fake.responses["https://api.chuckfang.com"] = FakeResponse(
        status_code=200, payload={"status": 400, "data": False, "msg": "发送失败，消息内容不能为空"}
    )

    ok, _, results, reason = await notifier.send_test()

    assert ok is False
    assert "400" in (results[0].reason or "")
    assert "消息内容不能为空" in (results[0].reason or "")
    assert reason and "meow" in reason
    await db.close()


async def test_meow_business_status_hints(tmp_path, monkeypatch):
    """业务状态码语义化：429/404/403 都在 JSON status 里（HTTP 恒 200），逐码给可操作指引。

    反向断言（缺陷回归）：429 必须落到「限流」语义——此前判的是
    ``resp.status_code == 429``，而该服务 HTTP 恒 200，那行是**死代码**，
    限流被当成普通异常（用户看不出是被限流还是昵称写错）。
    """
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    await config.set("notify.meow.nickname", "nas")
    for status, hint in ((429, "限流"), (404, "昵称未注册"), (403, "敏感词")):
        notifier_module.reset_meow_rate_limiter()
        fake = install_fake_client(monkeypatch)
        fake.responses["https://api.chuckfang.com"] = FakeResponse(
            status_code=200, payload={"status": status, "data": False, "msg": "发送失败"}
        )
        _, _, results, _ = await notifier.send_test()
        reason = results[0].reason or ""
        assert str(status) in reason and hint in reason, f"status={status} 未落到语义化文案：{reason}"
    await db.close()


async def test_meow_optional_url_and_img(tmp_path, monkeypatch):
    """可选增强 ``url`` / ``imgUrl`` 进 JSON Body（文档 §3.2：POST 时 Body 优先于同名查询参数）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas")
    await config.set("notify.meow.url", "https://nas.local/jobs")
    await config.set("notify.meow.img_url", "https://nas.local/icon.png")

    await notifier.send_test()

    body = fake.calls[0][2]
    assert body["url"] == "https://nas.local/jobs"
    assert body["imgUrl"] == "https://nas.local/icon.png"
    # 反向断言：不得混进查询参数（查询参数只该有 msgType/htmlHeight）
    assert fake.params[0] == {"msgType": "text"}
    await db.close()


async def _noop_sleep(_seconds: float) -> None:
    return None


async def test_meow_rate_limiter_min_interval(monkeypatch):
    """最小间隔 3 秒（API §5.8）：第二次发送必须等待，且等待不超过上限。"""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(notifier_module, "_sleep", fake_sleep)
    await notifier_module._MEOW_LIMITER.acquire()
    await notifier_module._MEOW_LIMITER.acquire()
    assert slept and 0 < slept[0] <= MEOW_MAX_WAIT_SECONDS


async def test_meow_rate_limiter_quota_exceeded(monkeypatch):
    """分钟窗口 15 条：第 16 条直接丢弃（不排队、不重试——通知不得拖住任务链路）。"""
    monkeypatch.setattr(notifier_module, "_sleep", _noop_sleep)
    now = now_utc()
    for _ in range(15):
        notifier_module._MEOW_LIMITER._sent.append(now)
    with pytest.raises(RuntimeError, match="配额超限"):
        await notifier_module._MEOW_LIMITER.acquire()


async def test_meow_plan_premium_no_min_interval(monkeypatch):
    """高级版：不限频率（不睡最小间隔），只守每天 2000 条。"""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(notifier_module, "_sleep", fake_sleep)
    await notifier_module._MEOW_LIMITER.acquire("premium")
    await notifier_module._MEOW_LIMITER.acquire("premium")
    assert slept == []          # 反向断言：免费版这里会睡 3 秒

    # 日总量 2000：第 2000 条放行、第 2001 条丢弃（免费版的 15 条/分钟对它无效）
    notifier_module.reset_meow_rate_limiter()
    now = now_utc()
    for _ in range(1999):
        notifier_module._MEOW_LIMITER._sent.append(now)
    await notifier_module._MEOW_LIMITER.acquire("premium")
    with pytest.raises(RuntimeError, match="配额超限"):
        await notifier_module._MEOW_LIMITER.acquire("premium")


async def test_meow_plan_lifetime_daily_quota(monkeypatch):
    """终身版：不限频率，日总量 3000（比高级版 2000 高）。"""
    monkeypatch.setattr(notifier_module, "_sleep", _noop_sleep)
    notifier_module.reset_meow_rate_limiter()
    now = now_utc()
    for _ in range(2999):
        notifier_module._MEOW_LIMITER._sent.append(now)
    await notifier_module._MEOW_LIMITER.acquire("lifetime")     # 第 3000 条放行
    with pytest.raises(RuntimeError, match="配额超限"):
        await notifier_module._MEOW_LIMITER.acquire("lifetime") # 第 3001 条丢弃


async def test_meow_plan_invalid_falls_back_to_free(tmp_path, monkeypatch):
    """套餐值非法 → 回退免费版（最保守：宁可少发也不吃 429）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas")
    await config.set("notify.meow.plan", "vip")   # 非法
    # 先占满免费版分钟配额 → 非法值若被当成不限量就会放行（漏发风险）
    monkeypatch.setattr(notifier_module, "_sleep", _noop_sleep)
    now = now_utc()
    for _ in range(15):
        notifier_module._MEOW_LIMITER._sent.append(now)

    _, _, results, _ = await notifier.send_test()

    assert fake.calls == []
    assert results and results[0].ok is False and "配额超限" in (results[0].reason or "")
    await db.close()


async def test_meow_quota_exceeded_skips_request(tmp_path, monkeypatch):
    """配额耗尽：send_test 的 meow 渠道报失败且**一个请求都不发**（本地守门优于事后吃 429）。"""
    notifier, config, db = await make_notifier(tmp_path, monkeypatch)
    fake = install_fake_client(monkeypatch)
    monkeypatch.setattr(notifier_module, "_sleep", _noop_sleep)
    await config.set("notify.meow.nickname", "nas")
    now = now_utc()
    for _ in range(15):
        notifier_module._MEOW_LIMITER._sent.append(now)

    _, _, results, _ = await notifier.send_test()

    assert fake.calls == []                # 反向断言：不是「发了再吃 429」
    assert results and results[0].ok is False and "配额超限" in (results[0].reason or "")
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


def test_render_no_task_falls_back_to_repo_name():
    """_render：无任务关联（手动 init/unlock 等）→ 任务行回落仓库名，绝不出现「未关联任务」裸奔。"""
    n = Notifier.__new__(Notifier)
    # 1) 有仓库名：显示「仓库：xxx」——用户能看出是哪个仓库的操作失败了
    _, content = n._render(
        job_type="init", status="failed", task_name=None, job_id="01J",
        file_count=0, bytes_done=0, skipped_count=0,
        started_at=None, finished_at=None, summary=None,
        include_summary=False, repo_name="NAS 仓库",
    )
    assert "仓库：NAS 仓库" in content
    assert "未关联任务" not in content
    # 2) 有任务名：任务名优先（仓库名只服务无任务关联的场景）
    _, content2 = n._render(
        job_type="backup", status="failed", task_name="每日全量", job_id="01J",
        file_count=0, bytes_done=0, skipped_count=0,
        started_at=None, finished_at=None, summary=None,
        include_summary=False, repo_name="NAS 仓库",
    )
    assert "任务：每日全量" in content2 and "NAS 仓库" not in content2
    # 3) 都没有：明确标注来源，而不是零信息量占位
    _, content3 = n._render(
        job_type="init", status="failed", task_name=None, job_id="01J",
        file_count=0, bytes_done=0, skipped_count=0,
        started_at=None, finished_at=None, summary=None,
        include_summary=False, repo_name=None,
    )
    assert "来源：手动操作（未关联任务）" in content3


def test_render_job_type_labels_and_redaction_hint():
    """_render：手动操作类型有中文标签；脱敏渠道的失败通知指明去哪看详情。"""
    n = Notifier.__new__(Notifier)
    title, content = n._render(
        job_type="init", status="failed", task_name=None, job_id="01J",
        file_count=0, bytes_done=0, skipped_count=0,
        started_at=None, finished_at=None, summary="Fatal: repository does not exist",
        include_summary=False, repo_name="NAS 仓库",
    )
    assert "初始化任务失败" in title          # 不再裸奔英文 "init"
    assert "类型：初始化" in content
    # 脱敏渠道：摘要被剔除 → 明示前往任务日志，别让失败看起来没有原因
    # （2026-10-22 owner 裁决：正文不出现「已脱敏」字样，只留行动指引）
    assert "失败详情请前往任务日志查看" in content
    # 成功通知不追加该提示（无歧义）
    _, ok_content = n._render(
        job_type="init", status="success", task_name=None, job_id="01J",
        file_count=0, bytes_done=0, skipped_count=0,
        started_at=None, finished_at=None, summary=None,
        include_summary=False, repo_name="NAS 仓库",
    )
    assert "请前往任务日志查看" not in ok_content


async def test_on_finish_manual_job_shows_repo_name(tmp_path, monkeypatch):
    """_on_finish 端到端：手动 init 失败 → MeoW 正文含仓库名（经 ReposRepo 实查）。"""
    from app.db.repositories.repo_repo import ReposRepo

    db = Database(tmp_path / "notify.db")
    await db.connect()
    config = ConfigRepo(db)
    repos_repo = ReposRepo(db)
    await repos_repo.create("r1", "NAS 仓库")
    notifier = Notifier(config, repos_repo)
    fake = install_fake_client(monkeypatch)
    await config.set("notify.meow.nickname", "nas")

    # 无 task_id 的手动 init job（截图场景）：通知必须能看出是哪个仓库
    await notifier.on_finish(
        FakeJob(job_type="init", repo_id="r1", summary="Fatal: unable to open config file"),
        status="failed", task_name=None,
    )
    assert fake.calls, "MeoW 应有一次 POST"
    body = fake.calls[-1][2]
    assert "仓库：NAS 仓库" in body["msg"]     # 显示名，而非 repo_id/UUID
    assert "仓库：r1" not in body["msg"]
    assert "类型：初始化" in body["msg"]
    assert "未关联任务" not in body["msg"]
    await db.close()
