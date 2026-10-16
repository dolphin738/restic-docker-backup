"""R-024 指标与报表路由（P3-B）：E37 指标端点 + E38 备份成功率报表。

鉴权（owner 2026-10-16 裁决 1C）：
- **E37 ``GET /api/metrics``**：``Authorization: Bearer <token>`` 或 session
  cookie **任一通过**（Prometheus scraper 不会存 cookie；令牌库内只存 SHA-256，
  见 :mod:`app.services.metrics`）。两者都不过 → 401；首启未改密 → 403
  （S4/S8 全站压制不因新路由破防）。GET 只读，无 Origin/CSRF 面。
- **E38 ``GET /api/reports/backup-success``**：普通 session 端点（报表页数据源）。

S4 自查：本文件出现 ``require_session`` 字面（:func:`require_metrics_access`
的无令牌回落路径 + E38 显式依赖），机器断言不脱轨；令牌只经 Authorization 头
传递，query 字符串传令牌的形态零出现（S4 红线）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import METRICS_TOKEN_KEY, REPORTS_MAX_DAYS
from app.db.repositories.config_repo import ConfigRepo
from app.errors import ValidationError
from app.schemas.api_schemas import BackupSuccessReportOut, ReportBucketOut
from app.services.metrics import (
    MetricsSnapshot,
    build_backup_success_report,
    collect_metrics,
    render_prometheus,
    verify_metrics_token,
)
from app.services.scheduler import TIMEZONE_KEY

router = APIRouter(prefix="/api", tags=["metrics"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]


async def require_metrics_access(request: Request) -> None:
    """E37 鉴权（裁决 1C）：Bearer 令牌或 session 任一通过；首启门两种路径都过。

    Raises:
        UnauthorizedError: 401（令牌无效/缺失且无有效 session）。
        BootstrapRequiredError: 403（首启未改密，全站压制含本端点）。
    """
    header = request.headers.get("authorization") or ""
    if header[:7].lower() == "bearer ":
        config_repo: ConfigRepo = request.app.state.config_repo
        stored = await config_repo.get_value(METRICS_TOKEN_KEY)
        if verify_metrics_token(header[7:], stored):
            await require_bootstrap_done(request)
            return
    # 无令牌/令牌无效：回落 session（浏览器内查看指标同样可用）
    await require_session(request)
    await require_bootstrap_done(request)


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="E37 Prometheus 指标（R-024；Bearer 令牌或 session 任一通过）",
    dependencies=[Depends(require_metrics_access)],
)
async def get_metrics(request: Request) -> PlainTextResponse:
    """返回 Prometheus exposition format 0.0.4 文本（pull 模型，抓取时现算）。"""
    snapshot: MetricsSnapshot = await collect_metrics(
        request.app.state.job_repo,
        cred_encryption_enabled=request.app.state.encryption.enabled,
    )
    return PlainTextResponse(render_prometheus(snapshot), media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get(
    "/reports/backup-success",
    response_model=BackupSuccessReportOut,
    summary="E38 备份成功率报表（R-024；按配置时区分桶，零运行日补齐）",
    dependencies=_PROTECTED,
)
async def get_backup_success_report(
    request: Request,
    session: SessionContext = Depends(require_session),
    days: int = Query(default=30, description="时间窗天数（1..90）"),
) -> BackupSuccessReportOut:
    """备份成功率日报表：分母只计终态 backup，``interrupted``/``cancelled`` 单列
    （取消/中断既不是成功也不是备份失败，混入分母会扭曲成功率）。

    Raises:
        ValidationError: 422（``days`` 越出 1..``REPORTS_MAX_DAYS``）。
    """
    del session
    if not 1 <= days <= REPORTS_MAX_DAYS:
        raise ValidationError(
            f"days 须为 1~{REPORTS_MAX_DAYS} 的整数",
            data={"days": days, "max": REPORTS_MAX_DAYS},
        )
    config_repo: ConfigRepo = request.app.state.config_repo
    tz_name = (await config_repo.get_value(TIMEZONE_KEY)) or request.app.state.settings.tz
    report = await build_backup_success_report(request.app.state.job_repo, days=days, timezone=tz_name)
    return BackupSuccessReportOut(
        days=report.days,
        timezone=report.timezone,
        from_date=report.from_date,
        to_date=report.to_date,
        buckets=[
            ReportBucketOut(
                date=bucket.date,
                total=bucket.total,
                success=bucket.success,
                failed=bucket.failed,
                ratio=bucket.ratio,
            )
            for bucket in report.buckets
        ],
        total=report.total,
        success=report.success,
        failed=report.failed,
        ratio=report.ratio,
    )
