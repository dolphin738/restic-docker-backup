"""API 集成：E39 时区统一——config.timezone 为单一事实源的运行期传播。

覆盖：
- put_config 写 ``timezone`` → restic 子进程 TZ 即时切换（``app.state.restic.tz``）；
- 坏时区名 422 前置拒绝，且**不**改动已生效的 restic TZ（不产生半生效状态）；
- 空串 = 清除配置回落 settings.tz（与调度 / 系统时间接口同一条回退链）。

背景（真机取证 2026-10-25）：用户把「系统设置-时区」改成 Asia/Seoul 后，restic
子进程仍按 settings.tz 渲染文本输出——旧实现 build_env 直读 settings.tz
（compose 级，运行期不可变），UI 时区对 restic 侧完全不生效。
"""

from __future__ import annotations

from conftest import ORIGIN, finish_bootstrap


async def test_put_config_timezone_refreshes_restic_tz(app_client):
    """保存时区后 restic 子进程 TZ 即时切换（无需重启）。"""
    ns = app_client
    await finish_bootstrap(ns)
    restic = ns.app.state.restic
    settings = ns.app.state.settings
    assert restic.tz == settings.tz  # 构造期回落链第一跳

    resp = await ns.client.put(
        "/api/config", json={"patch": {"timezone": "Asia/Seoul"}}, headers=ORIGIN
    )
    assert resp.status_code == 200, resp.text
    assert restic.tz == "Asia/Seoul"
    assert restic.build_env(repo_url="/repo")["TZ"] == "Asia/Seoul"


async def test_put_config_invalid_timezone_rejected_without_half_apply(app_client):
    """坏时区名 422 前置拒绝；已生效 TZ 保持不变（不半生效）。"""
    ns = app_client
    await finish_bootstrap(ns)
    restic = ns.app.state.restic
    await ns.client.put(
        "/api/config", json={"patch": {"timezone": "Asia/Seoul"}}, headers=ORIGIN
    )
    assert restic.tz == "Asia/Seoul"

    resp = await ns.client.put(
        "/api/config", json={"patch": {"timezone": "Not/A_Zone"}}, headers=ORIGIN
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert restic.tz == "Asia/Seoul"  # 拒绝不影响已生效值


async def test_put_config_null_timezone_falls_back_to_settings(app_client):
    """null = 清除配置 → 回落 settings.tz（与调度/系统时间接口同回退链）。

    注：清除语义是 JSON null（E07 口径），空串会被 is_valid_timezone 前置拒绝。
    """
    ns = app_client
    await finish_bootstrap(ns)
    restic = ns.app.state.restic
    settings = ns.app.state.settings

    await ns.client.put(
        "/api/config", json={"patch": {"timezone": "Asia/Seoul"}}, headers=ORIGIN
    )
    assert restic.tz == "Asia/Seoul"

    resp = await ns.client.put("/api/config", json={"patch": {"timezone": None}}, headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    assert restic.tz == settings.tz


async def test_put_config_empty_string_timezone_rejected(app_client):
    """空串不是合法时区名 → 422 前置拒绝（清除请用 null，E07 口径）。"""
    ns = app_client
    await finish_bootstrap(ns)
    restic = ns.app.state.restic

    resp = await ns.client.put("/api/config", json={"patch": {"timezone": ""}}, headers=ORIGIN)
    assert resp.status_code == 422
    assert restic.tz == ns.app.state.settings.tz
