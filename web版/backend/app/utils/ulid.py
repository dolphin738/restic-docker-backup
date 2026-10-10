"""ULID 生成：任务 id（可排序，§4.5 jobs.id）。"""

from __future__ import annotations

from ulid import ULID


def new_ulid() -> str:
    """生成一个新的 ULID 字符串（26 字符，字典序即时间序）。

    Returns:
        大写 ULID 字符串，如 ``01J9Z8X7Y6W5V4U3T2S1R0Q9P8``。
    """
    return str(ULID())


def new_job_id() -> str:
    """生成任务 id（语义别名，便于调用处表意）。"""
    return new_ulid()
