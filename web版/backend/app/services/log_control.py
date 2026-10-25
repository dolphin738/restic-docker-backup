"""日志级别运行期热切换（E07 ``log.level`` 消费点）。

设计约束（test_logging.py 护栏锁定）：日志配置必须在**模块导入期**装好
（:func:`app.main._configure_logging`），早于 ``create_app()``——否则 uvicorn 以
``app.main:app`` 导入时 root 还没有 handler，启动日志会被 lastResort 按 WARNING
门槛静默丢弃。导入期 DB 未连接，拿不到 ``config.timezone`` 之外的任何 config 键，
故初始级别只能取 env/常量；本模块承担**启动校正**与**运行期热切换**两个后置时机：

- lifespan（env→config 同步之后）：读 ``log.level`` 应用一次；
- put_config 命中该键：立即应用。

作用范围：root logger + uvicorn 三件套（``uvicorn`` / ``uvicorn.error`` /
``uvicorn.access``）——uvicorn 的 handler 由 dictConfig 单独装配，只改 root
对它的访问日志不生效。
"""

from __future__ import annotations

import logging

from app.constants import LOG_LEVELS

_UVICORN_LOGGERS: tuple[str, ...] = ("uvicorn", "uvicorn.error", "uvicorn.access")


def resolve_level(name: str | None) -> int:
    """级别名 → logging 级别常量；空/非法回落 INFO（与 import 期兜底同口径）。"""
    candidate = (name or "").strip().upper()
    if candidate in LOG_LEVELS:
        return getattr(logging, candidate)
    return logging.INFO


def apply_log_level(name: str | None) -> str:
    """热切换日志级别，返回实际生效的级别名。

    Args:
        name: 级别名（大小写不敏感）；空/非法回落 INFO。

    Returns:
        实际生效的级别名（供调用方记日志/审计核对）。
    """
    level = resolve_level(name)
    logging.getLogger().setLevel(level)
    for logger_name in _UVICORN_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)
    return logging.getLevelName(level)
