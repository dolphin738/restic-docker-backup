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
- **MeoW 成功判定**：响应 JSON ``status==200``（该服务 HTTP 恒 200）；429 记日志不重试；
- healthchecks：``GET {url}/start``（on_start）→ 成功 ``GET {url}`` / 失败 ``GET {url}/fail``。

配置键（§4.3 notify.*，值统一字符串存储）：``notify.healthchecks_url``（敏感脱敏）、
``notify.webhook.url``（敏感脱敏）、``notify.webhook.template``（可选模板，``{var}`` 占位）、
``notify.meow.nickname``、``notify.on_success``、``notify.skipped_threshold``、
``notify.webhook.redact``、``notify.meow.redact``。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.constants import (
    NOTIFY_HTTP_TIMEOUT_SECONDS,
    NOTIFY_SKIPPED_THRESHOLD_DEFAULT,
    NOTIFY_SKIPPED_THRESHOLD_MAX,
)
from app.db.repositories.config_repo import ConfigRepo

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
KEY_MEOW_REDACT = "notify.meow.redact"

# 渠道级脱敏缺省（架构 §7.6：MeoW=true、自建 webhook=false）
DEFAULT_WEBHOOK_REDACT = False
DEFAULT_MEOW_REDACT = True

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


@dataclass(frozen=True)
class ChannelResult:
    """单渠道推送结果（E24 测试逐渠道明示，禁止静默）。"""

    channel: str
    ok: bool
    reason: str | None = None


class Notifier:
    """三渠道通知器（运行期读 config，配置改动即时生效，无需重启）。"""

    def __init__(self, config: ConfigRepo) -> None:
        """构造通知器。

        Args:
            config: 配置仓储（每次推送前现读，E23 保存即生效）。
        """
        self._config = config

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
            try:
                await self._send_meow(nickname=meow_nickname, title=title, content=content)
                results.append(ChannelResult(channel="meow", ok=True))
            except Exception as exc:  # noqa: BLE001
                results.append(ChannelResult(channel="meow", ok=False, reason=str(exc)[:200]))

        latency_ms = int((time.monotonic() - started) * 1000)
        if not results:
            return False, latency_ms, results, "未配置任何通知渠道（请先填写 healthchecks / webhook / MeoW 任一）"
        ok = all(item.ok for item in results)
        reason = None if ok else "；".join(
            f"{item.channel}: {item.reason}" for item in results if not item.ok
        )
        return ok, latency_ms, results, reason

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
        if not (healthchecks_url or webhook_url or meow_nickname):
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
                                      include_summary=False)
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
            try:
                await self._send_meow(
                    nickname=meow_nickname,
                    title=title,
                    # MeoW 默认脱敏（第三方服务）：错误摘要仅在 meow_redact=false 时携带
                    content=content + (f"\n摘要：{summary}" if summary and not meow_redact else ""),
                )
            except Exception:  # noqa: BLE001
                logger.warning("MeoW 推送失败（job=%s）", getattr(job, "id", "?"), exc_info=True)

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
    ) -> tuple[str, str]:
        """渲染标题与正文（``include_summary=False`` = 该渠道脱敏，错误摘要整体剔除不出占位符）。"""
        type_label = {"backup": "备份", "restore": "恢复", "forget": "清理", "check": "校验"}.get(job_type, job_type)
        title = f"restic-web：{type_label}任务成功" if status == "success" else f"restic-web：{type_label}任务失败"
        skipped_note = f"\nskipped：{skipped_count}（超过阈值，请检查任务日志）" if skipped_count > 0 else ""
        content = (
            f"任务：{task_name or '（未关联任务）'}\n"
            f"类型：{type_label}（job {job_id}）\n"
            f"状态：{'成功' if status == 'success' else '失败'}\n"
            f"文件数：{file_count}　字节：{bytes_done}{skipped_note}"
        )
        if summary and include_summary:
            content += f"\n摘要：{summary}"
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

    async def _send_meow(self, *, nickname: str, title: str, content: str) -> None:
        """MeoW 推送：POST JSON；成功判定 = 响应 JSON ``status==200``（HTTP 恒 200）。"""
        url = f"{MEOW_BASE_URL}/{nickname.strip()}"
        async with httpx.AsyncClient(timeout=NOTIFY_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.post(url, json={"title": title, "content": content})
            if resp.status_code == 429:
                raise RuntimeError("MeoW 限流（429），已放弃本次推送（不重试）")
            resp.raise_for_status()
            try:
                payload = resp.json()
            except ValueError as exc:
                raise RuntimeError(f"MeoW 响应非 JSON：{resp.text[:120]}") from exc
            if not (isinstance(payload, dict) and payload.get("status") == 200):
                raise RuntimeError(f"MeoW 返回异常：{str(payload)[:120]}")
