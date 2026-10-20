"""E39 时间同步服务单测（SNTP 解析 / 探测缓存 / 同步阈值 / 系统时钟校准）。

网络零触达：``_query_server`` 以替身注入；``set_system_clock`` 在 Windows 开发机上
恒走 unsupported 分支（这正是容器外开发的预期形态）。
"""

from __future__ import annotations

import struct
import sys

import pytest

from app.constants import NTP_SERVERS, TIME_SYNC_MAX_OFFSET_MS, TIME_SYNC_MIN_OFFSET_MS
from app.services import time_sync as ts
from app.utils.time import is_valid_timezone


@pytest.fixture(autouse=True)
def _clean_cache():
    ts.reset_probe_cache()
    yield
    ts.reset_probe_cache()


# ---------------------------------------------------------------- SNTP 报文解析（纯函数）


def _sntp_packet(*, mode: int = 4, stratum: int = 2, recv: float = 0.0, tx: float = 0.0) -> bytes:
    """构造 48 字节 SNTP 响应（recv/tx 为 Unix 秒）。

    布局 = 12 个大端 uint32：首字含 LI/VN/Mode/stratum/poll/precision，
    其后 root delay/disp/refid/reference ts，最后 originate/receive/transmit 三个 64 位时间戳
    （解析端取 fields[8..9]=receive、fields[10..11]=transmit）。
    """

    def to_ntp(unix_seconds: float) -> tuple[int, int]:
        ntp = unix_seconds + 2208988800
        whole = int(ntp)
        return whole, int((ntp - whole) * 2**32)

    recv_int, recv_frac = to_ntp(recv)
    tx_int, tx_frac = to_ntp(tx)
    header = (((3 << 3) | mode) << 24) | (stratum << 16) | (4 << 8) | 0xEC
    return struct.pack(
        "!12I",
        header,
        0,
        0,
        0x4C4F434C,  # refid "LOCL"
        0,
        0,
        0,
        0,
        recv_int,
        recv_frac,
        tx_int,
        tx_frac,
    )


def test_parse_offset_and_rtt():
    """offset/rtt 按 RFC 4330 公式计算（服务端比客户端快 2s、报文往返 0.1s）。"""
    t1, t4 = 1_000_000.0, 1_000_000.1
    data = _sntp_packet(recv=t1 + 2.05, tx=t1 + 2.05)
    offset, rtt = ts._parse_sntp_response(data, t1, t4)
    assert offset == pytest.approx(2.0, abs=0.01)
    assert rtt == pytest.approx(0.1, abs=0.01)


def test_parse_rejects_client_mode_and_short_packet():
    with pytest.raises(RuntimeError, match="ntp.bad_response"):
        ts._parse_sntp_response(_sntp_packet(mode=3), 1.0, 1.1)
    with pytest.raises(RuntimeError, match="ntp.bad_response"):
        ts._parse_sntp_response(b"\x24" * 20, 1.0, 1.1)


def test_parse_rejects_kiss_of_death():
    """stratum=0 + server 模式 = KoD（服务端拒绝对时，必须显式失败）。"""
    with pytest.raises(RuntimeError, match="ntp.kod"):
        ts._parse_sntp_response(_sntp_packet(stratum=0), 1.0, 1.1)


# ---------------------------------------------------------------- 探测与缓存


async def test_probe_falls_through_to_next_server(monkeypatch):
    """首服务器失败 → 继续探测下一个；结果按成功服务器返回。"""
    calls: list[str] = []

    async def fake_query(server: str) -> tuple[float, float]:
        calls.append(server)
        if server == NTP_SERVERS[0]:
            raise RuntimeError("ntp.timeout")
        return 0.5, 0.02

    monkeypatch.setattr(ts, "_query_server", fake_query)
    result = await ts.probe_ntp()
    assert result.available is True
    assert result.server == NTP_SERVERS[1]
    assert result.offset_ms == pytest.approx(500.0)
    assert calls == [NTP_SERVERS[0], NTP_SERVERS[1]]


async def test_probe_all_fail_returns_last_reason(monkeypatch):
    async def fake_query(server: str) -> tuple[float, float]:
        raise RuntimeError("ntp.unreachable")

    monkeypatch.setattr(ts, "_query_server", fake_query)
    result = await ts.probe_ntp()
    assert result.available is False
    assert result.reason == "ntp.unreachable"


