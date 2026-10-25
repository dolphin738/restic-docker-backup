"""API 集成：系统设置域（compose → UI 迁移第一批）——log.level / db.busy_timeout_ms。

覆盖：
- 保存即热生效：日志级别切换 root logger（uvicorn 三件套同）；锁等待对当前
  连接重放 PRAGMA busy_timeout（新连接取同一份值）；
- 值域前置 422：级别白名单外 / 非正整数 / 越出 100~600000 区间；
- env 覆盖锁定：compose 设了（source='env'）→ 界面写 409 ENV_OVERRIDDEN；
- null 清除 = 回落（级别回 INFO、锁等待回落 settings/常量）。

背景：这两个值原先只能改 docker-compose.yml 并重建容器；迁入 E07 config 后
UI 可配、env 仍可覆盖（ENV_KEY_MAP 登记 → sync_env_overrides 标记 env 来源）。
"""

from __future__ import annotations

import logging

from conftest import ORIGIN, finish_bootstrap

from app.constants import (
    DB_BUSY_TIMEOUT_MS_KEY,
    LOG_LEVEL_KEY,
)
from app.schemas.enums import ConfigSource


async def test_put_config_log_level_hot_applies(app_client):
    """保存 log.level 后 root logger 级别立即切换（无重启）。"""
    ns = app_client
    await finish_bootstrap(ns)

    resp = await ns.client.put(
        "/api/config", json={"patch": {LOG_LEVEL_KEY: "DEBUG"}}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("uvicorn.access").level == logging.DEBUG

    # 回落默认档同样热切换（小写也接受，与 compose env 的大小写习惯兼容）
    resp = await ns.client.put(
        "/api/config", json={"patch": {LOG_LEVEL_KEY: "warning"}}, headers=ORIGIN
    )
    assert resp.status_code == 200
    assert logging.getLogger().level == logging.WARNING


async def test_put_config_invalid_log_level_rejected(app_client):
    """白名单外级别 → 422（不落库、不半生效）。"""
    ns = app_client
    await finish_bootstrap(ns)

    for bad in ("TRACE", "verbose", ""):
        resp = await ns.client.put(
            "/api/config", json={"patch": {LOG_LEVEL_KEY: bad}}, headers=ORIGIN
        )
        assert resp.status_code == 422, f"{bad!r} 应被拒绝"
        assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_put_config_busy_timeout_hot_applies(app_client):
    """保存 db.busy_timeout_ms 后当前连接 PRAGMA 立即生效。"""
    ns = app_client
    await finish_bootstrap(ns)
    db = ns.app.state.db

    resp = await ns.client.put(
        "/api/config", json={"patch": {DB_BUSY_TIMEOUT_MS_KEY: "15000"}}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    row = await db.fetch_one("PRAGMA busy_timeout")
    assert int(row[0]) == 15000
    # 新连接（重连）取同一份值：property 已被 apply 覆盖
    assert db.busy_timeout_ms == 15000


async def test_put_config_invalid_busy_timeout_rejected(app_client):
    """非正整数 / 越出 100~600000 → 422。"""
    ns = app_client
    await finish_bootstrap(ns)

    for bad in ("0", "99", "600001", "abc", "-1", "1.5"):
        resp = await ns.client.put(
            "/api/config", json={"patch": {DB_BUSY_TIMEOUT_MS_KEY: bad}}, headers=ORIGIN
        )
        assert resp.status_code == 422, f"{bad!r} 应被拒绝"


async def test_env_override_locks_ui_write(app_client):
    """compose 设了（source='env'）→ 界面写 409 ENV_OVERRIDDEN，不产生部分写入。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.config_repo.set(LOG_LEVEL_KEY, "WARNING", source=ConfigSource.ENV, force=True)

    resp = await ns.client.put(
        "/api/config", json={"patch": {LOG_LEVEL_KEY: "DEBUG"}}, headers=ORIGIN
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "ENV_OVERRIDDEN"
    # env 值未被界面改动覆盖
    item = await ns.app.state.config_repo.get(LOG_LEVEL_KEY)
    assert item is not None and item.value == "WARNING"


async def test_null_clears_and_falls_back(app_client):
    """null = 清除配置：日志级别回落 INFO（apply_log_level 兜底档）。"""
    ns = app_client
    await finish_bootstrap(ns)

    await ns.client.put("/api/config", json={"patch": {LOG_LEVEL_KEY: "DEBUG"}}, headers=ORIGIN)
    assert logging.getLogger().level == logging.DEBUG

    resp = await ns.client.put("/api/config", json={"patch": {LOG_LEVEL_KEY: None}}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    assert logging.getLogger().level == logging.INFO
