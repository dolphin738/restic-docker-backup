"""E39 时间同步：NTP 对时探测 + 系统时钟校准。

设计口径（逐条对应裁决/红线）：

- **探测零依赖**：SNTP（RFC 4330）单发单收，纯 asyncio + 标准库 socket，不引第三方库；
  候选服务器按序探测（默认 :data:`~app.constants.NTP_SERVERS`，可经配置键
  ``time_sync.ntp_primary`` / ``time_sync.ntp_backup`` 设置主备地址），任一成功即用；
- **失败不静默**：超时/不可达/KoD/坏包一律回带 reason 机器码，UI 转译为可操作文案；
- **结果缓存**：探测结果进程内 TTL 缓存（:data:`~app.constants.NTP_PROBE_CACHE_SECONDS`）——
  前端轮询服务器时间时不得把公网 NTP 池当心跳打；手动同步走 ``force=True`` 绕过缓存；
- **校准有边界**：偏移低于 ``TIME_SYNC_MIN_OFFSET_MS`` 不动时钟（无谓写入），
  高于 ``TIME_SYNC_MAX_OFFSET_MS`` 直接拒绝（防坏响应把时钟改到离谱位置）；
- **改时钟不静默**：只走 libc ``clock_settime``（CLOCK_REALTIME），不经 shell（S9 延续）；
  Windows 无对应 POSIX 调用、容器缺 ``CAP_SYS_TIME`` 时均明确失败并回 reason 码，
  由 UI 指路「宿主机 chrony / systemd-timesyncd」，绝不假报成功；
- **无时区语义**：本模块只测时长与绝对时刻，不碰 zoneinfo（C8：时区解析只在
  :mod:`app.utils.time`）。
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import errno
import logging
import socket
import struct
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

from app.constants import (
    NTP_PORT,
    NTP_PROBE_CACHE_SECONDS,
    NTP_SERVERS,
    NTP_TIMEOUT_SECONDS,
    TIME_SYNC_MAX_OFFSET_MS,
    TIME_SYNC_MIN_OFFSET_MS,
)

logger = logging.getLogger(__name__)

# E39 NTP 服务器配置键（主备各一；留空回落内置默认链 NTP_SERVERS）。
# 命名沿用 notify.* / behavior.* / dump.* 的点号命名空间；与 timezone 同理**不进**
# 配置导出（_EXPORT_CONFIG_PREFIXES 只含 behavior./notify.）——内网 NTP 地址属环境相关。
NTP_PRIMARY_KEY = "time_sync.ntp_primary"
NTP_BACKUP_KEY = "time_sync.ntp_backup"

# NTP 纪元差：1900-01-01 → 1970-01-01（秒）
_NTP_EPOCH_DELTA = 2208988800
_NTP_PACKET_SIZE = 48
# client 请求首字节：LI=0（无告警）/ VN=3（SNTPv3）/ Mode=3（client）
_CLIENT_MODE_BYTE = 0x1B
# 响应 Mode=4（server）；stratum=0 且 refid 为 KoD 时表示服务端拒绝对时
_SERVER_MODE = 4
# CLOCK_REALTIME（Linux clockid 0）
_CLOCK_REALTIME = 0


@dataclass(frozen=True)
class NtpProbeResult:
    """NTP 探测结果（失败必须带 reason 机器码，禁止静默）。"""

    available: bool
    server: str | None
    offset_ms: float | None
    rtt_ms: float | None
    reason: str | None


@dataclass(frozen=True)
class TimeSyncOutcome:
    """一次时间同步的结论（applied=False 时必须带 reason）。"""

    ok: bool
    applied: bool
    offset_ms: float | None
    server: str | None
    reason: str | None


def _ntp_to_unix(seconds: int, fraction: int) -> float:
    """NTP 64 位时间戳（秒 + 小数）→ Unix 纪元秒。"""
    return (seconds - _NTP_EPOCH_DELTA) + fraction / 2**32


def _parse_sntp_response(data: bytes, t1: float, t4: float) -> tuple[float, float]:
    """解析 SNTP 响应，返回 ``(offset_seconds, rtt_seconds)``（纯函数，可单测）。

    Args:
        data: 服务端响应报文（至少 48 字节）。
        t1: 客户端发送时刻（Unix 秒）。
        t4: 客户端接收时刻（Unix 秒）。

    Raises:
        RuntimeError: reason 机器码（``ntp.bad_response`` / ``ntp.kod``）。
    """
    if len(data) < _NTP_PACKET_SIZE or (data[0] & 0x07) != _SERVER_MODE:
        raise RuntimeError("ntp.bad_response")
    if data[1] == 0:
        # stratum=0 + server 模式 = KoD（kiss-of-death，服务端拒绝对时）
        raise RuntimeError("ntp.kod")

    fields = struct.unpack("!12I", data[:_NTP_PACKET_SIZE])
    t2 = _ntp_to_unix(fields[8], fields[9])  # 服务端接收时刻
    t3 = _ntp_to_unix(fields[10], fields[11])  # 服务端发送时刻
    offset = ((t2 - t1) + (t3 - t4)) / 2
    rtt = (t4 - t1) - (t3 - t2)
    return offset, rtt


async def _query_server(server: str) -> tuple[float, float]:
    """单次 SNTP 查询；返回 ``(offset_seconds, rtt_seconds)``。

    Raises:
        RuntimeError: reason 机器码（``ntp.timeout`` / ``ntp.unreachable`` /
            ``ntp.bad_response`` / ``ntp.kod``）。
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(server, NTP_PORT, type=socket.SOCK_DGRAM)
        family, sock_type, proto, _canonname, address = infos[0]
        sock = socket.socket(family, sock_type, proto)
        sock.setblocking(False)
    except OSError as exc:
        # DNS 解析失败（gaierror）与建连失败同归「不可达」：禁止裸抛 OSError 变 500
        raise RuntimeError("ntp.unreachable") from exc
    try:
        await loop.sock_connect(sock, address)
        t1 = time.time()
        await loop.sock_sendall(sock, bytes([_CLIENT_MODE_BYTE]) + b"\x00" * (_NTP_PACKET_SIZE - 1))
        data = await asyncio.wait_for(loop.sock_recv(sock, _NTP_PACKET_SIZE), NTP_TIMEOUT_SECONDS)
        t4 = time.time()
    except TimeoutError as exc:
        raise RuntimeError("ntp.timeout") from exc
    except OSError as exc:
        raise RuntimeError("ntp.unreachable") from exc
    finally:
        sock.close()

    return _parse_sntp_response(data, t1, t4)