async def test_query_server_dns_failure_maps_to_unreachable(monkeypatch):
    """DNS 失败（gaierror）→ ntp.unreachable：禁止裸抛 OSError 让端点 500。

    回归护栏：getaddrinfo 曾在 try 块之外，无外网 DNS 的 CI/容器里
    GET /api/system/time 直接 500（而非按契约回 reason）。
    """
    import socket as socket_module

    def raise_gaierror(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise socket_module.gaierror("[Errno 11002] getaddrinfo failed")

    monkeypatch.setattr(socket_module, "getaddrinfo", raise_gaierror)
    with pytest.raises(RuntimeError, match="ntp.unreachable"):
        await ts._query_server("ntp.example.com")


async def test_probe_cache_ttl(monkeypatch):
    """TTL 内复用缓存（前端轮询不得反复打 NTP）；force=True 绕过。"""
    calls: list[str] = []

    async def fake_query(server: str) -> tuple[float, float]:
        calls.append(server)
        return 0.0, 0.01

    monkeypatch.setattr(ts, "_query_server", fake_query)
    await ts.probe_ntp()
    await ts.probe_ntp()
    assert len(calls) == 1
    await ts.probe_ntp(force=True)
    assert len(calls) == 2


async def test_probe_cache_keyed_by_chain(monkeypatch):
    """缓存键含服务器链：改主备配置后不得复用旧链的探测结果。"""
    calls: list[str] = []

    async def fake_query(server: str) -> tuple[float, float]:
        calls.append(server)
        return 0.0, 0.01

    monkeypatch.setattr(ts, "_query_server", fake_query)
    await ts.probe_ntp(["ntp.example.com"])
    # 同链 + TTL 内 → 复用缓存
    await ts.probe_ntp(["ntp.example.com"])
    assert calls == ["ntp.example.com"]
    # 换链 → 缓存不命中，重新探测
    result = await ts.probe_ntp(["ntp.example.com", "ntp2.example.com"])
    assert calls == ["ntp.example.com", "ntp.example.com"]
    assert result.server == "ntp.example.com"


async def test_probe_configured_chain_fails_without_silent_fallback(monkeypatch):
    """配置了主备即只用配置链：全失败明确报错，不静默混入公网默认服务器。"""
    calls: list[str] = []

    async def fake_query(server: str) -> tuple[float, float]:
        calls.append(server)
        raise RuntimeError("ntp.unreachable")

    monkeypatch.setattr(ts, "_query_server", fake_query)
    result = await ts.probe_ntp(["ntp.example.com", "ntp2.example.com"])
    assert result.available is False
    assert result.reason == "ntp.unreachable"
    assert calls == ["ntp.example.com", "ntp2.example.com"]
    assert all(server not in NTP_SERVERS for server in calls)


# ---------------------------------------------------------------- 同步阈值与校准


async def test_sync_noop_below_threshold(monkeypatch):
    """偏移 < 1s：不动系统时钟，回 clock.noop（无谓写入要避免）。"""

    async def fake_query(server: str) -> tuple[float, float]:
        return (TIME_SYNC_MIN_OFFSET_MS / 2000), 0.01

    monkeypatch.setattr(ts, "_query_server", fake_query)
    applied: list[bool] = []
    monkeypatch.setattr(ts, "set_system_clock", lambda offset: (applied.append(True), (True, None))[1])

    outcome = await ts.sync_time()
    assert outcome.ok is True
    assert outcome.applied is False
    assert outcome.reason == "clock.noop"
    assert applied == []


async def test_sync_rejects_absurd_offset(monkeypatch):
    """偏移 > 1 天：坏响应防护，拒绝把时钟改到离谱位置。"""

    async def fake_query(server: str) -> tuple[float, float]:
        return TIME_SYNC_MAX_OFFSET_MS * 2 / 1000, 0.01

    monkeypatch.setattr(ts, "_query_server", fake_query)
    outcome = await ts.sync_time()
    assert outcome.ok is False
    assert outcome.applied is False
    assert outcome.reason == "clock.out_of_range"


async def test_sync_applies_offset(monkeypatch):
    """偏移超阈值 → 调 clock_settime 校准。"""
    seen: list[float] = []

    async def fake_query(server: str) -> tuple[float, float]:
        return 5.0, 0.02

    monkeypatch.setattr(ts, "_query_server", fake_query)
    monkeypatch.setattr(ts, "set_system_clock", lambda offset: (seen.append(offset), (True, None))[1])

    outcome = await ts.sync_time()
    assert outcome.ok is True and outcome.applied is True
    assert outcome.offset_ms == pytest.approx(5000.0)
    assert seen == [5.0]


async def test_sync_propagates_permission_denied(monkeypatch):
    """容器缺 CAP_SYS_TIME：明确失败，绝不假报成功。"""

    async def fake_query(server: str) -> tuple[float, float]:
        return 5.0, 0.02

    monkeypatch.setattr(ts, "_query_server", fake_query)
    monkeypatch.setattr(ts, "set_system_clock", lambda offset: (False, "clock.permission_denied"))

    outcome = await ts.sync_time()
    assert outcome.ok is False
    assert outcome.applied is False
    assert outcome.reason == "clock.permission_denied"


def test_set_system_clock_unsupported_on_windows(monkeypatch):
    """Windows 无 POSIX clock_settime：明确 unsupported，由 UI 指路宿主机对时。"""
    monkeypatch.setattr(sys, "platform", "win32")
    applied, reason = ts.set_system_clock(5.0)
    assert applied is False
    assert reason == "clock.unsupported_platform"


def test_sync_without_ntp_reports_reason(monkeypatch):
    """NTP 不可达：applied=False + reason，禁止静默。"""

    async def fake_query(server: str) -> tuple[float, float]:
        raise RuntimeError("ntp.timeout")

    monkeypatch.setattr(ts, "_query_server", fake_query)
    import asyncio

    outcome = asyncio.run(ts.sync_time())
    assert outcome.ok is False
    assert outcome.reason == "ntp.timeout"


# ---------------------------------------------------------------- 时区名校验（E39 写入侧钩子）


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Asia/Shanghai", True),
        ("UTC", True),
        ("Etc/UTC", True),
        ("America/New_York", True),
        ("Not/AZone", False),
        ("", False),
        (None, False),
        ("../etc/passwd", False),
    ],
)
def test_is_valid_timezone(name, expected):
    assert is_valid_timezone(name) is expected


