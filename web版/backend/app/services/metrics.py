"""R-024 Prometheus 指标与备份成功率报表（P3-B）。

设计：
- **pull 模型、无进程内计数器**：指标在每次抓取时从 jobs 表即时聚合——单进程
  asyncio + SQLite 单库下这是最简且无重启漂移的形态（计数本来就在库里）；
- **指标端点鉴权（owner 裁决 1C）**：``Authorization: Bearer <token>`` 或
  session cookie **任一通过**。令牌走 config 键 ``metrics.token``，**库内只存
  SHA-256**（与 session token 同口径，S3）；界面设置，env 同步不映射（env 写
  原值会绕过硬哈希）；
- **文本格式**：Prometheus exposition format 0.0.4，手写渲染（指标面小，
  不引第三方客户端库）；标签值做最小转义；
- **报表分桶走配置时区**（C8：日期语义只经 ``utils/time``），UTC ISO 文本在
  Python 侧换算后分桶，不在 SQL 里 substr。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import Any

from app.constants import REPORTS_MAX_DAYS
from app.db.repositories.job_repo import Job, JobRepo
from app.utils.time import now_utc, parse_iso, resolve_timezone, utc_iso

METRIC_PREFIX = "restic_web"

# 进程启动时刻（Prometheus 惯例 process_start_time_seconds；模块导入即进程启动）
_PROCESS_STARTED_AT = time.time()

# 报表成功率口径：分母只计终态 backup；成功 = success。interrupted/cancelled
# 单列（取消/中断既不是成功也不是备份失败，混入分母会扭曲成功率）。
_TERMINAL_STATUSES = ("success", "failed", "interrupted", "cancelled")


# ---------------------------------------------------------------- 令牌（SHA-256 硬哈希）


def hash_metrics_token(raw: str) -> str:
    """令牌硬哈希（入库形态）：SHA-256 hex。

    Args:
        raw: 用户设置的令牌原文（设置后不再可读，与 session token 同口径）。

    Returns:
        SHA-256 hex 摘要。
    """
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def verify_metrics_token(raw: str, stored_hash: str | None) -> bool:
    """常量时间比对（防时序侧信道）；未设令牌恒 False。"""
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_metrics_token(raw), stored_hash.strip())


# ---------------------------------------------------------------- 指标快照与渲染


@dataclass(frozen=True)
class MetricsSnapshot:
    """一次抓取的指标快照（渲染与断言共用，避免字符串手工比对）。"""

    backup_jobs_by_status: dict[str, int]
    backup_success_ratio: float | None
    last_backup: Job | None
    last_backup_success_at: float | None
    jobs_active: int


def _epoch(iso_text: str | None) -> float | None:
    """ISO 8601 UTC 文本 → epoch 秒（C8：解析只经 utils/time；不可解析返回 None）。"""
    if not iso_text:
        return None
    moment = parse_iso(iso_text)
    return moment.timestamp() if moment is not None else None


async def collect_metrics(job_repo: JobRepo) -> MetricsSnapshot:
    """从库里聚合指标快照（pull 模型；每次抓取现算，无计数器漂移）。"""
    counts = await job_repo.backup_status_counts()
    terminal_total = sum(counts.get(status, 0) for status in _TERMINAL_STATUSES)
    success_total = counts.get("success", 0)
    ratio = (success_total / terminal_total) if terminal_total else None
    last_backup = await job_repo.last_backup_terminal()
    last_success_at = _epoch(last_backup.finished_at) if last_backup and last_backup.status == "success" else None
    return MetricsSnapshot(
        backup_jobs_by_status=counts,
        backup_success_ratio=ratio,
        last_backup=last_backup,
        last_backup_success_at=last_success_at,
        jobs_active=await job_repo.count_active(),
    )


def _escape_label(value: str) -> str:
    """Prometheus 标签值最小转义（反斜杠/双引号/换行）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labels: dict[str, str] | None) -> str:
    """渲染标签段（``{k="v",...}``；无标签返回空串）。"""
    if not labels:
        return ""
    rendered = ",".join(f'{key}="{_escape_label(str(val))}"' for key, val in sorted(labels.items()))
    return f"{{{rendered}}}"


def _gauge(name: str, value: float, help_text: str, labels: dict[str, str] | None = None) -> str:
    """渲染单样本指标族（HELP + TYPE + 样本行）。"""
    return f"# HELP {name} {help_text}\n# TYPE {name} gauge\n{name}{_render_labels(labels)} {value}\n"


def _family(name: str, help_text: str, samples: list[tuple[dict[str, str] | None, float]]) -> str:
    """渲染多样本指标族：**HELP/TYPE 只出一次**，随后每标签值一行样本。

    exposition 规范要求同一指标名的 HELP/TYPE 唯一——按样本重复输出虽被多数
    scraper 容忍，但严格来说是非法格式（本函数的存在即护栏）。
    """
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    lines.extend(f"{name}{_render_labels(labels)} {value}" for labels, value in samples)
    return "\n".join(lines) + "\n"


