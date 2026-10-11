"""目录扫描与分页游标：E05（备份源浏览）与 E33（容器文件系统浏览）共用。

从 ``routers/sources.py`` 抽出为公共服务（D8 单一事实来源）：两端点共用同一套
「单层扫描 + 排序 + 不透明游标分页」语义，避免实现漂移。
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from app.errors import ForbiddenError, NotFoundError

# 单层扫描是纯阻塞 IO（os.listdir + stat），统一丢线程池
_scan_lock_note = "调用方应经 asyncio.to_thread 调用 scan_dir"


async def scan_dir_async(path: str) -> list[tuple[str, str]]:
    """:func:`scan_dir` 的线程池包装。"""
    return await asyncio.to_thread(scan_dir, path)


def scan_dir(path: str) -> list[tuple[str, str]]:
    """同步扫描单层目录，返回按名称排序的 ``(name, type)`` 列表。

    Args:
        path: 容器内绝对路径（调用方负责白名单/合法性校验）。

    Returns:
        排序后的条目列表，type 取 ``dir`` / ``file`` / ``other``。

    Raises:
        NotFoundError: 路径不存在。
        ValidationError: 路径不是目录。
        ForbiddenError: 无读取权限（非 root 档常见，R-012）。
    """
    from app.errors import ValidationError

    target = Path(path)
    if not target.exists():
        raise NotFoundError(f"路径不存在：{path}")
    if not target.is_dir():
        raise ValidationError(f"路径不是目录：{path}")
    try:
        names = sorted(os.listdir(target))
    except PermissionError as exc:
        raise ForbiddenError(f"无权限读取目录：{path}（非 root 档常见，如需全量备份请切 root 档）") from exc

    entries: list[tuple[str, str]] = []
    for name in names:
        child = target / name
        if child.is_symlink():
            kind = "other"
        elif child.is_dir():
            kind = "dir"
        elif child.is_file():
            kind = "file"
        else:
            kind = "other"
        entries.append((name, kind))
    return entries


def cursor_index(entries: list[tuple[str, str]], cursor: str | None) -> int:
    """把游标换算为起始下标；游标不可解析时从头开始。"""
    if not cursor:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode("utf-8")
    except Exception:  # noqa: BLE001 - 游标损坏直接从头返回，不报错
        return 0
    for index, (name, _) in enumerate(entries):
        if name == decoded:
            return index + 1
    return 0


def encode_cursor(name: str) -> str:
    """把末条目名编码为不透明游标。"""
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("utf-8")
