"""Notifier（R-018，架构 §7.6）：任务事件 → 三渠道推送（healthchecks / webhook / MeoW）。

设计口径（逐条对应裁决/红线）：

- **通知绝不影响任务链路**：公开方法自身吞异常只记日志（调用点在 JobRunner 终态
  落库之后，异常若上抛会被 ``_run`` 兜底 except 收敛为 failed，篡改已落库的终态）；
- **渠道启用判定** = 对应配置键有值；全部未配置时等价 Noop（零网络调用）；
- **告警口径**（PRD G1/Q4）：failed 恒告警；skipped ≥ 阈值（``notify.skipped_threshold``，
  默认 100，0=只要有 skipped 即告警）恒告警；成功仅在 ``notify.on_success`` 开启时推送；
- **渠道级脱敏**（方案 §5）：``notify.<channel>.redact=true`` 时敏感变量（repo/host/
  error_detail 等）从消息体**直接剔除、不出占位符**，非敏感摘要保留；
  默认 MeoW=true（第三方 SaaS）、webhook=false（自建）；
- **MeoW 成功判定**：响应 JSON ``status==200``（该服务 HTTP 恒 200）；业务码 429（限流）
  记日志不重试，404/403/400 按 :data:`_MEOW_STATUS_HINTS` 给可操作指引；
- **MeoW 客户端限频**（API §5.8/§5.9）：3 秒 1 条 + 分钟 15 / 小时 60 / 天 1000 三级滑动
  窗口，超限直接丢弃（不排队、不重试）——本地守门优于事后吃服务端 429；
- healthchecks：``GET {url}/start``（on_start）→ 成功 ``GET {url}`` / 失败 ``GET {url}/fail``；
- **SMTP 邮件（C2，Duplicati ``SendMail`` 模块等价物）**：与 webhook/MeoW 同为**消息口径**
  （failed/skipped 超阈值恒发、success 仅 ``notify.on_success`` 开启时发），**不参与
  healthchecks 的心跳口径**（邮件不适合当心跳：频次高、易被判垃圾邮件）。

配置键（§4.3 notify.*，值统一字符串存储）：``notify.healthchecks_url``（敏感脱敏）、
``notify.webhook.url``（敏感脱敏）、``notify.webhook.template``（可选模板，``{var}`` 占位）、
``notify.meow.nickname``、``notify.meow.msg_type``（text/html）、``notify.meow.html_height``
（像素，仅 html 生效）、``notify.meow.url``、``notify.meow.img_url``（可选增强）、
``notify.on_success``、``notify.skipped_threshold``、``notify.webhook.redact``、
``notify.meow.redact``、
C2 新增 ``notify.mail.host/port/username/password/from/to/security/redact``
（``password`` 入 :data:`~app.db.repositories.config_repo.SENSITIVE_EXACT_KEYS`：
只回 ``mail_password_set``、库内随 R-025 加密）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import smtplib
import ssl
import time
from asyncio import to_thread
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import quote

import httpx

from app.constants import (
    MAIL_PORT_DEFAULT,
    MAIL_SECURITY_MODES,
    MEOW_HTML_HEIGHT_DEFAULT,
    MEOW_HTML_HEIGHT_MAX,
    MEOW_HTML_HEIGHT_MIN,
    MEOW_MAX_WAIT_SECONDS,
    MEOW_MSG_TYPE_DEFAULT,
    MEOW_MSG_TYPES,
    MEOW_PLAN_DEFAULT,
    MEOW_PLAN_LIMITS,
    MEOW_PLANS,
    NOTIFY_HTTP_TIMEOUT_SECONDS,
    NOTIFY_MAIL_TIMEOUT_SECONDS,
    NOTIFY_SKIPPED_THRESHOLD_DEFAULT,
    NOTIFY_SKIPPED_THRESHOLD_MAX,
)
from app.db.repositories.config_repo import ConfigRepo
from app.utils.time import now_utc

logger = logging.getLogger(__name__)

# 配置键（单一真源；E23 读写与 Notifier 运行期读取共用）
KEY_HEALTHCHECKS_URL = "notify.healthchecks_url"
KEY_WEBHOOK_URL = "notify.webhook.url"
KEY_WEBHOOK_TEMPLATE = "notify.webhook.template"
KEY_MEOW_NICKNAME = "notify.meow.nickname"
KEY_ON_SUCCESS = "notify.on_success"
KEY_SKIPPED_THRESHOLD = "notify.skipped_threshold"
KEY_WEBHOOK_REDACT = "notify.webhook.redact"
KEY_MEOW_REDACT = "notify.meow.redact"
# 消息形态（API §3.2：msgType / htmlHeight 属查询参数，不是 JSON Body 字段）
KEY_MEOW_MSG_TYPE = "notify.meow.msg_type"
KEY_MEOW_HTML_HEIGHT = "notify.meow.html_height"
# 可选增强（API §3.2 Body 可选字段）：跳转链接与通知图标
KEY_MEOW_URL = "notify.meow.url"
KEY_MEOW_IMG_URL = "notify.meow.img_url"
# 套餐等级（free/premium/lifetime）：决定本地限流档位（API §5.8 只对免费版限频）
KEY_MEOW_PLAN = "notify.meow.plan"

# C2 SMTP 邮件渠道（与 webhook/MeoW 同属消息口径，不参与 healthchecks 心跳）
KEY_MAIL_HOST = "notify.mail.host"
KEY_MAIL_PORT = "notify.mail.port"
KEY_MAIL_USERNAME = "notify.mail.username"
KEY_MAIL_PASSWORD = "notify.mail.password"  # 敏感键（ConfigRepo 侧加密，API 只回 _set）
KEY_MAIL_FROM = "notify.mail.from"
KEY_MAIL_TO = "notify.mail.to"             # 逗号分隔的收件人清单
KEY_MAIL_SECURITY = "notify.mail.security"  # none / starttls / tls
KEY_MAIL_REDACT = "notify.mail.redact"

# 渠道级脱敏缺省（架构 §7.6：MeoW=true、自建 webhook=false；邮件默认 false——
# 邮件是运维自己的通道，错误摘要对排障有价值）
DEFAULT_WEBHOOK_REDACT = False
DEFAULT_MEOW_REDACT = True
DEFAULT_MAIL_REDACT = False
# 邮件加密方式缺省：STARTTLS 提交端口形态（与 MAIL_PORT_DEFAULT=587 同源）
DEFAULT_MAIL_SECURITY = "starttls"

# MeoW 推送端点（架构 §7.6 定稿：POST JSON 至 api.chuckfang.com/{昵称}）
MEOW_BASE_URL = "https://api.chuckfang.com"

# 非敏感摘要变量（脱敏后仍保留；方案 §5 分级清单）
_SAFE_VARIABLES: tuple[str, ...] = (
    "job_id",
    "task_name",
    "type",
    "status",
    "file_count",
    "bytes_done",
    "skipped_count",
    "started_at",
    "finished_at",
)


def _parse_bool(raw: str | None, default: bool) -> bool:
    """config 布尔值解析（"1"/"0" 存储形态）；缺失/非法回退缺省（与阈值解析同口径）。"""
    if raw is None or str(raw).strip() == "":
        return default
    text = str(raw).strip()
    if text in ("1", "true", "True"):
        return True
    if text in ("0", "false", "False"):
        return False
    logger.warning("notify 布尔配置非法：%r，回退缺省 %s", raw, default)
    return default


def _parse_recipients(raw: str | None) -> tuple[str, ...]:
    """解析 ``notify.mail.to``（逗号/分号分隔的收件人清单）；去空白、丢空、保序去重。

    形态校验只做「非空 + 不含空白/逗号」两项——完整 RFC 5322 地址校验交给 SMTP 服务器
    （本地校验再严也挡不住服务器拒收，报错必须来自真实投递）。
    """
    if not raw or not raw.strip():
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in raw.replace(";", ",").split(","):
        address = item.strip()
        if not address or address in seen:
            continue
        seen.add(address)
        result.append(address)
    return tuple(result)


def _parse_mail_port(raw: str | None) -> int:
    """解析 ``notify.mail.port``（非法/越界回退缺省 587 并告警）。"""
    if raw is None or not str(raw).strip():
        return MAIL_PORT_DEFAULT
    try:
        port = int(str(raw).strip())
    except ValueError:
        logger.warning("notify.mail.port 配置非法：%r，回退缺省 %s", raw, MAIL_PORT_DEFAULT)
        return MAIL_PORT_DEFAULT
    if not (1 <= port <= 65535):
        logger.warning("notify.mail.port=%s 越界，回退缺省 %s", port, MAIL_PORT_DEFAULT)
        return MAIL_PORT_DEFAULT
    return port


def _parse_mail_security(raw: str | None) -> str:
    """解析 ``notify.mail.security``（白名单：none/starttls/tls；非法回退 starttls）。

    fail-safe 方向是**更安全**：非法值回退 starttls（加密）而不是 none（明文）。
    """
    if raw is None or not str(raw).strip():
        return DEFAULT_MAIL_SECURITY
    candidate = str(raw).strip().lower()
    if candidate not in MAIL_SECURITY_MODES:
        logger.warning(
            "notify.mail.security 配置非法：%r，回退缺省 %s", raw, DEFAULT_MAIL_SECURITY
        )
        return DEFAULT_MAIL_SECURITY
    return candidate


def _send_mail_sync(cfg: MailConfig, subject: str, body: str) -> None:
    """同步投递一封邮件（线程池内执行）；失败抛异常由调用方逐渠道捕获。

    连接形态三选一（``security``）：
    - ``tls``：``SMTP_SSL`` 直连（465 隐式 TLS）；
    - ``starttls``：明文连接 → ``starttls()`` 升级（587 提交端口）；
    - ``none``：明文（仅内网中继场景，UI 须显式选择）。

    收件人清单进 ``To`` 头（便于客户端展示），实际投递仍用 RCPT TO 逐个下发。
    """
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = cfg.sender
    message["To"] = ", ".join(cfg.recipients)

    if cfg.security == "tls":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            cfg.host, cfg.port, timeout=NOTIFY_MAIL_TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=NOTIFY_MAIL_TIMEOUT_SECONDS)
    try:
        server.ehlo()
        if cfg.security == "starttls":
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if cfg.username:
            server.login(cfg.username, cfg.password)
        server.sendmail(cfg.sender, list(cfg.recipients), message.as_string())
    finally:
        # quit() 失败（网络已断）不该掩盖投递结果：吞掉连接关闭异常
        with contextlib.suppress(Exception):
            server.quit()


def _parse_threshold(raw: str | None) -> int:
    """skipped 阈值解析；非法/越界回退缺省（Q4：0=只要有 skipped 即告警）。"""
    if raw is None or str(raw).strip() == "":
        return NOTIFY_SKIPPED_THRESHOLD_DEFAULT
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning("notify.skipped_threshold 配置非法：%r，回退缺省 %s", raw, NOTIFY_SKIPPED_THRESHOLD_DEFAULT)
        return NOTIFY_SKIPPED_THRESHOLD_DEFAULT
    return max(0, min(value, NOTIFY_SKIPPED_THRESHOLD_MAX))


def _parse_meow_msg_type(raw: str | None) -> str:
    """解析 ``notify.meow.msg_type``（白名单 text/html；非法回退 text 并告警）。

    回退方向是**最保守**：非法值退回纯文本，避免把纯文本内容当 HTML 渲染出脏版式。
    """
    if raw is None or not str(raw).strip():
        return MEOW_MSG_TYPE_DEFAULT
    candidate = str(raw).strip().lower()
    if candidate not in MEOW_MSG_TYPES:
        logger.warning("notify.meow.msg_type 配置非法：%r，回退缺省 %s", raw, MEOW_MSG_TYPE_DEFAULT)
        return MEOW_MSG_TYPE_DEFAULT
    return candidate


def _parse_html_height(raw: str | None) -> int:
    """解析 ``notify.meow.html_height``（像素；非法/越界回退 200 并告警）。"""
    if raw is None or not str(raw).strip():
        return MEOW_HTML_HEIGHT_DEFAULT
    try:
        height = int(str(raw).strip())
    except ValueError:
        logger.warning("notify.meow.html_height 配置非法：%r，回退缺省 %s", raw, MEOW_HTML_HEIGHT_DEFAULT)
        return MEOW_HTML_HEIGHT_DEFAULT
    if not (MEOW_HTML_HEIGHT_MIN <= height <= MEOW_HTML_HEIGHT_MAX):
        logger.warning(
            "notify.meow.html_height=%s 越界（%s~%s），回退缺省 %s",
            height, MEOW_HTML_HEIGHT_MIN, MEOW_HTML_HEIGHT_MAX, MEOW_HTML_HEIGHT_DEFAULT,
        )
        return MEOW_HTML_HEIGHT_DEFAULT
    return height


def _parse_meow_plan(raw: str | None) -> str:
    """解析 ``notify.meow.plan``（白名单 free/premium/lifetime；非法回退 ``free``）。

    回退方向是**最保守**：按最严格的免费版限流（宁可自己少发，也不触发服务端 429
    导致消息真的丢失）；用户改回合法值即生效。
    """
    if raw is None or not str(raw).strip():
        return MEOW_PLAN_DEFAULT
    candidate = str(raw).strip().lower()
    if candidate not in MEOW_PLANS:
        logger.warning("notify.meow.plan 配置非法：%r，回退缺省 %s", raw, MEOW_PLAN_DEFAULT)
        return MEOW_PLAN_DEFAULT
    return candidate


# MeoW 业务状态码提示（API §4）：HTTP 恒 200，真实结果在 JSON 的 status 字段里。
# 逐码给可操作指引——此前一律渲染成「MeoW 返回异常」，用户看不出是该改昵称还是被限流。
_MEOW_STATUS_HINTS: dict[int, str] = {
    400: "参数错误（标题或消息内容为空）",
    403: "内容被策略拦截（敏感词）；可开启 MeoW 脱敏后重试",
    404: "昵称未注册；请核对通知设置里的 MeoW 昵称",
    429: "限流（非会员 3 秒 1 条 / 分钟 15 / 小时 60 / 天 1000）：已放弃本次推送，不重试",
    500: "MeoW 服务端错误",
}

# 可替换的 sleep 钩子（测试注入假时钟用；生产即 asyncio.sleep）
_sleep = asyncio.sleep


@dataclass
class _MeowRateLimiter:
    """MeoW 客户端侧限频（API §5.8/§5.9）：按套餐档位守最小间隔 + 滑动窗口配额。

    存在理由：服务端超限返回的是**业务状态码 429**（HTTP 恒 200），即消息已经丢了；
    且公共出口 IP 向多个昵称高频推送可能被判为攻击（§5.9）。本地守门优于事后吃 429。

    档位（:data:`MEOW_PLAN_LIMITS`）：免费版守 3 秒 1 条 + 15/60/1000 三档；
    高级版/终身版不限频率，只守每天 2000 / 3000 条。

    纪律（红线：通知绝不影响任务链路）：
    - 只为「最小间隔」睡眠，且等待上限 :data:`MEOW_MAX_WAIT_SECONDS`，超上限直接丢弃；
    - 窗口配额超限即抛错丢弃，**不排队、不重试**——告警可以少一条，
      但不能把任务收尾拖住；
    - 时间语义统一走 :func:`app.utils.time.now_utc`（红线 C8，禁止裸 datetime.now）。
    """

    _sent: deque = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self, plan: str = MEOW_PLAN_DEFAULT) -> None:
        """占用一个发送名额；不满足档位限制即抛 :class:`RuntimeError`（调用方按渠道失败处理）。"""
        min_interval, windows = MEOW_PLAN_LIMITS.get(plan, MEOW_PLAN_LIMITS[MEOW_PLAN_DEFAULT])
        async with self._lock:
            now = now_utc()
            if min_interval > 0 and self._sent:
                wait = min_interval - (now - self._sent[-1]).total_seconds()
                if wait > MEOW_MAX_WAIT_SECONDS:
                    raise RuntimeError(
                        f"MeoW 最小间隔未到（{min_interval:g} 秒 1 条）：已丢弃本次推送"
                    )
                if wait > 0:
                    await _sleep(wait)
                    now = now_utc()
            for window, limit in windows:
                used = sum(1 for ts in self._sent if (now - ts).total_seconds() < window)
                if used >= limit:
                    raise RuntimeError(f"MeoW 配额超限（{window}s 内 {limit} 条）：已丢弃本次推送")
            self._sent.append(now)
            # 只保留最大窗口内的记录（更老的用不上，避免无界增长）
            cutoff = now - timedelta(seconds=windows[-1][0])
            while self._sent and self._sent[0] < cutoff:
                self._sent.popleft()

    def reset(self) -> None:
        """清空计数（仅测试夹具使用）。"""
        self._sent.clear()


# 模块级单例（进程内全局生效）；测试用 autouse 夹具 reset，避免用例相互污染
_MEOW_LIMITER = _MeowRateLimiter()


def reset_meow_rate_limiter() -> None:
    """重置 MeoW 限流计数（测试夹具入口）。"""
    _MEOW_LIMITER.reset()


@dataclass(frozen=True)
class MeowStyle:
    """MeoW 消息形态快照（msg_type/html_height = API 查询参数；url/img_url = Body 可选字段）。"""

    msg_type: str = MEOW_MSG_TYPE_DEFAULT
    html_height: int = MEOW_HTML_HEIGHT_DEFAULT
    url: str = ""
    img_url: str = ""
    plan: str = MEOW_PLAN_DEFAULT


@dataclass(frozen=True)
class ChannelResult:
    """单渠道推送结果（E24 测试逐渠道明示，禁止静默）。"""

    channel: str
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class MailConfig:
    """C2 SMTP 配置快照（读 config 后的内存形态，明文只在本进程内流转）。"""

    host: str
    port: int = MAIL_PORT_DEFAULT
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: tuple[str, ...] = ()
    security: str = DEFAULT_MAIL_SECURITY
    redact: bool = DEFAULT_MAIL_REDACT

    @property
    def enabled(self) -> bool:
        """启用判定 = 服务器 + 发件人 + 至少一个收件人（用户名/密码对内中继可留空）。"""
        return bool(self.host and self.sender and self.recipients)


class Notifier:
    """三渠道通知器（运行期读 config，配置改动即时生效，无需重启）。"""

    def __init__(self, config: ConfigRepo, repos_repo: Any = None) -> None:
        """构造通知器。

        Args:
            config: 配置仓储（每次推送前现读，E23 保存即生效）。
            repos_repo: 仓库仓储（可选）：仅用于把无任务关联的 job（init/unlock/check
                等手动操作）在通知里显示为**仓库名**而非「（未关联任务）」；缺省时回落
                「来源：手动操作」。查询失败一律吞掉回落 None——通知不得因查库失败而降级。
        """
        self._config = config
        self._repos = repos_repo

    # ---------------------------------------------------------------- JobRunner 接线点

    async def on_start(self, job: Any) -> None:
        """任务进入 running：healthchecks ``/start`` ping（仅配置了该渠道时）。

        Args:
            job: jobs 表行（只用 ``id`` / ``type``）。
        """
        try:
            url = (await self._config.get_value(KEY_HEALTHCHECKS_URL)) or ""
            if not url.strip():
                return
            await self._ping(url.strip().rstrip("/") + "/start")
        except Exception:  # noqa: BLE001 - 通知绝不影响任务链路
            logger.warning("healthchecks /start 推送失败（job=%s）", getattr(job, "id", "?"), exc_info=True)

    async def on_finish(
        self,
        job: Any,
        *,
        status: str,
        task_name: str | None = None,
        file_count: int = 0,
        bytes_done: int = 0,
        skipped_count: int = 0,
    ) -> None:
        """任务终态：失败/skipped 超阈值恒告警；成功仅在 on_success 开启时推送。

        Args:
            job: jobs 表行。
            status: 终态（success / failed）。
            task_name: 任务名（409 提示同源解析；None = legacy 策略 job）。
            file_count / bytes_done / skipped_count: 任务聚合摘要。
        """
        try:
            await self._on_finish(job, status=status, task_name=task_name,
                                  file_count=file_count, bytes_done=bytes_done, skipped_count=skipped_count)
        except Exception:  # noqa: BLE001 - 通知绝不影响任务链路
            logger.warning("任务终态通知失败（job=%s）", getattr(job, "id", "?"), exc_info=True)

    async def send_test(self) -> tuple[bool, int, list[ChannelResult], str | None]:
        """E24 测试消息：向全部已配置渠道各发一条，逐渠道回结果。

        Returns:
            (整体 ok, 耗时 ms, 逐渠道结果, 整体失败原因)；未配置任何渠道 → ok=False + 原因。
        """
        started = time.monotonic()
        results: list[ChannelResult] = []
        title = "restic-web 通知测试"
        content = "这是一条测试消息：如果你看到它，说明该通知渠道工作正常。"

        healthchecks_url = ((await self._config.get_value(KEY_HEALTHCHECKS_URL)) or "").strip()
        webhook_url = ((await self._config.get_value(KEY_WEBHOOK_URL)) or "").strip()
        meow_nickname = ((await self._config.get_value(KEY_MEOW_NICKNAME)) or "").strip()
        mail = await self._load_mail()

        if healthchecks_url:
            try:
                await self._ping(healthchecks_url.rstrip("/"))
                results.append(ChannelResult(channel="healthchecks", ok=True))
            except Exception as exc:  # noqa: BLE001 - 逐渠道明示失败
                results.append(ChannelResult(channel="healthchecks", ok=False, reason=str(exc)[:200]))
        if webhook_url:
            try:
                await self._send_webhook(url=webhook_url, title=title, content=content, redact=True)
                results.append(ChannelResult(channel="webhook", ok=True))
            except Exception as exc:  # noqa: BLE001
                results.append(ChannelResult(channel="webhook", ok=False, reason=str(exc)[:200]))
        if meow_nickname:
            style = await self._load_meow_style()
            try:
                await self._send_meow(
                    nickname=meow_nickname, title=title, content=content,
                    msg_type=style.msg_type, html_height=style.html_height,
                    url=style.url, img_url=style.img_url, plan=style.plan,
                )
                results.append(ChannelResult(channel="meow", ok=True))
            except Exception as exc:  # noqa: BLE001
                results.append(ChannelResult(channel="meow", ok=False, reason=str(exc)[:200]))
        if mail is not None:
            # 逐渠道显式：邮件失败（服务器拒收/认证失败）必须带着原因回到 UI，禁止静默
            try:
                await self._send_mail(mail, title=title, content=content)
                results.append(ChannelResult(channel="mail", ok=True))
            except Exception as exc:  # noqa: BLE001
                results.append(ChannelResult(channel="mail", ok=False, reason=str(exc)[:200]))

        latency_ms = int((time.monotonic() - started) * 1000)
        if not results:
            return (
                False,
                latency_ms,
                results,
                "未配置任何通知渠道（请先填写 healthchecks / webhook / MeoW / 邮件任一）",
            )
        ok = all(item.ok for item in results)
        reason = None if ok else "；".join(
            f"{item.channel}: {item.reason}" for item in results if not item.ok
        )
        return ok, latency_ms, results, reason

    async def _load_meow_style(self) -> MeowStyle:
        """读取 MeoW 消息形态配置；缺省/非法回退见各解析器。"""
        return MeowStyle(
            msg_type=_parse_meow_msg_type(await self._config.get_value(KEY_MEOW_MSG_TYPE)),
            html_height=_parse_html_height(await self._config.get_value(KEY_MEOW_HTML_HEIGHT)),
            url=((await self._config.get_value(KEY_MEOW_URL)) or "").strip(),
            img_url=((await self._config.get_value(KEY_MEOW_IMG_URL)) or "").strip(),
            plan=_parse_meow_plan(await self._config.get_value(KEY_MEOW_PLAN)),
        )

    # ---------------------------------------------------------------- C2 SMTP 邮件渠道

    async def _load_mail(self) -> MailConfig | None:
        """读取并装配 SMTP 配置；未启用（缺服务器/发件人/收件人）返回 None。"""
        host = ((await self._config.get_value(KEY_MAIL_HOST)) or "").strip()
        sender = ((await self._config.get_value(KEY_MAIL_FROM)) or "").strip()
        recipients = _parse_recipients(await self._config.get_value(KEY_MAIL_TO))
        if not (host and sender and recipients):
            return None
        return MailConfig(
            host=host,
            port=_parse_mail_port(await self._config.get_value(KEY_MAIL_PORT)),
            username=((await self._config.get_value(KEY_MAIL_USERNAME)) or "").strip(),
            # S3：明文只在本进程内存中流转，日志/审计/API 一律不落
            password=(await self._config.get_value(KEY_MAIL_PASSWORD)) or "",
            sender=sender,
            recipients=recipients,
            security=_parse_mail_security(await self._config.get_value(KEY_MAIL_SECURITY)),
            redact=_parse_bool(await self._config.get_value(KEY_MAIL_REDACT), default=DEFAULT_MAIL_REDACT),
        )

    @staticmethod
    async def _send_mail(cfg: MailConfig, *, title: str, content: str) -> None:
        """投递一封邮件（``to_thread``：smtplib 是阻塞 IO，不得占用事件循环）。"""
        await to_thread(_send_mail_sync, cfg, title, content)

    # ---------------------------------------------------------------- 内部实现

    async def _on_finish(
        self,
        job: Any,
        *,
        status: str,
        task_name: str | None,
        file_count: int,
        bytes_done: int,
        skipped_count: int,
    ) -> None:
        """终态分派（异常由 :meth:`on_finish` 外壳吞掉）。

        healthchecks 与 webhook/meow 的告警口径**不同**：healthchecks 是心跳监控，
        成功 ping 恒发（不发会被它判成 missed ping → 反向误报任务死亡）；失败/skipped
        超阈值走 ``/fail``。webhook/MeoW 是消息推送，受 on_success 开关控制。
        """
        job_type = str(getattr(job, "type", "backup"))
        threshold = _parse_threshold(await self._config.get_value(KEY_SKIPPED_THRESHOLD))
        skipped_alert = skipped_count > 0 and skipped_count >= threshold  # 阈值 0 → 任何 skipped 即告警
        on_success = _parse_bool(await self._config.get_value(KEY_ON_SUCCESS), default=False)

        healthchecks_url = ((await self._config.get_value(KEY_HEALTHCHECKS_URL)) or "").strip()
        webhook_url = ((await self._config.get_value(KEY_WEBHOOK_URL)) or "").strip()
        meow_nickname = ((await self._config.get_value(KEY_MEOW_NICKNAME)) or "").strip()
        mail = await self._load_mail()
        if not (healthchecks_url or webhook_url or meow_nickname or mail):
            return  # 全渠道未配置：零网络调用

        # healthchecks：心跳口径——成功/失败都 ping（成功 ping 恒发，见 docstring）
        if healthchecks_url:
            path = "" if (status == "success" and not skipped_alert) else "/fail"
            try:
                await self._ping(healthchecks_url.rstrip("/") + path)
            except Exception:  # noqa: BLE001 - 单渠道失败不拖垮其他渠道
                logger.warning("healthchecks 终态推送失败（job=%s）", getattr(job, "id", "?"), exc_info=True)

        # webhook / MeoW：消息口径——failed/skipped 超阈值恒告警；success 仅 on_success 开启时
        should_push = status == "failed" or skipped_alert or (status == "success" and on_success)
        if not should_push:
            return

        title, content = self._render(job_type=job_type, status=status, task_name=task_name,
                                      job_id=str(getattr(job, "id", "")), file_count=file_count,
                                      bytes_done=bytes_done, skipped_count=skipped_count,
                                      started_at=getattr(job, "started_at", None),
                                      finished_at=getattr(job, "finished_at", None),
                                      summary=getattr(job, "summary", None),
                                      include_summary=False,
                                      repo_name=await self._repo_display_name(job))
        summary = str(getattr(job, "summary", None) or "")
        webhook_redact = _parse_bool(
            await self._config.get_value(KEY_WEBHOOK_REDACT), default=DEFAULT_WEBHOOK_REDACT
        )
        meow_redact = _parse_bool(await self._config.get_value(KEY_MEOW_REDACT), default=DEFAULT_MEOW_REDACT)

        if webhook_url:
            try:
                await self._send_webhook(
                    url=webhook_url,
                    title=title,
                    # redact=false（自建）：正文可携带错误摘要；redact=true 由 variables 置空剔除
                    content=content + (f"\n摘要：{summary}" if summary and not webhook_redact else ""),
                    redact=webhook_redact,
                    variables={
                        "job_id": str(getattr(job, "id", "")),
                        "task_name": task_name or "",
                        "type": job_type,
                        "status": status,
                        "file_count": str(file_count),
                        "bytes_done": str(bytes_done),
                        "skipped_count": str(skipped_count),
                        "started_at": str(getattr(job, "started_at", None) or ""),
                        "finished_at": str(getattr(job, "finished_at", None) or ""),
                        "error_detail": str(getattr(job, "summary", None) or ""),
                    },
                )
            except Exception:  # noqa: BLE001
                logger.warning("webhook 推送失败（job=%s）", getattr(job, "id", "?"), exc_info=True)
        if meow_nickname:
            style = await self._load_meow_style()
            try:
                await self._send_meow(
                    nickname=meow_nickname,
                    title=title,
                    # MeoW 默认脱敏（第三方服务）：错误摘要仅在 meow_redact=false 时携带
                    content=content + (f"\n摘要：{summary}" if summary and not meow_redact else ""),
                    msg_type=style.msg_type,
                    html_height=style.html_height,
                    url=style.url,
                    img_url=style.img_url,
                    plan=style.plan,
                )
            except Exception:  # noqa: BLE001
                logger.warning("MeoW 推送失败（job=%s）", getattr(job, "id", "?"), exc_info=True)
        if mail is not None:
            try:
                await self._send_mail(
                    mail,
                    title=title,
                    # 邮件默认不脱敏（运维自有通道）：错误摘要仅在 mail_redact=true 时剔除
                    content=content + (f"\n摘要：{summary}" if summary and not mail.redact else ""),
                )
            except Exception:  # noqa: BLE001
                logger.warning("邮件推送失败（job=%s）", getattr(job, "id", "?"), exc_info=True)

    async def _repo_display_name(self, job: Any) -> str | None:
        """解析 job 归属仓库的显示名（通知正文用）；任何失败都回落 None。

        手动操作（init/unlock/check/dump/repair/recover）没有 ``task_id`` 关联，
        任务名解析必然为 None——此类通知显示仓库名才有排障价值（否则用户只看到
        「（未关联任务）」加一串 job UUID，既不知道是哪个仓库也不知道去哪看详情）。
        """
        repo_id = getattr(job, "repo_id", None)
        if not repo_id or self._repos is None:
            return None
        try:
            repo = await self._repos.get(str(repo_id))
        except Exception:  # noqa: BLE001 - 名称解析失败只影响文案，不影响推送
            logger.warning("解析仓库 %s 显示名失败", repo_id, exc_info=True)
            return None
        return str(getattr(repo, "name", "") or "") or None

    def _render(
        self,
        *,
        job_type: str,
        status: str,
        task_name: str | None,
        job_id: str,
        file_count: int,
        bytes_done: int,
        skipped_count: int,
        started_at: str | None,
        finished_at: str | None,
        summary: str | None,
        include_summary: bool,
        repo_name: str | None = None,
    ) -> tuple[str, str]:
        """渲染标题与正文（``include_summary=False`` = 该渠道脱敏，错误摘要整体剔除不出占位符）。"""
        type_label = {
            "backup": "备份",
            "restore": "恢复",
            "forget": "清理",
            "check": "校验",
            "init": "初始化",
            "unlock": "解锁",
            "repair": "修复",
            "recover": "恢复数据",
            "dump": "导出",
        }.get(job_type, job_type)
        title = f"restic-web：{type_label}任务成功" if status == "success" else f"restic-web：{type_label}任务失败"
        # 任务名缺失（手动操作）→ 显示仓库名 → 再无则「来源：手动操作」；
        # 绝不出现「（未关联任务）」这类零信息量占位（用户看不到是哪个仓库的操作）
        subject = f"任务：{task_name}" if task_name else (
            f"仓库：{repo_name}" if repo_name else "来源：手动操作（未关联任务）"
        )
        skipped_note = f"\nskipped：{skipped_count}（超过阈值，请检查任务日志）" if skipped_count > 0 else ""
        content = (
            f"{subject}\n"
            f"类型：{type_label}（job {job_id}）\n"
            f"状态：{'成功' if status == 'success' else '失败'}\n"
            f"文件数：{file_count}　字节：{bytes_done}{skipped_note}"
        )
        if summary and include_summary:
            content += f"\n摘要：{summary}"
        elif status != "success" and not include_summary:
            # 脱敏渠道（MeoW 默认）：摘要被整体剔除——明示去哪看，别让用户以为失败无原因
            content += "\n（失败详情已脱敏，请前往任务日志查看）"
        return title, content

    async def _ping(self, url: str) -> None:
        """healthchecks GET ping：非 2xx 即抛错（调用方逐渠道捕获）。"""
        async with httpx.AsyncClient(timeout=NOTIFY_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

    async def _send_webhook(
        self,
        *,
        url: str,
        title: str,
        content: str,
        redact: bool,
        variables: dict[str, str] | None = None,
    ) -> None:
        """webhook 推送：POST JSON ``{"title","content"}``；HTTP 非 2xx 即抛错。

        模板（``notify.webhook.template``，可选）：``{var}`` 占位的渲染文本，发送体
        变为 ``{"title","content": <渲染文本>}``。变量分级（方案 §5）：

        - 非敏感摘要（job_id/task_name/type/status/file_count/bytes/skipped/时间）：
          ``variables`` 提供的真实值；
        - 敏感变量（repo/host/paths/error_detail/db_names）：``redact=true`` 渲染为
          空串（直接剔除、不出占位符）；``redact=false``（自建）时 ``error_detail``
          允许携带真实摘要，其余无值变量仍为空串。
        """
        template = ((await self._config.get_value(KEY_WEBHOOK_TEMPLATE)) or "").strip()
        content_out = content
        if template:
            values = dict(variables or {})
            sensitive = ("repo", "host", "paths", "error_detail", "db_names")
            for name in sensitive:
                if name not in values or redact:
                    values[name] = ""
            content_out = template
            for name in set(_SAFE_VARIABLES) | set(sensitive):
                content_out = content_out.replace("{" + name + "}", str(values.get(name, "")))
        async with httpx.AsyncClient(timeout=NOTIFY_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.post(url, json={"title": title, "content": content_out})
            resp.raise_for_status()

    # S3：异常文案只带业务状态码与服务端 msg，不含请求体（凭据/隐私不出日志与 UI）
    async def _send_meow(
        self,
        *,
        nickname: str,
        title: str,
        content: str,
        msg_type: str = MEOW_MSG_TYPE_DEFAULT,
        html_height: int = MEOW_HTML_HEIGHT_DEFAULT,
        url: str = "",
        img_url: str = "",
        plan: str = MEOW_PLAN_DEFAULT,
    ) -> None:
        """MeoW 推送：``POST /{昵称}`` + JSON ``{"title","msg"}``（API 文档 §3.2）。

        契约要点（逐条对齐文档，改这里前先看 ``docs`` 引的官方 API 文档）：

        - **消息内容字段名是 ``msg``**（不是 content/text）：服务端只认 ``msg``，
          字段名写错 → ``{"status":400,"msg":"发送失败，消息内容不能为空"}``；
        - **``msgType`` / ``htmlHeight`` 是查询参数**，不属于 JSON Body（文档 §3.2 明确）；
        - **``url`` / ``imgUrl`` 是可选增强**：走 JSON Body（文档：POST 时 Body 优先于
          同名查询参数）；图标建议 216×216 PNG（§5.7）；
        - **昵称需 URL 编码**（中文/特殊字符；昵称本身不允许含斜杠），中文参数一律编码；
        - **成功判定** = 响应 JSON ``status==200``（该服务 HTTP 恒 200）；
        - **业务状态码优先于 HTTP 状态码**：429/404/403 都在 JSON 的 ``status`` 里
          （HTTP 恒 200），必须按业务码给可操作指引；429 不重试；
        - **发送前先过本地限频**（§5.8/§5.9）：按套餐档位（``plan``）守限——免费版
          3 秒 1 条 + 15/60/1000，高级版/终身版不限频率只守每天 2000/3000 条；
          超限直接丢弃，避免触发服务端 429 与被判攻击。
        """
        await _MEOW_LIMITER.acquire(plan)
        target = f"{MEOW_BASE_URL}/{quote(nickname.strip(), safe='')}"
        params: dict[str, str] = {"msgType": msg_type}
        if msg_type == "html":
            params["htmlHeight"] = str(html_height)
        payload: dict[str, str] = {"title": title, "msg": content}
        # 可选增强只在非空时带上（不传即服务端默认，避免空串被当成有效值）
        if url.strip():
            payload["url"] = url.strip()
        if img_url.strip():
            payload["imgUrl"] = img_url.strip()
        async with httpx.AsyncClient(timeout=NOTIFY_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.post(target, json=payload, params=params)
            # 防御性兜底：文档称 HTTP 恒 200；真出现 HTTP 429 同样按限流处理（不重试）
            if resp.status_code == 429:
                raise RuntimeError(f"MeoW 限流（HTTP 429）：{_MEOW_STATUS_HINTS[429]}")
            resp.raise_for_status()
            try:
                body = resp.json()
            except ValueError as exc:
                raise RuntimeError(f"MeoW 响应非 JSON：{resp.text[:120]}") from exc
            if isinstance(body, dict) and body.get("status") == 200:
                return
            status = body.get("status") if isinstance(body, dict) else None
            detail = str(body.get("msg"))[:120] if isinstance(body, dict) else str(body)[:120]
            hint = _MEOW_STATUS_HINTS.get(status, "") if isinstance(status, int) else ""
            suffix = f"｜{hint}" if hint else ""
            raise RuntimeError(f"MeoW 返回异常（status={status}）：{detail}{suffix}")
