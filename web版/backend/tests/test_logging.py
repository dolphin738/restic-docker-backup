"""日志格式护栏：容器日志必须带时间戳（真机首跑暴露的缺陷）。

背景：uvicorn 默认 log_config 的格式串是 `%(levelprefix)s %(message)s`，
访问日志同样没有时间字段；同时本项目原先把 ``logging.basicConfig`` 放在 lifespan 里，
而 uvicorn 以 `app.main:app` 字符串导入模块 —— 导入期 root 无 handler，
``create_app()`` 与 ``main()`` 的启动日志被 lastResort handler 按 WARNING 门槛丢弃。
表现：容器日志里只看得到几行 `INFO:     Uvicorn running on ...`，且全无时间。

本文件锁定三点：
① uvicorn 的 default/access 两个 formatter 都带时间戳；
② 配置后 uvicorn 三个 logger 与第三方 logger 输出都含时间字段；
③ root handler 在导入期就已装好（应用启动日志不会被吞）。
"""

from __future__ import annotations

import io
import logging
import logging.config

import pytest

from app.main import (
    LOG_DATEFMT,
    LOG_FORMAT,
    _configure_logging,
    _log_level_name,
    _uvicorn_log_config,
)


@pytest.fixture
def restore_logging():
    """隔离全局 logging 状态，避免污染其它测试。"""
    root = logging.getLogger()
    uvicorn_logger = logging.getLogger("uvicorn")
    error = logging.getLogger("uvicorn.error")
    access = logging.getLogger("uvicorn.access")
    saved = {
        "root": (root.handlers[:], root.level),
        "uvicorn": (uvicorn_logger.handlers[:], uvicorn_logger.level, uvicorn_logger.propagate),
        "error": (error.handlers[:], error.level, error.propagate),
        "access": (access.handlers[:], access.level, access.propagate),
    }
    yield
    root.handlers[:], root.level = saved["root"]
    uvicorn_logger.handlers[:], uvicorn_logger.level, uvicorn_logger.propagate = saved["uvicorn"]
    error.handlers[:], error.level, error.propagate = saved["error"]
    access.handlers[:], access.level, access.propagate = saved["access"]


def test_format_strings_carry_timestamp() -> None:
    """格式串本身必须含 %(asctime)s 与显式 datefmt（带 %z 偏移）。"""
    assert "%(asctime)s" in LOG_FORMAT
    assert "%z" in LOG_DATEFMT, "缺少 %z 偏移，容器 TZ 非 UTC 时时间会被误读"


def test_uvicorn_log_config_has_timestamp_on_both_formatters() -> None:
    """default 与 access 两个 formatter 都要带时间戳——访问日志默认没有。"""
    config = _uvicorn_log_config()
    assert config["version"] == 1
    assert config["disable_existing_loggers"] is False, "True 会摘掉导入期装好的 root handler"
    formatters = config["formatters"]  # type: ignore[index]
    for name in ("default", "access"):
        assert "%(asctime)s" in formatters[name]["format"], f"{name} formatter 缺时间戳"
        assert formatters[name]["datefmt"] == LOG_DATEFMT


def test_uvicorn_loggers_emit_timestamp(restore_logging) -> None:  # type: ignore[no-untyped-def]
    """按该配置 dictConfig 后，uvicorn 自身的日志输出首字段即时间。"""
    logging.config.dictConfig(_uvicorn_log_config())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info("探针消息")
        line = stream.getvalue().strip()
        assert line, f"{name} 未输出任何日志"
        assert line[:4].isdigit() and line[10] == " ", f"{name} 输出缺少时间戳前缀：{line!r}"


def test_third_party_logger_inherits_timestamp(restore_logging) -> None:  # type: ignore[no-untyped-def]
    """第三方库日志经 root 传播，同样要带时间戳（不能只有 uvicorn 有）。"""
    _configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("some.vendor.lib").warning("第三方探针")

    line = stream.getvalue().strip()
    assert line[:4].isdigit(), f"第三方日志缺时间戳：{line!r}"


def test_root_handler_installed_at_import_time() -> None:
    """root handler 必须在导入期就装好，否则 create_app/main 的启动日志会被丢弃。

    该断言与「模块导入 → 立即 basicConfig」的实现绑定：导入 app.main 后 root 必有 handler。
    """
    assert logging.getLogger().handlers, "root 无 handler：启动日志会被 lastResort 静默丢弃"


def test_log_level_env_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOG_LEVEL 环境变量生效（默认 INFO）。"""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert _log_level_name() == "INFO"
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert _log_level_name() == "DEBUG"
