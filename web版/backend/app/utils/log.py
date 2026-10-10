"""日志/审计详情截断（方案 §8.1：单任务日志按大小上限截断，审计 detail 同理）。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

DEFAULT_LIMIT = 2000
TRUNCATE_SUFFIX = "...<truncated>"


def truncate_text(text: str, limit: int = DEFAULT_LIMIT) -> str:
    """按字符数截断文本并追加截断标记。

    Args:
        text: 原始文本。
        limit: 允许保留的最大字符数。

    Returns:
        截断后的文本；未超限时原样返回。
    """
    if limit <= 0:
        return TRUNCATE_SUFFIX
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATE_SUFFIX


def truncate_detail(detail: Mapping[str, Any] | None, limit: int = DEFAULT_LIMIT) -> str | None:
    """将审计详情字典序列化为受限长度的 JSON 字符串。

    Args:
        detail: 结构化详情；None 时返回 None。
        limit: 序列化后允许的最大字符数。

    Returns:
        JSON 字符串（可能被截断）；输入为 None 时返回 None。
    """
    if detail is None:
        return None
    raw = json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)
    return truncate_text(raw, limit)
