"""E39 系统时间路由：服务器时间展示 + NTP 对时 + 系统时钟校准。

配套前端入口：系统设置页「时区」卡片（时区手动设置走 E07 ``timezone`` 键，
本文件只承担「时间展示与校准」两个探测/动作端点）。

鉴权（S4/S5/S8）：两端点均挂 ``require_session`` + ``require_bootstrap_done``
（首启未改密时全站压制不因新路由破防）；写方法另经全局 CSRF/Origin 中间件；
无 query token 形态。

审计（S6）：``time.sync`` 是改系统状态的危险动作，埋点 ``AUDIT_TIME_SYNC``；
detail 只含 applied/offset/server/reason——零凭据面（S3 延续）。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_bootstrap_done, require_session
from app.auth.session import SessionContext
from app.constants import AUDIT_TIME_SYNC
from app.db.repositories.config_repo import ConfigRepo
from app.schemas.api_schemas import NtpProbeOut, SystemTimeOut, TimeSyncOut
from app.services.scheduler import TIMEZONE_KEY
from app.services.time_sync import (
    NTP_BACKUP_KEY,
    NTP_PRIMARY_KEY,
    TimeSyncOutcome,
    probe_ntp,
    resolve_ntp_servers,
    sync_time,
)
from app.utils.time import now_utc, resolve_timezone, utc_iso

router = APIRouter(prefix="/api/system", tags=["system"])

_PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]


def _format_offset(offset: timedelta | None) -> str:
    """时区偏移 → ``+08:00`` 形态（None 按 +00:00）。"""
    if offset is None:
        return "+00:00"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


async def _resolve_ntp_chain(config: ConfigRepo) -> list[str]:
    """主备配置键 → 生效探测链（皆空 = 内置默认链）。"""
    primary = (await config.get_value(NTP_PRIMARY_KEY)) or ""
    backup = (await config.get_value(NTP_BACKUP_KEY)) or ""
    return resolve_ntp_servers(primary, backup)


@router.get(
    "/time",
    response_model=SystemTimeOut,
    summary="E39 服务器时间与 NTP 偏差",
    dependencies=_PROTECTED,
)
async def get_system_time(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> SystemTimeOut:
    """服务器 UTC 时刻 + 生效时区（config.timezone → settings.tz）本地时间 + NTP 偏差。

    NTP 探测结果带 30s 进程内缓存（前端轮询不打爆公网 NTP 池）；探测链按主备配置
    解析（``time_sync.ntp_primary`` / ``time_sync.ntp_backup``，皆空回落内置默认链），
    生效链随响应透传供前端展示。
    """
    del session
    settings = request.app.state.settings
    config: ConfigRepo = request.app.state.config_repo
    tz_name = ((await config.get_value(TIMEZONE_KEY)) or "").strip() or settings.tz
    chain = await _resolve_ntp_chain(config)

    now = now_utc()
    local = now.astimezone(resolve_timezone(tz_name))
    probe = await probe_ntp(chain)
    return SystemTimeOut(
        server_time=utc_iso(now),
        timezone=tz_name,
        local_time=local.isoformat(timespec="seconds"),
        utc_offset=_format_offset(local.utcoffset()),
        epoch_ms=int(now.timestamp() * 1000),
        ntp_servers=chain,
        ntp=NtpProbeOut(
            available=probe.available,
            server=probe.server,
            offset_ms=probe.offset_ms,
            rtt_ms=probe.rtt_ms,
            reason=probe.reason,
        ),
    )


@router.post(
    "/time/sync",
    response_model=TimeSyncOut,
    summary="E39 时间同步（NTP 对时 + 校准系统时钟）",
    dependencies=_PROTECTED,
)
async def post_time_sync(
    request: Request,
    session: SessionContext = Depends(require_session),
) -> TimeSyncOut:
    """按 NTP 结果校准系统时钟（有权限时）；失败/未应用必须带 reason。

    边界：偏移 < 1s 不动时钟（``clock.noop``）、> 1 天拒绝（``clock.out_of_range``）、
    容器缺 ``CAP_SYS_TIME``（``clock.permission_denied``）与 Windows
    （``clock.unsupported_platform``）均明确回原因，由 UI 指路宿主机对时。

    探测链按主备配置解析（同 GET /time）；``force=True`` 绕过 30s 探测缓存。
    """
    del session
    config: ConfigRepo = request.app.state.config_repo
    chain = await _resolve_ntp_chain(config)
    outcome: TimeSyncOutcome = await sync_time(chain, force=True)
    await request.app.state.audit.record(
        AUDIT_TIME_SYNC,
        target="system.time",
        detail={
            "applied": outcome.applied,
            "offset_ms": outcome.offset_ms,
            "server": outcome.server,
            "reason": outcome.reason,
        },
        result="ok" if outcome.ok else "failed",
    )
    return TimeSyncOut(
        ok=outcome.ok,
        applied=outcome.applied,
        offset_ms=outcome.offset_ms,
        server=outcome.server,
        reason=outcome.reason,
    )