# ---------------------------------------------------------------- NTP 服务器配置（主备）


def validate_ntp_server(raw: str) -> str | None:
    """校验单个 NTP 服务器地址：``主机名`` 或 ``主机名:端口``（IPv4 亦可）。

    非法返回 ``None``（调用方转 422，坏地址落库只会在探测时才炸，表现为
    「保存成功但同步失败」，写入侧必须前置拒绝）：

    - 拒 scheme 前缀（``http://`` 等——SNTP 是裸 UDP，用户常误填带协议的地址）；
    - 拒空白/控制字符/超长（DNS 名上限 253 + ``:端口`` 余量）；
    - 端口段须为 1~65535 纯数字；主机段仅 ``[A-Za-z0-9.-]``（拒 IPv6 方括号等
      暂不支持的形态，错误信息直说）。
    """
    value = raw.strip()
    if not value or len(value) > 260:
        return None
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    if "://" in value:
        return None

    host, sep, port = value.rpartition(":")
    if not sep:
        host, port = value, ""
    if not host or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-." for ch in host):
        return None
    if host.startswith("-") or host.endswith("-") or ".." in host or host.startswith(".") or host.endswith("."):
        return None
    if port and (not port.isdigit() or not (1 <= int(port) <= 65535)):
        return None
    return value


def resolve_ntp_servers(primary: str | None, backup: str | None) -> list[str]:
    """主备配置 → 生效探测链：非空项按「主 → 备」排序、去重（保序）。

    两者皆空 → 回落内置默认链 :data:`~app.constants.NTP_SERVERS`。
    一旦配置了主/备任一，就**只**用配置链（不静默混入公网默认服务器——
    内网对时场景把查询泄露给公网 NTP 池不可接受，全失败也应明确报错）。
    """
    chain: list[str] = []
    for candidate in (primary or "", backup or ""):
        server = candidate.strip()
        if server and server not in chain:
            chain.append(server)
    return chain or list(NTP_SERVERS)


# 探测结果缓存（进程内 TTL；键含服务器链——改主备配置后不得复用旧链的结果）。
# 测试可经 reset_probe_cache 清零
_probe_cache: tuple[tuple[str, ...], float, NtpProbeResult] | None = None