def render_prometheus(snapshot: MetricsSnapshot) -> str:
    """渲染 Prometheus exposition format 0.0.4 文本。"""
    parts: list[str] = [
        _gauge(
            f"{METRIC_PREFIX}_info",
            1,
            "restic-web 构建信息",
            {"version": "1.0.0"},
        ),
        _gauge(f"{METRIC_PREFIX}_process_start_time_seconds", _PROCESS_STARTED_AT, "进程启动时刻（epoch 秒）"),
        _gauge(f"{METRIC_PREFIX}_jobs_active", snapshot.jobs_active, "活跃任务数（running/pending，全类型）"),
        _family(
            f"{METRIC_PREFIX}_backup_jobs_total",
            "backup 任务累计数（按终态）",
            [
                ({"status": status}, snapshot.backup_jobs_by_status.get(status, 0))
                for status in _TERMINAL_STATUSES
            ],
        ),
        _gauge(
            f"{METRIC_PREFIX}_backup_success_ratio",
            snapshot.backup_success_ratio if snapshot.backup_success_ratio is not None else -1,
            "backup 成功率（success/终态总数；无历史时为 -1）",
        ),
    ]
    last = snapshot.last_backup
    if last is not None:
        last_run = _epoch(last.finished_at) or _epoch(last.created_at) or -1
        duration = -1
        if last.started_at and last.finished_at:
            started, finished = _epoch(last.started_at), _epoch(last.finished_at)
            if started is not None and finished is not None:
                duration = finished - started
        last_skipped = last.skipped_count
    else:
        last_run, duration, last_skipped = -1, -1, -1
    parts.append(
        _gauge(
            f"{METRIC_PREFIX}_backup_last_run_timestamp_seconds",
            last_run,
            "最近一次终态 backup 的时刻（epoch 秒；无历史为 -1）",
        )
    )
    parts.append(
        _gauge(
            f"{METRIC_PREFIX}_backup_last_success_timestamp_seconds",
            snapshot.last_backup_success_at if snapshot.last_backup_success_at is not None else -1,
            "最近一次成功 backup 的时刻（epoch 秒；无成功为 -1）",
        )
    )
    parts.append(
        _gauge(
            f"{METRIC_PREFIX}_backup_last_duration_seconds",
            duration,
            "最近一次终态 backup 的耗时（epoch 秒差；不可计算为 -1）",
        )
    )
    parts.append(
        _gauge(
            f"{METRIC_PREFIX}_backup_last_skipped_files",
            last_skipped,
            "最近一次终态 backup 的跳过文件数（R-012 skipped；无历史为 -1）",
        )
    )
    return "".join(parts)


# ---------------------------------------------------------------- 成功率报表


@dataclass(frozen=True)
class ReportBucket:
    """报表单日桶。"""

    date: str
    total: int
    success: int
    failed: int

    @property
    def ratio(self) -> float | None:
        """当日成功率（无运行日为 None，前端展示为 —）。"""
        return (self.success / self.total) if self.total else None


@dataclass(frozen=True)
class BackupSuccessReport:
    """备份成功率报表（按配置时区分桶，零运行日补齐）。"""

    days: int
    timezone: str
    from_date: str
    to_date: str
    buckets: list[ReportBucket]
    total: int
    success: int
    failed: int

    @property
    def ratio(self) -> float | None:
        """整窗成功率（无运行为 None）。"""
        return (self.success / self.total) if self.total else None


def _local_date(iso_text: str, tz: Any) -> str | None:
    """UTC ISO 文本 → 配置时区的本地日期（YYYY-MM-DD；C8 只经 utils/time）。"""
    moment = parse_iso(iso_text)
    return moment.astimezone(tz).strftime("%Y-%m-%d") if moment is not None else None


async def build_backup_success_report(job_repo: JobRepo, *, days: int, timezone: str) -> BackupSuccessReport:
    """聚合备份成功率报表。

    Args:
        job_repo: 任务仓储。
        days: 时间窗天数（1..``REPORTS_MAX_DAYS``，由路由层校验）。
        timezone: 配置时区名（C8：经 ``resolve_timezone`` 解析）。

    Returns:
        按本地日期升序的日报表（零运行日补齐 total=0）。
    """
    tz = resolve_timezone(timezone)
    # 窗口防御性收敛（路由层已校验；服务层不信任调用方，免无界查询）
    days = max(1, min(int(days), REPORTS_MAX_DAYS))
    today = now_utc().astimezone(tz).date()
    from_date = today - timedelta(days=days - 1)
    # 窗口下界 = **本地零点**换算回 UTC——直接用 UTC 零点会在正偏移时区
    # （如 Asia/Shanghai +8）漏掉首日 00:00–08:00 的本地作业
    since = utc_iso(datetime.combine(from_date, dt_time.min, tzinfo=tz).astimezone(UTC))

    buckets: dict[str, ReportBucket] = {}
    for offset in range(days):
        day = (from_date + timedelta(days=offset)).isoformat()
        buckets[day] = ReportBucket(date=day, total=0, success=0, failed=0)
    for created_at, status in await job_repo.backup_window_rows(since=since):
        day = _local_date(created_at, tz)
        bucket = buckets.get(day) if day else None
        if bucket is None:
            continue  # 时区边界归位到窗外（理论仅发生于跨日切换瞬间）
        total = bucket.total + 1
        success = bucket.success + (1 if status == "success" else 0)
        failed = bucket.failed + (1 if status == "failed" else 0)
        buckets[day] = ReportBucket(date=day, total=total, success=success, failed=failed)

    # 降序（最新日期在最上）：报表是「最近发生了什么」的视角，用户第一眼看的是今天/昨天；
    # 升序会把最早一天推到首屏，与「默认看最新」的直觉相反。
    ordered = [buckets[key] for key in sorted(buckets, reverse=True)]
    total = sum(bucket.total for bucket in ordered)
    success = sum(bucket.success for bucket in ordered)
    failed = sum(bucket.failed for bucket in ordered)
    return BackupSuccessReport(
        days=days,
        timezone=timezone,
        from_date=from_date.isoformat(),
        to_date=today.isoformat(),
        buckets=ordered,
        total=total,
        success=success,
        failed=failed,
    )
