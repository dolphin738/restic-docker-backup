"""游标分页编码（§5.0 分页约定）：偏移游标的不透明编码（E18/E20 共用）。

编码方式与 E18 快照文件树的既有实现一致（base64 urlsafe 的数字偏移）——两条
端点共用同一实现，避免同一游标串在两处语义漂移（A 系列单一实现点纪律）。
"""

from __future__ import annotations

import base64


def encode_cursor(offset: int) -> str:
    """把下页起始下标编码为不透明游标。

    Args:
        offset: 下页起始下标。

    Returns:
        URL 安全的 base64 字符串。
    """
    return base64.urlsafe_b64encode(str(offset).encode("utf-8")).decode("utf-8")


def decode_cursor(cursor: str | None, max_offset: int) -> int:
    """把不透明游标换算为起始下标。

    Args:
        cursor: 客户端回传的游标；空值表示首页。
        max_offset: 下标上限（防止游标被篡改为超大值导致空转读取）。

    Returns:
        起始下标（不可解析时回落 0，越界时钳到 ``max_offset``）。
    """
    if not cursor:
        return 0
    try:
        decoded = int(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
    except Exception:  # noqa: BLE001 - 游标损坏直接从头返回，不报错
        return 0
    return max(0, min(decoded, max_offset))