# ---------------------------------------------------------------- NTP 主备服务器校验与解析（E39）


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ntp.example.com", "ntp.example.com"),
        ("ntp.example.com:123", "ntp.example.com:123"),
        ("  ntp.example.com  ", "ntp.example.com"),
        ("10.0.0.1", "10.0.0.1"),
        ("10.0.0.1:1234", "10.0.0.1:1234"),
        ("cn.pool.ntp.org", "cn.pool.ntp.org"),
        # 主机名带全部 10 个数字（字符集完整性回归：缺任一数字都会误拒合法主机名）
        ("ntp0.example.com", "ntp0.example.com"),
        ("ntp1.example.com", "ntp1.example.com"),
        ("ntp2.example.com", "ntp2.example.com"),
        ("ntp3.example.com", "ntp3.example.com"),
        ("ntp4.example.com", "ntp4.example.com"),
        ("ntp5.example.com", "ntp5.example.com"),
        ("ntp6.example.com", "ntp6.example.com"),
        ("ntp7.example.com", "ntp7.example.com"),
        ("ntp8.example.com", "ntp8.example.com"),
        ("ntp9.example.com", "ntp9.example.com"),
        ("", None),
        ("   ", None),
        ("http://ntp.example.com", None),
        ("https://ntp.example.com", None),
        ("ntp.example.com:0", None),
        ("ntp.example.com:99999", None),
        ("ntp.example.com:abc", None),
        ("ntp..example.com", None),
        (".ntp.example.com", None),
        ("ntp.example.com.", None),
        ("-ntp.example.com", None),
        ("ntp example.com", None),
        ("ntp.example.com/path", None),
        ("ntp.example.com\n", "ntp.example.com"),
        ("ntp.example.com\t", "ntp.example.com"),
        ("ntp.example.com ntp2.example.com", None),
        ("x" * 261, None),
    ],
)
def test_validate_ntp_server(raw, expected):
    assert ts.validate_ntp_server(raw) == expected


def test_resolve_ntp_servers_primary_backup_order():
    """主 → 备排序、去重；皆空回落内置默认链。"""
    assert ts.resolve_ntp_servers("a.example.com", "b.example.com") == ["a.example.com", "b.example.com"]
    assert ts.resolve_ntp_servers("a.example.com", "a.example.com") == ["a.example.com"]
    assert ts.resolve_ntp_servers("a.example.com", None) == ["a.example.com"]
    assert ts.resolve_ntp_servers(None, "b.example.com") == ["b.example.com"]
    assert ts.resolve_ntp_servers("", "  ") == list(NTP_SERVERS)
    assert ts.resolve_ntp_servers(None, None) == list(NTP_SERVERS)


async def test_sync_uses_passed_chain(monkeypatch):
    """sync_time 透传探测链给 probe_ntp（路由层按主备配置解析后传入）。"""
    seen: list[tuple[str, ...] | None] = []
    original = ts.probe_ntp

    async def spy(servers=None, *, force: bool = False):
        seen.append(tuple(servers) if servers else None)
        return await original(servers, force=force)

    monkeypatch.setattr(ts, "probe_ntp", spy)

    async def fake_query(server: str) -> tuple[float, float]:
        return 0.0, 0.01

    monkeypatch.setattr(ts, "_query_server", fake_query)
    outcome = await ts.sync_time(["ntp.example.com"])
    assert outcome.reason == "clock.noop"
    assert seen == [("ntp.example.com",)]