def reset_probe_cache() -> None:
    """清空探测缓存（测试用）。"""
    global _probe_cache
    _probe_cache = None


async def probe_ntp(servers: Sequence[str] | None = None, *, force: bool = False) -> NtpProbeResult:
    """按序探测候选 NTP 服务器；任一成功即返回，全失败回最后失败原因。

    Args:
        servers: 生效探测链（None = 内置默认链；由路由层按主备配置解析后传入）。
        force: 绕过 30s 进程内缓存（手动同步必须 force，展示轮询不得）。
    """
    global _probe_cache
    chain: tuple[str, ...] = tuple(servers) if servers else tuple(NTP_SERVERS)
    if not force and _probe_cache is not None:
        cached_chain, cached_at, cached_result = _probe_cache
        if cached_chain == chain and (time.monotonic() - cached_at) < NTP_PROBE_CACHE_SECONDS:
            return cached_result

    last_reason = "ntp.unreachable"
    for server in chain:
        try:
            offset, rtt = await _query_server(server)
        except RuntimeError as exc:
            last_reason = str(exc)
            logger.info("NTP 探测失败 server=%s reason=%s", server, last_reason)
            continue
        result = NtpProbeResult(
            available=True,
            server=server,
            offset_ms=offset * 1000.0,
            rtt_ms=rtt * 1000.0,
            reason=None,
        )
        _probe_cache = (chain, time.monotonic(), result)
        return result

    failed = NtpProbeResult(available=False, server=None, offset_ms=None, rtt_ms=None, reason=last_reason)
    _probe_cache = (chain, time.monotonic(), failed)
    return failed


def set_system_clock(offset_seconds: float) -> tuple[bool, str | None]:
    """按偏移校准系统时钟；返回 ``(applied, reason_code)``。

    只走 libc ``clock_settime(CLOCK_REALTIME)``，不经 shell（S9 延续）。
    Windows 无对应 POSIX 调用、容器缺 ``CAP_SYS_TIME``（EPERM）时明确失败。
    """
    if sys.platform == "win32":
        return False, "clock.unsupported_platform"

    class _Timespec(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    except OSError as exc:
        logger.warning("libc 加载失败，无法校准系统时钟：%s", exc)
        return False, "clock.failed"

    target = time.time() + offset_seconds
    whole = int(target)
    spec = _Timespec(whole, int((target - whole) * 1_000_000_000))
    if libc.clock_settime(_CLOCK_REALTIME, ctypes.byref(spec)) != 0:
        code = ctypes.get_errno()
        if code == errno.EPERM:
            return False, "clock.permission_denied"
        logger.warning("clock_settime 失败 errno=%s", code)
        return False, "clock.failed"
    return True, None


async def sync_time(servers: Sequence[str] | None = None, *, force: bool = False) -> TimeSyncOutcome:
    """NTP 对时并按需校准系统时钟（有权限时）。

    Args:
        servers: 生效探测链（None = 内置默认链；由路由层按主备配置解析后传入）。
        force: 绕过探测缓存（手动同步必须 force）。
    """
    probe = await probe_ntp(servers, force=force)
    if not probe.available or probe.offset_ms is None:
        return TimeSyncOutcome(
            ok=False, applied=False, offset_ms=None, server=None, reason=probe.reason or "ntp.unreachable"
        )
    offset_ms = probe.offset_ms
    if abs(offset_ms) < TIME_SYNC_MIN_OFFSET_MS:
        return TimeSyncOutcome(ok=True, applied=False, offset_ms=offset_ms, server=probe.server, reason="clock.noop")
    if abs(offset_ms) > TIME_SYNC_MAX_OFFSET_MS:
        logger.warning("NTP 偏移超限拒绝校准 server=%s offset_ms=%.1f", probe.server, offset_ms)
        return TimeSyncOutcome(
            ok=False, applied=False, offset_ms=offset_ms, server=probe.server, reason="clock.out_of_range"
        )

    applied, reason = set_system_clock(offset_ms / 1000.0)
    if applied:
        logger.info("系统时钟已按 NTP 校准 server=%s offset_ms=%.1f", probe.server, offset_ms)
    return TimeSyncOutcome(ok=applied, applied=applied, offset_ms=offset_ms, server=probe.server, reason=reason)
