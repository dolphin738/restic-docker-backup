"""时间工具：统一 UTC 存储 + zoneinfo 展示 + 子进程 TZ 注入（§4.1 时区条目）。

DB 中所有时间列均为 TEXT（ISO 8601 UTC，形如 ``2026-10-10T01:38:00Z``），
展示层按配置时区（config.timezone，回退容器 TZ）格式化。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> datetime:
    """返回当前 UTC 时间（带 tzinfo）。"""
    return datetime.now(UTC)


def utc_iso(moment: datetime | None = None) -> str:
    """将时间格式化为落库用的 ISO 8601 UTC 字符串。

    Args:
        moment: 待格式化时间；为 None 时取当前时间。

    Returns:
        形如 ``2026-10-10T01:38:00Z`` 的字符串。
    """
    target = moment or now_utc()
    return target.astimezone(UTC).strftime(ISO_FORMAT)


def parse_iso(value: str | None) -> datetime | None:
    """解析 ISO 8601 字符串为 datetime（兼容 ``Z`` 后缀与无时区后缀）。

    Args:
        value: 待解析字符串，可为 None 或空串。

    Returns:
        带 UTC tzinfo 的 datetime；输入为空时返回 None。
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def resolve_timezone(name: str | None) -> ZoneInfo | timezone:
    """解析时区名；无效或缺失时回退 UTC。

    Args:
        name: zoneinfo 时区名，如 ``Asia/Shanghai``。

    Returns:
        可传入 ``datetime.astimezone`` 的时区对象。
    """
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def to_local(moment: datetime, tz_name: str | None) -> datetime:
    """将 UTC 时间转换为指定时区的本地时间。"""
    return moment.astimezone(resolve_timezone(tz_name))


def format_local(moment: datetime, tz_name: str | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """按展示时区格式化时间（供 UI/通知模板使用）。"""
    return to_local(moment, tz_name).strftime(fmt)


def add_seconds(moment: datetime, seconds: int) -> datetime:
    """返回 ``moment + seconds``（用于会话过期与锁定到期计算）。"""
    return moment + timedelta(seconds=seconds)


def tz_env(tz_name: str | None) -> dict[str, str]:
    """构造注入 restic 子进程的 TZ 环境变量（方案 §4.1：容器系统时区仅作回退）。"""
    if not tz_name:
        return {}
    return {"TZ": tz_name}
